"""Tests for per-host devices, entities and the fleet-size gate."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.fleetdm.const import (
    CONF_MISSING_AFTER_HOURS,
    CONF_PER_HOST_ENTITIES,
    CONF_SUMMARY_INTERVAL,
    DOMAIN,
    PER_HOST_AUTO,
    PER_HOST_ENTITY_THRESHOLD,
    PER_HOST_OFF,
    PER_HOST_ON,
)

from .conftest import (
    HOST_DESKTOP,
    HOST_LAPTOP,
    get_device,
    host,
    hosts_payload,
    mock_fleet,
)
from .test_drift import setup_with


async def inventory_poll(hass, entry, aioclient_mock, **kwargs: Any) -> None:
    """Re-mock Fleet and run one inventory coordinator cycle."""
    aioclient_mock.clear_requests()
    mock_fleet(aioclient_mock, **kwargs)
    await entry.runtime_data.inventory.async_refresh()
    await hass.async_block_till_done()


async def test_host_devices_created(hass, setup_integration) -> None:
    """Each host becomes its own device hanging off the Fleet hub."""
    entry_id = setup_integration.entry_id
    hub = get_device(hass, entry_id, entry_id)
    assert hub is not None

    laptop = get_device(hass, entry_id, f"{entry_id}_host_1")
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
    """A host that enrolls later appears without reloading the integration."""
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


async def test_large_fleet_honors_explicit_opt_in(
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


async def test_small_fleet_honors_explicit_opt_out(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """An explicit no is honored even on a small fleet."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_PER_HOST_ENTITIES: False}
    )
    mock_fleet(aioclient_mock)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.ada_laptop_online") is None


async def test_never_seen_host_is_missing_after_grace(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """A host that enrolled long ago and never checked in is missing.

    Regression: hosts with no seen_time were excluded from the missing set
    entirely, so the most-missing host possible could never trip the sensor.
    """
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_MISSING_AFTER_HOURS: 4}
    )
    mock_fleet(
        aioclient_mock,
        hosts=hosts_payload(
            host(1, "Never Seen", seen=False, enrolled_hours_ago=50),
            host(2, "Just Enrolled", seen=False, enrolled_hours_ago=0.2),
            host(3, "No Timestamps At All", seen=False),
        ),
    )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Enrolled two days ago, still silent.
    assert hass.states.get("binary_sensor.never_seen_missing").state == "on"
    # Enrolled minutes ago: give it a chance to report before crying wolf.
    assert hass.states.get("binary_sensor.just_enrolled_missing").state == "off"
    # Nothing at all suggests this host is alive.
    assert hass.states.get("binary_sensor.no_timestamps_at_all_missing").state == "on"


async def test_deleted_host_removes_device(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """A host dropped from Fleet must not leave an empty device behind.

    Regression: entities were purged but the device registry entry survived,
    and nothing implemented async_remove_config_entry_device either, so it
    could not be cleared from the UI.
    """
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)
    entry_id = entry.entry_id
    assert get_device(hass, entry_id, f"{entry_id}_host_2") is not None

    await inventory_poll(hass, entry, aioclient_mock, hosts=hosts_payload(HOST_LAPTOP))

    assert get_device(hass, entry_id, f"{entry_id}_host_2") is None
    # The surviving host and the hub are untouched.
    assert get_device(hass, entry_id, f"{entry_id}_host_1") is not None
    assert get_device(hass, entry_id, entry_id) is not None


async def test_host_device_metadata_follows_fleet(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """Renames and OS upgrades reach the device without a reload.

    Regression: DeviceInfo was captured once at entity construction, so a host
    renamed or upgraded in Fleet kept stale details until the entry reloaded.
    """
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)
    key = f"{entry.entry_id}_host_1"
    assert get_device(hass, entry.entry_id, key).name == "Ada Laptop"

    renamed = host(1, "Ada Workstation")
    renamed["os_version"] = "macOS 15.6"
    renamed["hardware_model"] = "Mac15,3"
    await inventory_poll(
        hass, entry, aioclient_mock, hosts=hosts_payload(renamed, HOST_DESKTOP)
    )

    device = get_device(hass, entry.entry_id, key)
    assert device.name == "Ada Workstation"
    assert device.sw_version == "macOS 15.6"
    assert device.model == "Mac15,3 · darwin"


async def test_device_removal_allowed_only_for_departed_hosts(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """The UI may delete a gone host's device, but not a live one or the hub."""
    from custom_components.fleetdm import async_remove_config_entry_device

    entry = await setup_with(hass, mock_config_entry, aioclient_mock)
    live = get_device(hass, entry.entry_id, f"{entry.entry_id}_host_1")
    hub = get_device(hass, entry.entry_id, entry.entry_id)

    # A host Fleet still reports would just be recreated on the next refresh.
    assert await async_remove_config_entry_device(hass, entry, live) is False
    # The hub belongs to the config entry itself.
    assert await async_remove_config_entry_device(hass, entry, hub) is False

    # Built through the registry rather than constructed directly: DeviceEntry's
    # signature differs across the Home Assistant versions CI covers.
    stale = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{entry.entry_id}_host_999")},
    )
    assert await async_remove_config_entry_device(hass, entry, stale) is True


async def test_saving_options_preserves_auto_gating(hass, setup_integration) -> None:
    """Saving the options form must not silently disable the size rule.

    Regression: the per-host option was a boolean with a computed default, so
    submitting the form to change anything at all wrote an explicit value and
    killed auto-gating permanently.
    """
    result = await hass.config_entries.options.async_init(setup_integration.entry_id)
    # Change something unrelated, leaving every other field at its default.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {**result["data_schema"]({}), CONF_SUMMARY_INTERVAL: 90},
    )
    await hass.async_block_till_done()

    assert setup_integration.options[CONF_SUMMARY_INTERVAL] == 90
    assert setup_integration.options[CONF_PER_HOST_ENTITIES] == PER_HOST_AUTO


@pytest.mark.parametrize(
    ("value", "expect_entities"),
    [(PER_HOST_ON, True), (PER_HOST_OFF, False)],
)
async def test_explicit_tri_state_values(
    hass, aioclient_mock, mock_config_entry, value, expect_entities
) -> None:
    """The explicit choices win regardless of fleet size."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_PER_HOST_ENTITIES: value}
    )
    mock_fleet(aioclient_mock)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    exists = hass.states.get("binary_sensor.ada_laptop_online") is not None
    assert exists is expect_entities


async def test_auto_gate_reevaluated_as_fleet_grows(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """In auto mode, crossing the threshold stops per-host entities.

    Regression: the gate was only consulted at platform setup, so a fleet that
    grew past the threshold kept adding per-host entities indefinitely.
    """
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)
    assert hass.states.get("binary_sensor.ada_laptop_online") is not None

    many = hosts_payload(
        *(host(i, f"Host {i}") for i in range(1, PER_HOST_ENTITY_THRESHOLD + 2))
    )
    await inventory_poll(hass, entry, aioclient_mock, hosts=many)

    assert hass.states.get("binary_sensor.host_1_online") is None
    assert hass.states.get("binary_sensor.ada_laptop_online") is None
