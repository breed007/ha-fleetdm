"""Sensor platform for the Fleet integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import Any, override

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, Platform, UnitOfInformation
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import FleetConfigEntry
from .const import (
    CONF_LABEL_SENSORS,
    CONF_VULNERABILITY_SENSORS,
    CONF_WEBHOOKS,
    DEFAULT_LABEL_SENSORS,
    DEFAULT_VULNERABILITY_SENSORS,
    DEFAULT_WEBHOOKS,
    DOMAIN,
    SIGNAL_WEBHOOK_RECEIVED,
)
from .coordinator import (
    FleetData,
    FleetInventoryCoordinator,
    FleetSummaryCoordinator,
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

# Read-only, coordinator-driven: every entity reads from an already-fetched
# snapshot, so Home Assistant need not serialize updates across them.
PARALLEL_UPDATES = 0

UNIT_HOSTS = "hosts"
UNIT_POLICIES = "policies"
UNIT_TITLES = "titles"

POLICY_FAILING_KEY = "failing_hosts"
HOST_FAILING_POLICIES_KEY = "failing_policies"
HOST_LAST_RESTARTED_KEY = "last_restarted"
LABEL_HOSTS_KEY = "hosts"
HOST_DISK_PERCENT_KEY = "disk_free_percent"
HOST_DISK_GIGS_KEY = "disk_free_gigs"
HOST_OSQUERY_KEY = "osquery_version"
HOST_MDM_KEY = "mdm_status"


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

    async_add_entities([FleetOsVersionsSensor(inventory, entry)])

    if entry.options.get(CONF_WEBHOOKS, DEFAULT_WEBHOOKS):
        async_add_entities([FleetLastWebhookSensor(entry)])

    for key, factory in (
        (HOST_FAILING_POLICIES_KEY, FleetHostFailingPoliciesSensor),
        (HOST_LAST_RESTARTED_KEY, FleetHostLastRestartedSensor),
        (HOST_DISK_PERCENT_KEY, FleetHostDiskPercentSensor),
        (HOST_DISK_GIGS_KEY, FleetHostDiskFreeSensor),
        (HOST_OSQUERY_KEY, FleetHostOsqueryVersionSensor),
        (HOST_MDM_KEY, FleetHostMdmStatusSensor),
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


def _build_host_sensor(
    factory: Callable[[FleetInventoryCoordinator, FleetConfigEntry, int], SensorEntity],
    coordinator: FleetInventoryCoordinator,
    entry: FleetConfigEntry,
    host_id: int,
) -> SensorEntity:
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
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._attr_unique_id = fleet_unique_id(entry.entry_id, description.key)

    @property
    @override
    def native_value(self) -> StateType:
        """Return the current count."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes, where the description supplies them."""
        if self.entity_description.attrs_fn is None:
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
    _attr_translation_key = "policy_failing_hosts"

    def __init__(
        self,
        coordinator: FleetSummaryCoordinator,
        entry: FleetConfigEntry,
        policy_id: int,
    ) -> None:
        """Initialize the per-policy sensor."""
        super().__init__(coordinator, entry, policy_id, POLICY_FAILING_KEY)

    @property
    @override
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
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = fleet_unique_id(entry.entry_id, "vulnerable_software")

    @property
    @override
    def native_value(self) -> StateType:
        """Return the exact number of vulnerable titles Fleet reports."""
        data = self.coordinator.data
        if data.vulnerable is None:
            return None
        return data.vulnerable.count

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """List the worst titles by affected host count.

        Fleet orders this server-side, so these really are the most widespread
        vulnerable titles rather than whichever page happened to come back.

        There is deliberately no severity here: CVSS and EPSS are Fleet Premium
        fields, and inventing a severity from the CVE count would be worse than
        omitting it.
        """
        data = self.coordinator.data
        if data.vulnerable is None:
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
    _attr_translation_key = "label_hosts"

    def __init__(
        self,
        coordinator: FleetInventoryCoordinator,
        entry: FleetConfigEntry,
        label_id: int,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry, label_id, LABEL_HOSTS_KEY)
        label = self.label
        self._attr_entity_registry_enabled_default = not (
            label is not None and label.is_builtin
        )

    @property
    @override
    def native_value(self) -> StateType:
        """Return the label's current membership count."""
        if (label := self.label) is None:
            return None
        return label.host_count

    @property
    @override
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
        """Initialize the sensor."""
        super().__init__(coordinator, entry, host_id, HOST_FAILING_POLICIES_KEY)

    @property
    @override
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
        """Initialize the sensor."""
        super().__init__(coordinator, entry, host_id, HOST_LAST_RESTARTED_KEY)

    @property
    @override
    def native_value(self) -> datetime | None:
        """Return the host's boot time, if Fleet knows it."""
        if (host := self.host) is None:
            return None
        return host.last_restarted_at


