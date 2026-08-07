"""Data update coordinators for the Fleet integration.

Phase 1 ships a single "summary" coordinator: it polls the two cheap endpoints
(``/host_summary`` and ``/global/policies``) that power every fleet-level and
per-policy entity. The slower "inventory" coordinator that backs per-host
entities arrives in Phase 2 and will live alongside this one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    FleetAuthError,
    FleetClient,
    FleetError,
    FleetHostSummary,
    FleetPolicy,
)
from .const import (
    CONF_SUMMARY_INTERVAL,
    DEFAULT_SUMMARY_INTERVAL,
    DOMAIN,
    EVENT_POLICY_FAILING,
    EVENT_POLICY_RECOVERED,
    EVENT_TYPE_POLICY_NEWLY_FAILING,
    EVENT_TYPE_POLICY_RECOVERED,
    STORAGE_KEY_TEMPLATE,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

# Bus event type fired for each `event` entity event type.
_BUS_EVENT_FOR_TYPE = {
    EVENT_TYPE_POLICY_NEWLY_FAILING: EVENT_POLICY_FAILING,
    EVENT_TYPE_POLICY_RECOVERED: EVENT_POLICY_RECOVERED,
}


@dataclass(frozen=True, slots=True)
class FleetDriftEvent:
    """A single compliance transition detected between two polls."""

    event_type: str
    data: dict[str, Any]


@dataclass(slots=True)
class FleetData:
    """Everything the summary coordinator produces in one cycle."""

    summary: FleetHostSummary
    policies: list[FleetPolicy]
    premium: bool
    version: dict[str, Any] = field(default_factory=dict)
    events: list[FleetDriftEvent] = field(default_factory=list)

    @property
    def policies_by_id(self) -> dict[int, FleetPolicy]:
        """Policies keyed by their stable Fleet ID."""
        return {policy.id: policy for policy in self.policies}

    @property
    def failing_policies(self) -> list[FleetPolicy]:
        """Policies with at least one failing host."""
        return [policy for policy in self.policies if policy.is_failing]

    @property
    def compliance_problem(self) -> bool:
        """Whether the fleet-level compliance sensor should report a problem.

        On Premium this tracks only policies explicitly marked critical, which
        is the signal most operators actually want to be paged on. ``critical``
        is a Premium-only field, so on Free tier we fall back to "any policy
        failing" rather than reporting permanently healthy.
        """
        failing = self.failing_policies
        if self.premium:
            return any(policy.critical for policy in failing)
        return bool(failing)


class FleetSummaryCoordinator(DataUpdateCoordinator[FleetData]):
    """Polls Fleet for host counts and global policy compliance."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: FleetClient,
    ) -> None:
        """Initialise the coordinator."""
        interval = entry.options.get(CONF_SUMMARY_INTERVAL, DEFAULT_SUMMARY_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} summary",
            update_interval=timedelta(seconds=interval),
        )
        self.client = client
        self.premium = False
        self.version: dict[str, Any] = {}
        self._store = Store[dict[str, Any]](
            hass,
            STORAGE_VERSION,
            STORAGE_KEY_TEMPLATE.format(entry_id=entry.entry_id),
        )
        self._failing_policy_ids: set[int] = set()
        # False until a baseline has been established, either from storage or
        # from the first successful poll. See _compute_drift for why this
        # matters.
        self._has_baseline = False

    async def _async_setup(self) -> None:
        """One-time setup, run before the first refresh.

        Fetches server metadata that does not change poll-to-poll, and restores
        the persisted drift baseline.
        """
        try:
            self.version = await self.client.async_get_version()
        except FleetAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except FleetError as err:
            raise UpdateFailed(str(err)) from err

        self.premium = await self.client.async_is_premium()

        stored = await self._store.async_load()
        if stored is not None:
            self._failing_policy_ids = {
                int(policy_id) for policy_id in stored.get("failing_policy_ids", [])
            }
            self._has_baseline = True
            _LOGGER.debug(
                "Restored drift baseline with %d failing policies",
                len(self._failing_policy_ids),
            )

    async def _async_update_data(self) -> FleetData:
        """Fetch host counts and policies, then diff for compliance drift."""
        try:
            summary = await self.client.async_get_host_summary()
            policies = await self.client.async_get_global_policies()
        except FleetAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except FleetError as err:
            raise UpdateFailed(str(err)) from err

        events = await self._compute_drift(policies)

        return FleetData(
            summary=summary,
            policies=policies,
            premium=self.premium,
            version=self.version,
            events=events,
        )

    async def _compute_drift(
        self, policies: list[FleetPolicy]
    ) -> list[FleetDriftEvent]:
        """Diff the current failing set against the last one and emit events.

        De-duplication is structural rather than time-based: an event fires only
        when a policy *crosses* between passing and failing, so a policy that
        stays failing for a week produces exactly one event.

        On a brand new config entry there is no stored baseline, so the first
        poll seeds the baseline silently. Without this, adding the integration
        to a fleet that already has failing policies would fire a notification
        for every one of them at setup. Once a baseline exists it is persisted,
        so transitions that happen while Home Assistant is down are still
        detected on the next start rather than being lost or re-fired.
        """
        current_failing = {policy.id for policy in policies if policy.is_failing}

        if not self._has_baseline:
            self._failing_policy_ids = current_failing
            self._has_baseline = True
            await self._async_save_drift_state()
            _LOGGER.debug(
                "Seeded drift baseline with %d failing policies; no events fired",
                len(current_failing),
            )
            return []

        if current_failing == self._failing_policy_ids:
            return []

        by_id = {policy.id: policy for policy in policies}
        known_ids = set(by_id)

        newly_failing = current_failing - self._failing_policy_ids
        # Intersecting with known_ids means a policy deleted from Fleet while
        # failing is dropped from the baseline silently instead of reporting a
        # phantom recovery.
        recovered = (self._failing_policy_ids & known_ids) - current_failing

        events = [
            FleetDriftEvent(
                event_type=EVENT_TYPE_POLICY_NEWLY_FAILING,
                data=_policy_event_payload(by_id[policy_id]),
            )
            for policy_id in sorted(newly_failing)
        ]
        events += [
            FleetDriftEvent(
                event_type=EVENT_TYPE_POLICY_RECOVERED,
                data=_policy_event_payload(by_id[policy_id]),
            )
            for policy_id in sorted(recovered)
        ]

        self._failing_policy_ids = current_failing
        await self._async_save_drift_state()

        for event in events:
            self._fire_bus_event(event)

        return events

    def _fire_bus_event(self, event: FleetDriftEvent) -> None:
        """Fire the bus event that mirrors an `event` entity event."""
        bus_event = _BUS_EVENT_FOR_TYPE.get(event.event_type)
        if bus_event is None:
            return
        self.hass.bus.async_fire(
            bus_event,
            {"entry_id": self.config_entry.entry_id, **event.data},
        )
        _LOGGER.debug(
            "Fired %s for policy %s", bus_event, event.data.get("policy_name")
        )

    async def _async_save_drift_state(self) -> None:
        """Persist the failing set.

        Written immediately rather than via a delayed save: the set changes only
        on an actual transition, so writes are rare, and a crash between the
        transition and a delayed flush would re-fire the event on restart.
        """
        await self._store.async_save(
            {"failing_policy_ids": sorted(self._failing_policy_ids)}
        )


def _policy_event_payload(policy: FleetPolicy) -> dict[str, Any]:
    """Build the automation-facing payload for a policy transition."""
    return {
        "policy_id": policy.id,
        "policy_name": policy.name,
        "description": policy.description,
        "resolution": policy.resolution,
        "platform": policy.platform,
        "critical": policy.critical,
        "failing_host_count": policy.failing_host_count,
        "passing_host_count": policy.passing_host_count,
        "host_count_updated_at": policy.host_count_updated_at,
    }
