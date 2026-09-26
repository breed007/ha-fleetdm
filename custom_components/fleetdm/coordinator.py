"""Data update coordinators for the Fleet integration.

Two coordinators, split by how expensive their endpoints are:

* **Summary** polls ``/host_summary`` and the policies route every minute or so.
  Both are cheap, and they power every fleet-level and per-policy entity.
* **Inventory** polls the full host list, the vulnerable software summary and
  the activity feed on a much slower cycle. The host list is the only genuinely
  expensive call the integration makes.

Both compute their events by diffing successive snapshots against a persisted
baseline, so an ongoing condition fires once rather than every poll, and a
restart neither replays nor loses transitions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, override

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    FEED_HOSTS,
    FEED_POLICIES,
    FleetActivity,
    FleetAuthError,
    FleetClient,
    FleetError,
    FleetHost,
    FleetHostSummary,
    FleetLabel,
    FleetOsVersions,
    FleetPolicy,
    FleetVulnerableSoftware,
)
from .const import (
    ACTIVITY_TYPES_HOST_ENROLLED,
    CONF_ACTIVITY_EVENTS,
    CONF_INVENTORY_INTERVAL,
    CONF_LABEL_SENSORS,
    CONF_MISSING_AFTER_HOURS,
    CONF_PER_HOST_ENTITIES,
    CONF_SUMMARY_INTERVAL,
    CONF_VULNERABILITY_SENSORS,
    DEFAULT_ACTIVITY_EVENTS,
    DEFAULT_INVENTORY_INTERVAL,
    DEFAULT_LABEL_SENSORS,
    DEFAULT_MISSING_AFTER_HOURS,
    DEFAULT_PER_HOST_ENTITIES,
    DEFAULT_SUMMARY_INTERVAL,
    DEFAULT_VULNERABILITY_SENSORS,
    DOMAIN,
    EVENT_ACTIVITY,
    EVENT_HOST_ENROLLED,
    EVENT_HOST_MISSING,
    EVENT_POLICY_FAILING,
    EVENT_POLICY_RECOVERED,
    EVENT_TYPE_ACTIVITY,
    EVENT_TYPE_HOST_ENROLLED,
    EVENT_TYPE_HOST_WENT_MISSING,
    EVENT_TYPE_POLICY_NEWLY_FAILING,
    EVENT_TYPE_POLICY_RECOVERED,
    PER_HOST_ENTITY_THRESHOLD,
    PER_HOST_OFF,
    PER_HOST_ON,
    STORAGE_KEY_INVENTORY_TEMPLATE,
    STORAGE_KEY_TEMPLATE,
    STORAGE_VERSION,
)
from .issues import async_sync_truncation_issue

_LOGGER = logging.getLogger(__name__)

# How many summary polls between re-reads of the Fleet version and license
# tier. At the default 60s interval this is roughly hourly: often enough that a
# server upgrade or license change is picked up the same day, rare enough to be
# invisible in request volume.
METADATA_REFRESH_EVERY = 60

# Bus event type fired for each `event` entity event type.
_BUS_EVENT_FOR_TYPE = {
    EVENT_TYPE_POLICY_NEWLY_FAILING: EVENT_POLICY_FAILING,
    EVENT_TYPE_POLICY_RECOVERED: EVENT_POLICY_RECOVERED,
    EVENT_TYPE_HOST_ENROLLED: EVENT_HOST_ENROLLED,
    EVENT_TYPE_HOST_WENT_MISSING: EVENT_HOST_MISSING,
    EVENT_TYPE_ACTIVITY: EVENT_ACTIVITY,
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
        """Initialize the coordinator."""
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
        self._polls_since_metadata = 0
        # False until a baseline has been established, either from storage or
        # from the first successful poll. See _compute_drift for why this
        # matters.
        self._has_baseline = False

    @override
    async def _async_setup(self) -> None:
        """One-time setup, run before the first refresh.

        Fetches server metadata and restores the persisted drift baseline.
        """
        await self._async_refresh_server_metadata()

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

    async def _async_refresh_server_metadata(self) -> None:
        """Read the Fleet version and license tier.

        Both change out from under us: a Fleet upgrade changes the version shown
        on the hub device, and a license change flips which policies the
        compliance sensor watches. Neither is worth a request every cycle, but
        fetching them once at setup meant they stayed wrong until a reload.
        """
        try:
            self.version = await self.client.async_get_version()
            # Inside the same guard: a 401 here means the token is bad whichever
            # call surfaced it, and must reach the reauth flow rather than being
            # retried forever as a transient failure.
            self.premium = await self.client.async_is_premium()
        except FleetError as err:
            raise _translated(err) from err

    @override
    async def _async_update_data(self) -> FleetData:
        """Fetch host counts and policies, then diff for compliance drift."""
        self._polls_since_metadata += 1
        if self._polls_since_metadata >= METADATA_REFRESH_EVERY:
            self._polls_since_metadata = 0
            await self._async_refresh_server_metadata()

        try:
            summary = await self.client.async_get_host_summary()
            policies = await self.client.async_get_global_policies()
        except FleetError as err:
            raise _translated(err) from err

        async_sync_truncation_issue(
            self.hass,
            self.config_entry,
            FEED_POLICIES,
            truncated=FEED_POLICIES in self.client.truncated,
            count=len(policies),
        )
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


@dataclass(slots=True)
class FleetInventoryData:
    """Everything the inventory coordinator produces in one cycle."""

    hosts: list[FleetHost] = field(default_factory=list)
    vulnerable: FleetVulnerableSoftware | None = None
    labels: list[FleetLabel] = field(default_factory=list)
    os_versions: FleetOsVersions | None = None
    events: list[FleetDriftEvent] = field(default_factory=list)

    @property
    def hosts_by_id(self) -> dict[int, FleetHost]:
        """Hosts keyed by their stable Fleet ID."""
        return {host.id: host for host in self.hosts}

    @property
    def labels_by_id(self) -> dict[int, FleetLabel]:
        """Labels keyed by their stable Fleet ID."""
        return {label.id: label for label in self.labels}


class FleetInventoryCoordinator(DataUpdateCoordinator[FleetInventoryData]):
    """Polls Fleet for the host list, vulnerable software and activity feed.

    Deliberately slower than the summary coordinator: the host list is the only
    genuinely expensive call the integration makes.

    Unlike the entities it feeds, this coordinator runs even when per-host
    entities are switched off, because the host-enrolled and host-missing events
    are a headline feature and both need the host list. Only the *entities* are
    gated by fleet size.
    """

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: FleetClient,
    ) -> None:
        """Initialize the coordinator."""
        interval = entry.options.get(
            CONF_INVENTORY_INTERVAL, DEFAULT_INVENTORY_INTERVAL
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} inventory",
            update_interval=timedelta(seconds=interval),
        )
        self.client = client
        self._store = Store[dict[str, Any]](
            hass,
            STORAGE_VERSION,
            STORAGE_KEY_INVENTORY_TEMPLATE.format(entry_id=entry.entry_id),
        )
        self._last_activity_id: int | None = None
        self._missing_host_ids: set[int] = set()
        self._has_baseline = False

    @property
    def missing_after(self) -> timedelta:
        """How long unseen before a host counts as missing."""
        hours = self.config_entry.options.get(
            CONF_MISSING_AFTER_HOURS, DEFAULT_MISSING_AFTER_HOURS
        )
        return timedelta(hours=float(hours))

    @override
    async def _async_setup(self) -> None:
        """Restore the persisted event watermarks before the first refresh."""
        stored = await self._store.async_load()
        if stored is not None:
            last = stored.get("last_activity_id")
            self._last_activity_id = int(last) if last is not None else None
            self._missing_host_ids = {
                int(host_id) for host_id in stored.get("missing_host_ids", [])
            }
            self._has_baseline = True

    @override
    async def _async_update_data(self) -> FleetInventoryData:
        """Fetch hosts, vulnerable software and new activities."""
        try:
            hosts = await self.client.async_get_hosts()
            activities = await self.client.async_get_activities(self._last_activity_id)
            vulnerable = None
            if self.config_entry.options.get(
                CONF_VULNERABILITY_SENSORS, DEFAULT_VULNERABILITY_SENSORS
            ):
                vulnerable = await self.client.async_get_vulnerable_software()
            labels: list[FleetLabel] = []
            if self.config_entry.options.get(CONF_LABEL_SENSORS, DEFAULT_LABEL_SENSORS):
                labels = await self.client.async_get_labels()
            # Aggregated server-side, so this is one request whatever the fleet
            # size — cheap enough not to be worth its own option.
            os_versions = await self.client.async_get_os_versions()
        except FleetError as err:
            raise _translated(err) from err

        async_sync_truncation_issue(
            self.hass,
            self.config_entry,
            FEED_HOSTS,
            truncated=FEED_HOSTS in self.client.truncated,
            count=len(hosts),
        )
        events = await self._compute_events(hosts, activities)
        return FleetInventoryData(
            hosts=hosts,
            vulnerable=vulnerable,
            labels=labels,
            os_versions=os_versions,
            events=events,
        )

    async def _compute_events(
        self, hosts: list[FleetHost], activities: list[FleetActivity]
    ) -> list[FleetDriftEvent]:
        """Derive host events, using the same no-storm rules as policy drift.

        The first cycle on a new config entry establishes watermarks silently.
        Without that, adding the integration would fire an enrollment event for
        every host in the recent activity feed and a missing event for every
        host that is already stale.
        """
        newest_activity = max((a.id for a in activities), default=None)
        now = dt_util.utcnow()
        cutoff = now - self.missing_after
        currently_missing = {host.id for host in hosts if host.is_missing_at(cutoff)}

        if not self._has_baseline:
            self._last_activity_id = newest_activity
            self._missing_host_ids = currently_missing
            self._has_baseline = True
            await self._async_save()
            _LOGGER.debug(
                "Seeded inventory baseline: %d missing hosts, activity watermark "
                "%s; no events fired",
                len(currently_missing),
                newest_activity,
            )
            return []

        events: list[FleetDriftEvent] = []
        by_id = {host.id: host for host in hosts}

        emit_activities = self.config_entry.options.get(
            CONF_ACTIVITY_EVENTS, DEFAULT_ACTIVITY_EVENTS
        )
        for activity in sorted(activities, key=lambda a: a.id):
            if activity.type in ACTIVITY_TYPES_HOST_ENROLLED:
                events.append(
                    FleetDriftEvent(
                        event_type=EVENT_TYPE_HOST_ENROLLED,
                        data=_enrollment_event_payload(activity),
                    )
                )
            elif emit_activities:
                events.append(
                    FleetDriftEvent(
                        event_type=EVENT_TYPE_ACTIVITY,
                        data=_activity_event_payload(activity),
                    )
                )

        # Only hosts still present in Fleet: one deleted while missing is gone,
        # not newly missing.
        newly_missing = (currently_missing - self._missing_host_ids) & set(by_id)
        events += [
            FleetDriftEvent(
                event_type=EVENT_TYPE_HOST_WENT_MISSING,
                data=_missing_event_payload(by_id[host_id], now),
            )
            for host_id in sorted(newly_missing)
        ]

        changed = (
            newest_activity is not None and newest_activity != self._last_activity_id
        ) or currently_missing != self._missing_host_ids
        if newest_activity is not None:
            self._last_activity_id = newest_activity
        self._missing_host_ids = currently_missing
        if changed:
            await self._async_save()

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

    async def _async_save(self) -> None:
        """Persist the event watermarks."""
        await self._store.async_save(
            {
                "last_activity_id": self._last_activity_id,
                "missing_host_ids": sorted(self._missing_host_ids),
            }
        )


def _translated(err: FleetError) -> ConfigEntryAuthFailed | UpdateFailed:
    """Convert a Fleet API error into a translated Home Assistant error.

    A rejected token becomes ConfigEntryAuthFailed so it reaches the reauth
    flow; anything else is an ordinary failed update, retried next cycle.
    """
    kwargs: dict[str, Any] = {
        "translation_domain": DOMAIN,
        "translation_key": err.translation_key,
        "translation_placeholders": err.translation_placeholders,
    }
    if isinstance(err, FleetAuthError):
        return ConfigEntryAuthFailed(**kwargs)
    return UpdateFailed(**kwargs)


def per_host_entities_enabled(entry: ConfigEntry, host_count: int) -> bool:
    """Whether to create per-host entities for this entry.

    Three states. ``on`` and ``off`` are explicit and always win. ``auto`` — the
    default — decides on fleet size: small fleets get them, because that is what
    most people want and the entity count is unremarkable, while a large fleet
    gets none until asked, so installing this on a 500-host fleet cannot create
    thousands of entities by surprise.

    In ``auto`` the decision is re-evaluated on every refresh, so a fleet that
    grows past the threshold stops having per-host entities. That is deliberate:
    crossing the threshold is exactly the moment the choice should become
    yours rather than ours. Set the option explicitly to ``on`` to keep them.
    """
    value = entry.options.get(CONF_PER_HOST_ENTITIES, DEFAULT_PER_HOST_ENTITIES)

    # Entries created before 0.4 stored a plain boolean here.
    if isinstance(value, bool):
        return value

    if value == PER_HOST_ON:
        return True
    if value == PER_HOST_OFF:
        return False
    return host_count <= PER_HOST_ENTITY_THRESHOLD


def _enrollment_event_payload(activity: FleetActivity) -> dict[str, Any]:
    """Build the automation-facing payload for a host enrollment."""
    details = activity.details
    return {
        "host_id": details.get("host_id"),
        "host_name": details.get("host_display_name"),
        "host_serial": details.get("host_serial"),
        "activity_id": activity.id,
        "enrolled_at": activity.created_at.isoformat() if activity.created_at else None,
    }


def _activity_event_payload(activity: FleetActivity) -> dict[str, Any]:
    """Build the payload for a generic Fleet audit activity.

    The Fleet activity type travels in the payload rather than becoming its own
    Home Assistant event type. Fleet's audit taxonomy grows between releases,
    and enumerating it here would mean new activity types silently going
    nowhere until this integration caught up.
    """
    return {
        "activity_id": activity.id,
        "activity_type": activity.type,
        "created_at": activity.created_at.isoformat() if activity.created_at else None,
        "details": activity.details,
    }


def _missing_event_payload(host: FleetHost, now: datetime) -> dict[str, Any]:
    """Build the automation-facing payload for a host going missing."""
    unseen_hours: float | None = None
    if host.seen_time is not None:
        unseen_hours = round((now - host.seen_time).total_seconds() / 3600, 1)
    return {
        "host_id": host.id,
        "host_name": host.display_name,
        "platform": host.platform,
        "os_version": host.os_version,
        "status": host.status,
        "last_seen": host.seen_time.isoformat() if host.seen_time else None,
        "unseen_hours": unseen_hours,
    }


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
