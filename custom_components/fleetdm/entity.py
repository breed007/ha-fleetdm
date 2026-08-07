"""Base entities and dynamic-entity plumbing for the Fleet integration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import FleetPolicy
from .const import DOMAIN, MANUFACTURER
from .coordinator import FleetSummaryCoordinator


def policy_unique_id(entry_id: str, policy_id: int, key: str) -> str:
    """Build the unique ID for a per-policy entity.

    Keyed on the Fleet policy ID, never the name, so renaming a policy in Fleet
    updates the display name without orphaning the entity.
    """
    return f"{entry_id}_policy_{policy_id}_{key}"


def fleet_unique_id(entry_id: str, key: str) -> str:
    """Build the unique ID for a fleet-level entity."""
    return f"{entry_id}_{key}"


class FleetEntity(CoordinatorEntity[FleetSummaryCoordinator]):
    """Base entity attached to the Fleet server hub device."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: FleetSummaryCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialise the entity and bind it to the hub device."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Fleet",
            manufacturer=MANUFACTURER,
            model="Fleet server",
            sw_version=coordinator.version.get("version"),
            configuration_url=coordinator.client.base_url,
        )


class FleetPolicyEntity(FleetEntity):
    """Base entity for a single Fleet global policy."""

    def __init__(
        self,
        coordinator: FleetSummaryCoordinator,
        entry: ConfigEntry,
        policy_id: int,
        key: str,
    ) -> None:
        """Initialise the policy entity."""
        super().__init__(coordinator, entry)
        self._policy_id = policy_id
        self._attr_unique_id = policy_unique_id(entry.entry_id, policy_id, key)

    @property
    def policy(self) -> FleetPolicy | None:
        """The policy this entity tracks, or None if it vanished from Fleet."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.policies_by_id.get(self._policy_id)

    @property
    def available(self) -> bool:
        """Only available while the policy still exists in Fleet."""
        return super().available and self.policy is not None

    @property
    def name(self) -> str | None:
        """Follow the policy's current name in Fleet.

        Resolved on every read rather than cached at construction, so a policy
        renamed in Fleet is renamed in Home Assistant on the next poll.
        """
        if (policy := self.policy) is not None:
            return policy.name
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose the policy's counts and metadata."""
        if (policy := self.policy) is None:
            return None
        return {
            "policy_id": policy.id,
            "passing_host_count": policy.passing_host_count,
            "failing_host_count": policy.failing_host_count,
            "critical": policy.critical,
            "platform": policy.platform,
            "description": policy.description,
            "resolution": policy.resolution,
            # Fleet recomputes policy host counts on its own schedule; this is
            # the honest "as of" timestamp for the numbers above.
            "host_count_updated_at": policy.host_count_updated_at,
        }


@callback
def async_setup_dynamic_policy_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: FleetSummaryCoordinator,
    async_add_entities: AddEntitiesCallback,
    platform: str,
    key: str,
    factory: Callable[[int], Any],
) -> None:
    """Create and remove per-policy entities as policies come and go in Fleet.

    Adds entities for policies seen for the first time and purges registry
    entries for policies that have been deleted, so the entity list tracks Fleet
    without requiring a reload.
    """
    known: set[int] = set()

    @callback
    def _sync_entities() -> None:
        if coordinator.data is None:
            return
        current = set(coordinator.data.policies_by_id)

        if added := current - known:
            async_add_entities(factory(policy_id) for policy_id in sorted(added))
            known.update(added)

        if removed := known - current:
            registry = er.async_get(hass)
            for policy_id in removed:
                unique_id = policy_unique_id(entry.entry_id, policy_id, key)
                if entity_id := registry.async_get_entity_id(
                    platform, DOMAIN, unique_id
                ):
                    registry.async_remove(entity_id)
            known.difference_update(removed)

    entry.async_on_unload(coordinator.async_add_listener(_sync_entities))
    _sync_entities()
