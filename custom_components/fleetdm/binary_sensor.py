"""Binary sensor platform for the Fleet integration."""

from __future__ import annotations

from functools import partial
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import FleetConfigEntry
from .coordinator import (
    FleetInventoryCoordinator,
    FleetSummaryCoordinator,
    per_host_entities_enabled,
)
from .entity import (
    FleetEntity,
    FleetHostEntity,
    FleetPolicyEntity,
    async_setup_dynamic_host_entities,
    async_setup_dynamic_policy_entities,
    fleet_unique_id,
)

POLICY_COMPLIANCE_KEY = "compliance"
HOST_ONLINE_KEY = "online"
HOST_MISSING_KEY = "missing"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FleetConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fleet binary sensors."""
    coordinator = entry.runtime_data.summary

    async_add_entities([FleetComplianceBinarySensor(coordinator, entry)])

    async_setup_dynamic_policy_entities(
        hass,
        entry,
        coordinator,
        async_add_entities,
        Platform.BINARY_SENSOR,
        POLICY_COMPLIANCE_KEY,
        lambda policy_id: FleetPolicyBinarySensor(coordinator, entry, policy_id),
    )

    inventory = entry.runtime_data.inventory
    host_count = len(inventory.data.hosts) if inventory.data else 0
    if not per_host_entities_enabled(entry, host_count):
        return

    for key, factory in (
        (HOST_ONLINE_KEY, FleetHostOnlineBinarySensor),
        (HOST_MISSING_KEY, FleetHostMissingBinarySensor),
    ):
        async_setup_dynamic_host_entities(
            hass,
            entry,
            inventory,
            async_add_entities,
            Platform.BINARY_SENSOR,
            key,
            partial(_build, factory, inventory, entry),
        )


def _build(factory, coordinator, entry, host_id: int):
    """Construct a per-host entity for the dynamic-entity helper."""
    return factory(coordinator, entry, host_id)


class FleetComplianceBinarySensor(FleetEntity, BinarySensorEntity):
    """Fleet-wide compliance problem sensor."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "compliance"

    def __init__(
        self, coordinator: FleetSummaryCoordinator, entry: FleetConfigEntry
    ) -> None:
        """Initialise the compliance sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = fleet_unique_id(entry.entry_id, "compliance")

    @property
    def is_on(self) -> bool | None:
        """Whether the fleet is out of compliance."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.compliance_problem

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Explain which policies drove the state, and on what basis."""
        data = self.coordinator.data
        if data is None:
            return None

        failing = data.failing_policies
        # Make the Free/Premium difference visible rather than implicit: on Free
        # there is no `critical` flag, so this sensor watches every policy.
        basis = "critical_policies" if data.premium else "all_policies"
        triggering = [
            policy for policy in failing if policy.critical or not data.premium
        ]
        return {
            "basis": basis,
            "premium": data.premium,
            "failing_policy_count": len(failing),
            "triggering_policies": sorted(policy.name for policy in triggering),
        }


class FleetPolicyBinarySensor(FleetPolicyEntity, BinarySensorEntity):
    """Problem sensor for a single Fleet global policy."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(
        self,
        coordinator: FleetSummaryCoordinator,
        entry: FleetConfigEntry,
        policy_id: int,
    ) -> None:
        """Initialise the per-policy sensor."""
        super().__init__(coordinator, entry, policy_id, POLICY_COMPLIANCE_KEY)

    @property
    def is_on(self) -> bool | None:
        """Whether any host currently fails this policy."""
        if (policy := self.policy) is None:
            return None
        return policy.is_failing


class FleetHostBinarySensorBase(FleetHostEntity, BinarySensorEntity):
    """Shared attributes for the per-host binary sensors."""

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose the host metadata worth seeing next to the state."""
        if (host := self.host) is None:
            return None
        return {
            "host_id": host.id,
            "hostname": host.hostname,
            "platform": host.platform,
            "os_version": host.os_version,
            "primary_ip": host.primary_ip,
            "status": host.status,
            "last_seen": host.seen_time.isoformat() if host.seen_time else None,
        }


class FleetHostOnlineBinarySensor(FleetHostBinarySensorBase):
    """Whether Fleet currently considers a host online.

    This is check-in freshness, not reachability: Fleet marks a host online if
    its osquery agent reported within the expected interval, so a host can be
    powered on and routable and still read as offline here.
    """

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "host_online"

    def __init__(
        self,
        coordinator: FleetInventoryCoordinator,
        entry: FleetConfigEntry,
        host_id: int,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, entry, host_id, HOST_ONLINE_KEY)

    @property
    def is_on(self) -> bool | None:
        """Connectivity device class: on means online."""
        if (host := self.host) is None:
            return None
        return host.is_online


class FleetHostMissingBinarySensor(FleetHostBinarySensorBase):
    """Whether a host has not checked in for longer than the configured window.

    Independent of Fleet's own 30-day "missing" bucket, which is far too slow to
    notice a laptop that is lost, dead, or has had its agent disabled.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "host_missing"

    def __init__(
        self,
        coordinator: FleetInventoryCoordinator,
        entry: FleetConfigEntry,
        host_id: int,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, entry, host_id, HOST_MISSING_KEY)

    @property
    def is_on(self) -> bool | None:
        """Whether the host has been unseen past the threshold."""
        host = self.host
        if host is None or host.seen_time is None:
            return None
        return host.seen_time < dt_util.utcnow() - self.coordinator.missing_after
