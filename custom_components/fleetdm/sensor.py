"""Sensor platform for the Fleet integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import FleetConfigEntry
from .const import (
    CONF_LABEL_SENSORS,
    CONF_VULNERABILITY_SENSORS,
    DEFAULT_LABEL_SENSORS,
    DEFAULT_VULNERABILITY_SENSORS,
)
from .coordinator import (
    FleetData,
    FleetInventoryCoordinator,
    FleetSummaryCoordinator,
    per_host_entities_enabled,
)
from .entity import (
    FleetEntity,
    FleetHostEntity,
    FleetInventoryEntity,
    FleetLabelEntity,
    FleetPolicyEntity,
    async_setup_dynamic_host_entities,
    async_setup_dynamic_label_entities,
    async_setup_dynamic_policy_entities,
    fleet_unique_id,
)

UNIT_HOSTS = "hosts"
UNIT_POLICIES = "policies"
UNIT_TITLES = "titles"

POLICY_FAILING_KEY = "failing_hosts"
HOST_FAILING_POLICIES_KEY = "failing_policies"
HOST_LAST_RESTARTED_KEY = "last_restarted"
LABEL_HOSTS_KEY = "hosts"


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

    inventory = entry.runtime_data.inventory

    if entry.options.get(CONF_VULNERABILITY_SENSORS, DEFAULT_VULNERABILITY_SENSORS):
        async_add_entities([FleetVulnerableSoftwareSensor(inventory, entry)])

    if entry.options.get(CONF_LABEL_SENSORS, DEFAULT_LABEL_SENSORS):
        async_setup_dynamic_label_entities(
            hass,
            entry,
            inventory,
            async_add_entities,
            Platform.SENSOR,
            LABEL_HOSTS_KEY,
            lambda label_id: FleetLabelHostsSensor(inventory, entry, label_id),
        )

    host_count = len(inventory.data.hosts) if inventory.data else 0
    if not per_host_entities_enabled(entry, host_count):
        return

    for key, factory in (
        (HOST_FAILING_POLICIES_KEY, FleetHostFailingPoliciesSensor),
        (HOST_LAST_RESTARTED_KEY, FleetHostLastRestartedSensor),
    ):
        async_setup_dynamic_host_entities(
            hass,
            entry,
            inventory,
            async_add_entities,
            Platform.SENSOR,
            key,
            partial(_build_host_sensor, factory, inventory, entry),
        )


def _build_host_sensor(factory, coordinator, entry, host_id: int):
    """Construct a per-host sensor for the dynamic-entity helper."""
    return factory(coordinator, entry, host_id)


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


class FleetVulnerableSoftwareSensor(FleetInventoryEntity, SensorEntity):
    """Count of software titles with known vulnerabilities across the fleet."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UNIT_TITLES
    _attr_translation_key = "vulnerable_software"

    def __init__(
        self, coordinator: FleetInventoryCoordinator, entry: FleetConfigEntry
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = fleet_unique_id(entry.entry_id, "vulnerable_software")

    @property
    def native_value(self) -> StateType:
        """Return the exact number of vulnerable titles Fleet reports."""
        data = self.coordinator.data
        if data is None or data.vulnerable is None:
            return None
        return data.vulnerable.count

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """List the worst titles by affected host count.

        Fleet orders this server-side, so these really are the most widespread
        vulnerable titles rather than whichever page happened to come back.

        There is deliberately no severity here: CVSS and EPSS are Fleet Premium
        fields, and inventing a severity from the CVE count would be worse than
        omitting it.
        """
        data = self.coordinator.data
        if data is None or data.vulnerable is None:
            return None
        vulnerable = data.vulnerable
        return {
            "counts_updated_at": (
                vulnerable.counts_updated_at.isoformat()
                if vulnerable.counts_updated_at
                else None
            ),
            "most_widespread": [
                {
                    "name": title.name,
                    "source": title.source,
                    "hosts_count": title.hosts_count,
                    "cve_count": title.cve_count,
                }
                for title in vulnerable.worst
            ],
        }


class FleetLabelHostsSensor(FleetLabelEntity, SensorEntity):
    """How many hosts currently match a Fleet label.

    Fleet's built-in labels are registered but **disabled by default**. They are
    the platform buckets Fleet defines for everyone rather than anything the
    operator chose: several are always empty on any given fleet, and "All Hosts"
    just restates `sensor.fleet_hosts_total`. Labels you created yourself are
    enabled, because those encode a distinction you cared enough to define.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UNIT_HOSTS

    def __init__(
        self,
        coordinator: FleetInventoryCoordinator,
        entry: FleetConfigEntry,
        label_id: int,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, entry, label_id, LABEL_HOSTS_KEY)
        label = self.label
        self._attr_entity_registry_enabled_default = not (
            label is not None and label.is_builtin
        )

    @property
    def name(self) -> str | None:
        """Prefix the label name so it reads clearly on the hub device."""
        if (label := self.label) is None:
            return None
        return f"Label {label.name}"

    @property
    def native_value(self) -> StateType:
        """Return the label's current membership count."""
        if (label := self.label) is None:
            return None
        return label.host_count

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose what kind of label this is and how membership is decided."""
        if (label := self.label) is None:
            return None
        return {
            "label_id": label.id,
            "builtin": label.is_builtin,
            "membership_type": label.membership_type,
            "platform": label.platform,
            "description": label.description,
        }


class FleetHostFailingPoliciesSensor(FleetHostEntity, SensorEntity):
    """How many policies a single host currently fails."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UNIT_POLICIES
    _attr_translation_key = "host_failing_policies"

    def __init__(
        self,
        coordinator: FleetInventoryCoordinator,
        entry: FleetConfigEntry,
        host_id: int,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, entry, host_id, HOST_FAILING_POLICIES_KEY)

    @property
    def native_value(self) -> StateType:
        """Return the host's failing policy count."""
        if (host := self.host) is None:
            return None
        return host.failing_policies_count


class FleetHostLastRestartedSensor(FleetHostEntity, SensorEntity):
    """When a host last booted.

    Reported as a timestamp rather than an uptime duration: Fleet gives the boot
    time directly, and a timestamp does not need re-rendering every second.

    Disabled by default because it is rarely what people are watching for, and
    on a large fleet it doubles the per-host entity count on its own.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "host_last_restarted"

    def __init__(
        self,
        coordinator: FleetInventoryCoordinator,
        entry: FleetConfigEntry,
        host_id: int,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, entry, host_id, HOST_LAST_RESTARTED_KEY)

    @property
    def native_value(self) -> datetime | None:
        """Return the host's boot time, if Fleet knows it."""
        if (host := self.host) is None:
            return None
        return host.last_restarted_at
