"""Tests for per-label host count sensors."""

from __future__ import annotations

from homeassistant.helpers import entity_registry as er

from custom_components.fleetdm.api import FleetLabel
from custom_components.fleetdm.const import CONF_LABEL_SENSORS, DOMAIN

from .conftest import label, labels_payload, mock_fleet
from .test_drift import setup_with
from .test_hosts import inventory_poll


def test_empty_label_counts_zero_not_none() -> None:
    """Fleet omits host_count for an empty label but always sends count.

    Reading host_count first would make every empty label report nothing at
    all instead of zero, which is why `count` wins.
    """
    parsed = FleetLabel.from_json(
        {"id": 3, "name": "Proxmox Linux VMs", "count": 0, "label_type": "regular"}
    )
    assert parsed.host_count == 0

    # And an older server that only sends host_count still works.
    legacy = FleetLabel.from_json({"id": 4, "name": "Old", "host_count": 7})
    assert legacy.host_count == 7


async def test_custom_label_sensor(hass, setup_integration) -> None:
    """A label the operator created gets an enabled sensor."""
    state = hass.states.get("sensor.fleet_label_apple_silicon_macos_hosts")
    assert state is not None
    assert state.state == "1"
    assert state.attributes["builtin"] is False
    assert state.attributes["membership_type"] == "dynamic"
    assert state.attributes["label_id"] == 2


async def test_empty_label_reports_zero(hass, setup_integration) -> None:
    """An empty label reads 0, not unknown."""
    state = hass.states.get("sensor.fleet_label_proxmox_linux_vms")
    assert state is not None
    assert state.state == "0"


async def test_builtin_label_disabled_by_default(hass, setup_integration) -> None:
    """Fleet's own platform buckets are registered but off by default."""
    registry = er.async_get(hass)
    unique_id = f"{setup_integration.entry_id}_label_1_hosts"
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)

    assert entity_id == "sensor.fleet_label_all_hosts"
    assert (
        registry.async_get(entity_id).disabled_by
        is er.RegistryEntryDisabler.INTEGRATION
    )
    # "All Hosts" would only restate sensor.fleet_hosts_total.
    assert hass.states.get(entity_id) is None


async def test_new_label_adds_sensor(hass, aioclient_mock, mock_config_entry) -> None:
    """A label created in Fleet appears without reloading."""
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)
    assert hass.states.get("sensor.fleet_label_kiosk_machines") is None

    await inventory_poll(
        hass,
        entry,
        aioclient_mock,
        labels=labels_payload(
            label(2, "Apple Silicon macOS hosts", count=1),
            label(9, "Kiosk machines", count=4),
        ),
    )

    assert hass.states.get("sensor.fleet_label_kiosk_machines").state == "4"


async def test_deleted_label_removes_sensor(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """A label deleted in Fleet has its sensor purged from the registry."""
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)
    registry = er.async_get(hass)
    unique_id = f"{entry.entry_id}_label_2_hosts"
    assert registry.async_get_entity_id("sensor", DOMAIN, unique_id)

    await inventory_poll(
        hass,
        entry,
        aioclient_mock,
        labels=labels_payload(label(1, "All Hosts", count=2, builtin=True)),
    )

    assert registry.async_get_entity_id("sensor", DOMAIN, unique_id) is None


async def test_label_rename_follows(hass, aioclient_mock, mock_config_entry) -> None:
    """Renaming a label keeps the entity and updates the display name."""
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)
    entity_id = "sensor.fleet_label_apple_silicon_macos_hosts"
    assert hass.states.get(entity_id) is not None

    await inventory_poll(
        hass,
        entry,
        aioclient_mock,
        labels=labels_payload(label(2, "Apple Silicon Macs", count=1)),
    )

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes["friendly_name"] == "Fleet Label Apple Silicon Macs"


async def test_sensors_absent_when_disabled(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """Turning the option off removes the sensors and stops the request."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_LABEL_SENSORS: False}
    )
    mock_fleet(aioclient_mock)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.fleet_label_apple_silicon_macos_hosts") is None
    requested = [str(call[1]) for call in aioclient_mock.mock_calls]
    assert not any(url.endswith("/labels") for url in requested)
