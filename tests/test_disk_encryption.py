"""Tests for disk encryption, which Fleet 4.93 added to the host list."""

from __future__ import annotations

from typing import Any

from custom_components.fleetdm.const import PER_HOST_ENTITY_THRESHOLD

from .conftest import host, hosts_payload
from .test_drift import setup_with
from .test_hosts import inventory_poll

FLEET_SENSOR = "sensor.fleet_hosts_without_disk_encryption"


def encrypted(host_id: int, name: str, value: bool | None) -> dict[str, Any]:
    """Build a host as Fleet 4.93+ reports it, with the encryption field."""
    return {**host(host_id, name), "disk_encryption_enabled": value}


MODERN_FLEET = hosts_payload(
    encrypted(1, "Ada Laptop", True),
    encrypted(2, "Grace Desktop", False),
    encrypted(3, "Carol Server", None),
)


async def test_per_host_sensor_states(hass, aioclient_mock, mock_config_entry) -> None:
    """On is encrypted, off is not, and a host that does not say is unknown."""
    await setup_with(hass, mock_config_entry, aioclient_mock, hosts=MODERN_FLEET)

    assert hass.states.get("binary_sensor.ada_laptop_disk_encryption").state == "on"
    assert hass.states.get("binary_sensor.grace_desktop_disk_encryption").state == "off"
    assert (
        hass.states.get("binary_sensor.carol_server_disk_encryption").state == "unknown"
    )


async def test_fleet_sensor_counts_unencrypted(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """The fleet-wide count names the hosts and separates the unknown ones."""
    await setup_with(hass, mock_config_entry, aioclient_mock, hosts=MODERN_FLEET)

    state = hass.states.get(FLEET_SENSOR)
    assert state.state == "1"
    assert state.attributes["hosts"] == ["Grace Desktop"]
    assert state.attributes["unknown_count"] == 1


async def test_absent_on_older_fleet(hass, setup_integration) -> None:
    """A server before 4.93 does not report it, so nothing is created."""
    assert hass.states.get(FLEET_SENSOR) is None
    assert hass.states.get("binary_sensor.ada_laptop_disk_encryption") is None
    # The rest of the host's entities are unaffected.
    assert hass.states.get("binary_sensor.ada_laptop_online") is not None


async def test_appears_after_fleet_upgrade(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """Upgrading Fleet creates the entities without a reload."""
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)
    assert hass.states.get(FLEET_SENSOR) is None

    await inventory_poll(hass, entry, aioclient_mock, hosts=MODERN_FLEET)

    assert hass.states.get(FLEET_SENSOR).state == "1"
    assert hass.states.get("binary_sensor.ada_laptop_disk_encryption").state == "on"


async def test_fleet_sensor_works_on_large_fleets(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """Above the per-host threshold, the fleet-wide count still reports."""
    many = hosts_payload(
        *(
            encrypted(i, f"Host {i}", i % 2 == 0)
            for i in range(1, PER_HOST_ENTITY_THRESHOLD + 2)
        )
    )
    await setup_with(hass, mock_config_entry, aioclient_mock, hosts=many)

    assert hass.states.get("binary_sensor.host_1_disk_encryption") is None
    # Odd IDs are unencrypted: 1, 3, ..., 51.
    assert hass.states.get(FLEET_SENSOR).state == str(
        (PER_HOST_ENTITY_THRESHOLD + 2) // 2
    )
