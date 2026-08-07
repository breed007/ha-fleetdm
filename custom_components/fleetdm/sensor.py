"""Sensor platform for the Fleet integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import FleetConfigEntry
from .coordinator import FleetData, FleetSummaryCoordinator
from .entity import (
    FleetEntity,
    FleetPolicyEntity,
    async_setup_dynamic_policy_entities,
    fleet_unique_id,
)

UNIT_HOSTS = "hosts"
UNIT_POLICIES = "policies"

POLICY_FAILING_KEY = "failing_hosts"


@dataclass(frozen=True, kw_only=True)
class FleetSensorEntityDescription(SensorEntityDescription):
    """Describes a fleet-level Fleet sensor."""

    value_fn: Callable[[FleetData], StateType]
    attrs_fn: Callable[[FleetData], dict[str, Any]] | None = None


def _failing_policy_attrs(data: FleetData) -> dict[str, Any]:
    """List the currently failing policies, worst first."""
    failing = sorted(
        data.failing_policies,
        key=lambda policy: (-policy.failing_host_count, policy.name),
    )
    return {
        "policies": [
            {
                "id": policy.id,
                "name": policy.name,
                "failing_host_count": policy.failing_host_count,
                "critical": policy.critical,
            }
            for policy in failing
        ]
    }


SENSORS: tuple[FleetSensorEntityDescription, ...] = (
    FleetSensorEntityDescription(
        key="hosts_online",
        translation_key="hosts_online",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UNIT_HOSTS,
        value_fn=lambda data: data.summary.online,
    ),
    FleetSensorEntityDescription(
        key="hosts_offline",
        translation_key="hosts_offline",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UNIT_HOSTS,
        value_fn=lambda data: data.summary.offline,
    ),
    FleetSensorEntityDescription(
        key="hosts_missing",
        translation_key="hosts_missing",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UNIT_HOSTS,
        value_fn=lambda data: data.summary.missing,
    ),
    FleetSensorEntityDescription(
        key="hosts_new",
        translation_key="hosts_new",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UNIT_HOSTS,
        value_fn=lambda data: data.summary.new,
    ),
    FleetSensorEntityDescription(
        key="hosts_total",
        translation_key="hosts_total",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UNIT_HOSTS,
        value_fn=lambda data: data.summary.total,
    ),
    FleetSensorEntityDescription(
        key="policies_failing",
        translation_key="policies_failing",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UNIT_POLICIES,
        value_fn=lambda data: len(data.failing_policies),
        attrs_fn=_failing_policy_attrs,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FleetConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fleet sensors."""
    coordinator = entry.runtime_data.summary

    async_add_entities(
        FleetSummarySensor(coordinator, entry, description) for description in SENSORS
    )

    async_setup_dynamic_policy_entities(
        hass,
        entry,
        coordinator,
        async_add_entities,
        Platform.SENSOR,
        POLICY_FAILING_KEY,
        lambda policy_id: FleetPolicyFailingSensor(coordinator, entry, policy_id),
    )


class FleetSummarySensor(FleetEntity, SensorEntity):
    """A fleet-level count sensor."""

    entity_description: FleetSensorEntityDescription

    def __init__(
        self,
        coordinator: FleetSummaryCoordinator,
        entry: FleetConfigEntry,
        description: FleetSensorEntityDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._attr_unique_id = fleet_unique_id(entry.entry_id, description.key)

    @property
    def native_value(self) -> StateType:
        """Return the current count."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes, where the description supplies them."""
        if self.coordinator.data is None or self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)


class FleetPolicyFailingSensor(FleetPolicyEntity, SensorEntity):
    """Failing host count for a single policy.

    Disabled by default: on a fleet with a large policy library this doubles the
    entity count, and the per-policy binary sensor already carries the same
    number as an attribute. Enable it for the policies you want to graph.
    """

    _attr_entity_registry_enabled_default = False
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UNIT_HOSTS

    def __init__(
        self,
        coordinator: FleetSummaryCoordinator,
        entry: FleetConfigEntry,
        policy_id: int,
    ) -> None:
        """Initialise the per-policy sensor."""
        super().__init__(coordinator, entry, policy_id, POLICY_FAILING_KEY)

    @property
    def name(self) -> str | None:
        """Distinguish this from the policy's binary sensor of the same name."""
        if (policy := self.policy) is None:
            return None
        return f"{policy.name} failing hosts"

    @property
    def native_value(self) -> StateType:
        """Return the number of hosts currently failing this policy."""
        if (policy := self.policy) is None:
            return None
        return policy.failing_host_count
