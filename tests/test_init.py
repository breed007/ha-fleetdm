"""Tests for Fleet integration setup, unload and error handling."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr

from custom_components.fleetdm.const import DOMAIN

from .conftest import API, BASE_URL


async def test_setup_and_unload(hass, setup_integration) -> None:
    """The entry sets up, creates the hub device, and unloads cleanly."""
    assert setup_integration.state is ConfigEntryState.LOADED

    devices = dr.async_get(hass)
    hub = devices.async_get_device(identifiers={(DOMAIN, setup_integration.entry_id)})
    assert hub is not None
    assert hub.sw_version == "4.52.0"
    assert hub.configuration_url == BASE_URL

    assert await hass.config_entries.async_unload(setup_integration.entry_id)
    await hass.async_block_till_done()
    assert setup_integration.state is ConfigEntryState.NOT_LOADED


async def test_setup_retries_on_connection_error(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """An unreachable server leaves the entry in a retrying state."""
    aioclient_mock.get(f"{API}/version", exc=TimeoutError())
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_starts_reauth_on_401(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """A rejected token puts the entry into the reauth state."""
    aioclient_mock.get(f"{API}/version", status=401)
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == "reauth" for flow in flows)


async def test_entities_created(hass, setup_integration) -> None:
    """The documented fleet-level entities exist with the expected values."""
    assert hass.states.get("sensor.fleet_hosts_online").state == "12"
    assert hass.states.get("sensor.fleet_hosts_offline").state == "2"
    assert hass.states.get("sensor.fleet_hosts_missing").state == "1"
    assert hass.states.get("sensor.fleet_hosts_new").state == "0"
    assert hass.states.get("sensor.fleet_hosts_total").state == "15"
    assert hass.states.get("sensor.fleet_policies_failing").state == "0"
    assert hass.states.get("binary_sensor.fleet_compliance").state == "off"
    assert hass.states.get("event.fleet_fleet_events") is not None


async def test_token_revoked_while_running_triggers_reauth(
    hass, aioclient_mock, setup_integration
) -> None:
    """A token revoked after setup raises reauth rather than going stale."""
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/host_summary", status=401)
    aioclient_mock.get(f"{API}/global/policies", status=401)

    await setup_integration.runtime_data.summary.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get("sensor.fleet_hosts_online").state == "unavailable"
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == "reauth" for flow in flows)
