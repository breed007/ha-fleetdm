"""Tests for the sensors added in v0.4: disk, osquery, MDM and OS versions."""

from __future__ import annotations

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.fleetdm.const import (
    CONF_ACTIVITY_EVENTS,
    DOMAIN,
    EVENT_ACTIVITY,
    EVENT_HOST_ENROLLED,
)

from .conftest import (
    activities_payload,
    enrollment_activity,
    mock_fleet,
)
from .test_drift import setup_with
from .test_hosts import inventory_poll


async def test_disk_percent_enabled_by_default(hass, setup_integration) -> None:
    """Free disk percentage is the number worth alerting on, so it is on."""
    state = hass.states.get("sensor.ada_laptop_disk_free")
    assert state is not None
    assert state.state == "42"
    assert state.attributes["unit_of_measurement"] == "%"


@pytest.mark.parametrize(
    ("key", "entity_id"),
    [
        ("disk_free_gigs", "sensor.ada_laptop_disk_free_space"),
        ("osquery_version", "sensor.ada_laptop_osquery_version"),
        ("mdm_status", "sensor.ada_laptop_mdm_status"),
    ],
)
async def test_secondary_host_sensors_disabled_by_default(
    hass, setup_integration, key, entity_id
) -> None:
    """The rest are registered but off, to keep the per-host count sane."""
    registry = er.async_get(hass)
    unique_id = f"{setup_integration.entry_id}_host_1_{key}"
    resolved = registry.async_get_entity_id("sensor", DOMAIN, unique_id)

    assert resolved == entity_id
    assert (
        registry.async_get(resolved).disabled_by is er.RegistryEntryDisabler.INTEGRATION
    )
    assert hass.states.get(resolved) is None


async def test_os_versions_counts_affected_hosts(hass, setup_integration) -> None:
    """The state is hosts on a vulnerable OS, not the number of versions."""
    state = hass.states.get("sensor.fleet_hosts_on_vulnerable_os")
    assert state is not None
    # Ubuntu (2 hosts, 4 CVEs) + Debian (1 host, 2 CVEs); macOS has none.
    assert state.state == "3"
    assert state.attributes["distinct_versions"] == 3

    versions = state.attributes["versions"]
    assert versions[0]["name"] == "macOS 26.6.1"  # most common first
    assert versions[0]["vulnerabilities_count"] == 0


async def test_os_versions_handles_empty_response(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """A fleet Fleet has not aggregated yet reports zero, not an error."""
    await setup_with(
        hass,
        mock_config_entry,
        aioclient_mock,
        os_versions={"count": 0, "os_versions": []},
    )

    state = hass.states.get("sensor.fleet_hosts_on_vulnerable_os")
    assert state.state == "0"
    assert state.attributes["distinct_versions"] == 0


async def test_activity_events_off_by_default(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """Fleet's audit feed is noisy, so generic events are opt-in."""
    events = async_capture_events(hass, EVENT_ACTIVITY)
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)

    await inventory_poll(
        hass,
        entry,
        aioclient_mock,
        activities=activities_payload(
            {"id": 40, "type": "user_logged_in", "details": {}},
            {"id": 41, "type": "ran_script", "details": {"host_id": 1}},
        ),
    )

    assert events == []


async def test_activity_events_when_enabled(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """Enabled, every non-enrollment activity fires with its Fleet type."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_ACTIVITY_EVENTS: True}
    )
    mock_fleet(aioclient_mock)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    activity_events = async_capture_events(hass, EVENT_ACTIVITY)
    enrollments = async_capture_events(hass, EVENT_HOST_ENROLLED)

    await inventory_poll(
        hass,
        mock_config_entry,
        aioclient_mock,
        activities=activities_payload(
            {"id": 40, "type": "ran_script", "details": {"host_id": 7}},
            enrollment_activity(41, 3, "Carol Server"),
        ),
    )

    # Enrollment keeps its own dedicated event rather than being folded in.
    assert [e.data["activity_type"] for e in activity_events] == ["ran_script"]
    assert activity_events[0].data["details"] == {"host_id": 7}
    assert len(enrollments) == 1


async def test_mdm_status_reports_unknown_without_mdm(hass, setup_integration) -> None:
    """A host with no mdm block reads unknown rather than crashing."""
    registry = er.async_get(hass)
    unique_id = f"{setup_integration.entry_id}_host_1_mdm_status"
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
    registry.async_update_entity(entity_id, disabled_by=None)
    await hass.config_entries.async_reload(setup_integration.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "unknown"
    assert state.attributes["connected_to_fleet"] is False