class FleetOsVersionsSensor(FleetInventoryEntity, SensorEntity):
    """How many hosts run an OS version with known vulnerabilities.

    Fleet aggregates this server-side, so the whole picture costs one request
    regardless of fleet size. The state is the number of *hosts* affected
    rather than the number of versions, because that is the number worth
    acting on; the full spread is in the attributes.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UNIT_HOSTS
    _attr_translation_key = "os_versions_vulnerable"

    def __init__(
        self, coordinator: FleetInventoryCoordinator, entry: FleetConfigEntry
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = fleet_unique_id(entry.entry_id, "os_versions_vulnerable")

    @property
    @override
    def native_value(self) -> StateType:
        """Return the number of hosts on a vulnerable OS version."""
        data = self.coordinator.data
        if data.os_versions is None:
            return None
        return data.os_versions.vulnerable_host_count

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """List every OS version in the fleet, most common first."""
        data = self.coordinator.data
        if data.os_versions is None:
            return None
        versions = sorted(
            data.os_versions.versions,
            key=lambda v: (-v.hosts_count, v.name),
        )
        return {
            "distinct_versions": len(versions),
            "counts_updated_at": (
                data.os_versions.counts_updated_at.isoformat()
                if data.os_versions.counts_updated_at
                else None
            ),
            "versions": [
                {
                    "name": v.name,
                    "platform": v.platform,
                    "hosts_count": v.hosts_count,
                    "vulnerabilities_count": v.vulnerabilities_count,
                }
                for v in versions
            ],
        }


class FleetHostDiskPercentSensor(FleetHostEntity, SensorEntity):
    """Free disk space on a host, as a percentage."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_translation_key = "host_disk_free_percent"

    def __init__(
        self,
        coordinator: FleetInventoryCoordinator,
        entry: FleetConfigEntry,
        host_id: int,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry, host_id, HOST_DISK_PERCENT_KEY)

    @property
    @override
    def native_value(self) -> StateType:
        """Return the percentage of disk still free."""
        if (host := self.host) is None:
            return None
        return host.disk_percent_available


class FleetHostDiskFreeSensor(FleetHostEntity, SensorEntity):
    """Free disk space on a host, in gigabytes.

    Disabled by default: the percentage is the more useful number to alert on,
    and this would otherwise double the per-host sensor count.
    """

    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfInformation.GIGABYTES
    _attr_suggested_display_precision = 1
    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "host_disk_free_gigs"

    def __init__(
        self,
        coordinator: FleetInventoryCoordinator,
        entry: FleetConfigEntry,
        host_id: int,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry, host_id, HOST_DISK_GIGS_KEY)

    @property
    @override
    def native_value(self) -> StateType:
        """Return the gigabytes still free."""
        if (host := self.host) is None:
            return None
        return host.disk_gigs_available


class FleetHostOsqueryVersionSensor(FleetHostEntity, SensorEntity):
    """Which osquery build a host is running.

    Diagnostic and disabled by default: useful when chasing why one host
    reports differently from the rest, uninteresting the rest of the time.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "host_osquery_version"

    def __init__(
        self,
        coordinator: FleetInventoryCoordinator,
        entry: FleetConfigEntry,
        host_id: int,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry, host_id, HOST_OSQUERY_KEY)

    @property
    @override
    def native_value(self) -> StateType:
        """Return the reported osquery version."""
        if (host := self.host) is None:
            return None
        return host.osquery_version or None


class FleetHostMdmStatusSensor(FleetHostEntity, SensorEntity):
    """A host's MDM enrollment status as Fleet reports it.

    Diagnostic and disabled by default: on a fleet not using Fleet's MDM every
    one of these reads "Off", which is honest but not worth an entity each.

    Deliberately does not report disk encryption. Fleet exposes that only on
    the per-host detail endpoint, so including it would cost one request per
    host on every cycle.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "host_mdm_status"

    def __init__(
        self,
        coordinator: FleetInventoryCoordinator,
        entry: FleetConfigEntry,
        host_id: int,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry, host_id, HOST_MDM_KEY)

    @property
    @override
    def native_value(self) -> StateType:
        """Return the MDM enrollment status."""
        if (host := self.host) is None:
            return None
        return host.mdm_enrollment_status

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Whether this host is actually talking to Fleet's MDM."""
        if (host := self.host) is None:
            return None
        return {"connected_to_fleet": host.mdm_connected}


class FleetLastWebhookSensor(RestoreSensor):
    """When Fleet last reached Home Assistant through the webhook.

    Answers "is Fleet actually reaching me?" after pasting the URL into Fleet.
    Deliveries are otherwise invisible until one of them fires an event, and
    the failing policies webhook may run only once a day.

    Not tied to either coordinator: deliveries arrive whether or not polling is
    healthy, and this should keep reporting them when it is not.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "last_webhook"

    def __init__(self, entry: FleetConfigEntry) -> None:
        """Initialize the sensor on the hub device."""
        self._entry = entry
        self._attr_unique_id = fleet_unique_id(entry.entry_id, "last_webhook")
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    @override
    async def async_added_to_hass(self) -> None:
        """Restore the last delivery time and listen for new ones."""
        await super().async_added_to_hass()
        status = self._entry.runtime_data.webhook_status
        last = await self.async_get_last_sensor_data()
        if (
            status.last_received is None
            and last is not None
            and isinstance(last.native_value, datetime)
        ):
            status.last_received = last.native_value
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_WEBHOOK_RECEIVED.format(entry_id=self._entry.entry_id),
                self._handle_delivery,
            )
        )

    @callback
    def _handle_delivery(self) -> None:
        self.async_write_ha_state()

    @property
    @override
    def native_value(self) -> datetime | None:
        """When the last delivery arrived."""
        return self._entry.runtime_data.webhook_status.last_received

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """What the last delivery was, and counts since Home Assistant started."""
        status = self._entry.runtime_data.webhook_status
        return {
            "last_kind": status.last_kind,
            "deliveries_since_start": dict(status.counts),
        }
