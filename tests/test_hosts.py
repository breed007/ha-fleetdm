"""Tests for per-host devices, entities and the fleet-size gate."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.fleetdm.const import (
    CONF_MISSING_AFTER_HOURS,
    CONF_PER_HOST_ENTITIES,
    DOMAIN,
    PER_HOST_ENTITY_THRESHOLD,
)

from .conftest import HOST_DESKTOP, HOST_LAPTOP, host, hosts_payload, mock_fleet
from .test_drift import setup_with


async def inventory_poll(hass, entry, aioclient_mock, **kwargs: Any) -> None:
    """Re-mock Fleet and run one inventory coordinator cycle."""
    aioclient_mock.clear_requests()
    mock_fleet(aioclient_mock, **kwargs)
    await entry.runtime_data.inventory.async_refresh()
    await hass.async_block_till_done()


async def test_host_devices_created(hass, setup_integration) -> None:
    """Each host becomes its own device hanging off the Fleet hub."""
    devices = dr.async_get(hass)
    hub = devices.async_get_device(identifiers={(DOMAIN, setup_integration.entry_id)})
    assert hub is not None

    laptop = devices.async_get_device(
        identifiers={(DOMAIN, f"{setup_integration.entry_id}_host_1")}
    )
    assert laptop is not None
    assert laptop.name == "Ada Laptop"
    assert laptop.via_device_id == hub.id
    assert laptop.configuration_url == "https://fleet.example.com/hosts/1"
    assert laptop.model == "MacBookPro18,3 · darwin"


async def test_host_entities_created(hass, setup_integration) -> None:
    """Online, missing and failing-policy entities exist per host."""
    online = hass.states.get("binary_sensor.ada_laptop_online")
    assert online is not None
    assert online.state == "on"
    assert online.attributes["device_class"] == "connectivity"
    assert online.attributes["host_id"] == 1
    assert online.attributes["primary_ip"] == "192.168.10.1"

    # A host Fleet reports as offline reads off, not unavailable: the record
    # still exists, the machine is simply not checking in.
    assert hass.states.get("binary_sensor.grace_desktop_online").state == "off"

    assert hass.states.get("sensor.ada_laptop_failing_policies").state == "2"
    assert hass.states.get("binary_sensor.ada_laptop_missing").state == "off"


async def test_last_restarted_disabled_by_default(hass, setup_integration) -> None:
    """The boot-time sensor is registered but not enabled."""
    registry = er.async_get(hass)
    unique_id = f"{setup_integration.entry_id}_host_1_last_restarted"
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)

    assert entity_id == "sensor.ada_laptop_last_restarted"
    assert (
        registry.async_get(entity_id).disabled_by
        is er.RegistryEntryDisabler.INTEGRATION
    )


async def test_missing_sensor_uses_configured_threshold(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """A host unseen past the threshold trips its missing sensor."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_MISSING_AFTER_HOURS: 2}
    )
    mock_fleet(
        aioclient_mock,
        hosts=hosts_payload(
            host(1, "Fresh Host", seen_hours_ago=0.5),
            host(2, "Stale Host", seen_hours_ago=9),
        ),
    )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.fresh_host_missing").state == "off"
    assert hass.states.get("binary_sensor.stale_host_missing").state == "on"


async def test_new_host_adds_entities(hass, aioclient_mock, mock_config_entry) -> None:
    """A host that enrols later appears without reloading the integration."""
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)
    assert hass.states.get("binary_sensor.carol_server_online") is None

    await inventory_poll(
        hass,
        entry,
        aioclient_mock,
        hosts=hosts_payload(HOST_LAPTOP, HOST_DESKTOP, host(3, "Carol Server")),
    )

    assert hass.states.get("binary_sensor.carol_server_online") is not None


async def test_deleted_host_removes_entities(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """A host deleted in Fleet has its entities purged from the registry."""
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)
    registry = er.async_get(hass)
    unique_id = f"{entry.entry_id}_host_2_online"
    assert registry.async_get_entity_id("binary_sensor", DOMAIN, unique_id)

    await inventory_poll(hass, entry, aioclient_mock, hosts=hosts_payload(HOST_LAPTOP))

    assert registry.async_get_entity_id("binary_sensor", DOMAIN, unique_id) is None
    assert hass.states.get("binary_sensor.grace_desktop_online") is None


async def test_large_fleet_skips_per_host_entities(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """Above the threshold, per-host entities are not created by surprise."""
    many = hosts_payload(
        *(host(i, f"Host {i}") for i in range(1, PER_HOST_ENTITY_THRESHOLD + 2))
    )
    await setup_with(hass, mock_config_entry, aioclient_mock, hosts=many)

    assert hass.states.get("binary_sensor.host_1_online") is None
    # Fleet-level entities are unaffected by the gate.
    assert hass.states.get("sensor.fleet_hosts_online") is not None


async def test_large_fleet_honours_explicit_opt_in(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """An explicit yes in the options overrides the size rule."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_PER_HOST_ENTITIES: True}
    )
    many = hosts_payload(
        *(host(i, f"Host {i}") for i in range(1, PER_HOST_ENTITY_THRESHOLD + 2))
    )
    mock_fleet(aioclient_mock, hosts=many)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.host_1_online") is not None


async def test_small_fleet_honours_explicit_opt_out(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """An explicit no is honoured even on a small fleet."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_PER_HOST_ENTITIES: False}
    )
    mock_fleet(aioclient_mock)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.ada_laptop_online") is None
