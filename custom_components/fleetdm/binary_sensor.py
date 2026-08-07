"""Binary sensor platform for the Fleet integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import FleetConfigEntry
from .coordinator import FleetSummaryCoordinator
from .entity import (
    FleetEntity,
    FleetPolicyEntity,
    async_setup_dynamic_policy_entities,
    fleet_unique_id,
)

POLICY_COMPLIANCE_KEY = "compliance"


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
