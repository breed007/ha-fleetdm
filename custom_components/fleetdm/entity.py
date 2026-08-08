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

from .api import FleetHost, FleetLabel, FleetPolicy
from .const import DOMAIN, MANUFACTURER
from .coordinator import FleetInventoryCoordinator, FleetSummaryCoordinator


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


def label_unique_id(entry_id: str, label_id: int, key: str) -> str:
    """Build the unique ID for a per-label entity."""
    return f"{entry_id}_label_{label_id}_{key}"


class FleetLabelEntity(CoordinatorEntity[FleetInventoryCoordinator]):
    """Base entity for a single Fleet label, on the hub device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FleetInventoryCoordinator,
        entry: ConfigEntry,
        label_id: int,
        key: str,
    ) -> None:
        """Initialise the label entity."""
        super().__init__(coordinator)
        self._entry = entry
        self._label_id = label_id
        self._attr_unique_id = label_unique_id(entry.entry_id, label_id, key)
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    @property
    def label(self) -> FleetLabel | None:
        """The label this entity tracks, or None if it was deleted."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.labels_by_id.get(self._label_id)

    @property
    def available(self) -> bool:
        """Only available while the label still exists in Fleet."""
        return super().available and self.label is not None

    @property
    def name(self) -> str | None:
        """Follow the label's current name in Fleet.

        Resolved on every read, so renaming a label in Fleet renames the entity
        without orphaning its history.
        """
        if (label := self.label) is not None:
            return label.name
        return None


def host_unique_id(entry_id: str, host_id: int, key: str) -> str:
    """Build the unique ID for a per-host entity.

    Keyed on the Fleet host ID, so renaming or re-imaging a host keeps its
    history as long as Fleet keeps the same record.
    """
    return f"{entry_id}_host_{host_id}_{key}"


class FleetInventoryEntity(CoordinatorEntity[FleetInventoryCoordinator]):
    """Base entity driven by the inventory coordinator, on the hub device."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: FleetInventoryCoordinator, entry: ConfigEntry
    ) -> None:
        """Initialise the entity and bind it to the hub device."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})


class FleetHostEntity(CoordinatorEntity[FleetInventoryCoordinator]):
    """Base entity for a single enrolled host.

    Each host becomes its own Home Assistant device hanging off the Fleet hub,
    so its entities group together and the device page links straight to that
    host in Fleet.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FleetInventoryCoordinator,
        entry: ConfigEntry,
        host_id: int,
        key: str,
    ) -> None:
        """Initialise the host entity."""
        super().__init__(coordinator)
        self._entry = entry
        self._host_id = host_id
        self._attr_unique_id = host_unique_id(entry.entry_id, host_id, key)

        host = self.host
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_host_{host_id}")},
            name=host.display_name if host else f"Host {host_id}",
            manufacturer=MANUFACTURER,
            model=_host_model(host),
            sw_version=host.os_version if host else None,
            via_device=(DOMAIN, entry.entry_id),
            configuration_url=coordinator.client.host_page_url(host_id),
        )

    @property
    def host(self) -> FleetHost | None:
        """The host this entity tracks, or None if it left Fleet."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.hosts_by_id.get(self._host_id)

    @property
    def available(self) -> bool:
        """Only available while the host still exists in Fleet.

        Note this is about the *record* existing, not the host being online.
        A powered-off laptop is still available here; its connectivity sensor
        is what reports it as offline.
        """
        return super().available and self.host is not None


def _host_model(host: FleetHost | None) -> str | None:
    """Describe the host's hardware and platform for the device registry."""
    if host is None:
        return None
    parts = [part for part in (host.hardware_model, host.platform) if part]
    return " · ".join(parts) or None


@callback
def async_setup_dynamic_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: Any,
    async_add_entities: AddEntitiesCallback,
    platform: str,
    current_ids: Callable[[Any], set[int]],
    unique_id_for: Callable[[int], str],
    factory: Callable[[int], Any],
) -> None:
    """Track a set of Fleet objects, creating and removing entities to match.

    Policies, hosts and labels all come and go in Fleet, and all want the same
    handling: create entities for anything newly seen, and purge registry
    entries for anything that has disappeared, without needing a reload. This is
    that shared behaviour, parameterised by how to read the current IDs out of
    the coordinator and how to build a unique ID from one.
    """
    known: set[int] = set()

    @callback
    def _sync_entities() -> None:
        if coordinator.data is None:
            return
        current = current_ids(coordinator.data)

        if added := current - known:
            async_add_entities(factory(item_id) for item_id in sorted(added))
            known.update(added)

        if removed := known - current:
            registry = er.async_get(hass)
            for item_id in removed:
                if entity_id := registry.async_get_entity_id(
                    platform, DOMAIN, unique_id_for(item_id)
                ):
                    registry.async_remove(entity_id)
            known.difference_update(removed)

    entry.async_on_unload(coordinator.async_add_listener(_sync_entities))
    _sync_entities()


@callback
def async_setup_dynamic_host_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: FleetInventoryCoordinator,
    async_add_entities: AddEntitiesCallback,
    platform: str,
    key: str,
    factory: Callable[[int], Any],
) -> None:
    """Track hosts as they enrol in and leave Fleet."""
    async_setup_dynamic_entities(
        hass,
        entry,
        coordinator,
        async_add_entities,
        platform,
        lambda data: set(data.hosts_by_id),
        lambda host_id: host_unique_id(entry.entry_id, host_id, key),
        factory,
    )


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
    """Track global policies as they are created and deleted in Fleet."""
    async_setup_dynamic_entities(
        hass,
        entry,
        coordinator,
        async_add_entities,
        platform,
        lambda data: set(data.policies_by_id),
        lambda policy_id: policy_unique_id(entry.entry_id, policy_id, key),
        factory,
    )


@callback
def async_setup_dynamic_label_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: FleetInventoryCoordinator,
    async_add_entities: AddEntitiesCallback,
    platform: str,
    key: str,
    factory: Callable[[int], Any],
) -> None:
    """Track labels as they are created and deleted in Fleet."""
    async_setup_dynamic_entities(
        hass,
        entry,
        coordinator,
        async_add_entities,
        platform,
        lambda data: set(data.labels_by_id),
        lambda label_id: label_unique_id(entry.entry_id, label_id, key),
        factory,
    )
