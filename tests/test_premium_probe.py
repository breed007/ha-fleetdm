"""Tests for licence tier detection and server metadata refresh.

Regression cover for a bug where `async_is_premium` caught `FleetError`.
`FleetAuthError` and `FleetConnectionError` both subclass it, so a rejected
token never reached the reauth flow and a momentary network blip silently
pinned the integration to Free-tier semantics until the next reload.
"""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.fleetdm.api import (
    FleetAuthError,
    FleetClient,
    FleetConnectionError,
)
from custom_components.fleetdm.coordinator import METADATA_REFRESH_EVERY

from .conftest import (
    API,
    BASE_URL,
    FREE_CONFIG_RESPONSE,
    PREMIUM_CONFIG_RESPONSE,
    VERSION_RESPONSE,
    mock_fleet,
)
from .test_drift import poll, setup_with


async def test_auth_error_propagates_from_premium_probe(hass, aioclient_mock) -> None:
    """A rejected token must surface, not be mistaken for "not Premium"."""
    aioclient_mock.get(f"{API}/config", status=401)
    client = FleetClient(async_get_clientsession(hass), BASE_URL, "token")

    with pytest.raises(FleetAuthError):
        await client.async_is_premium()


async def test_connection_error_propagates_from_premium_probe(
    hass, aioclient_mock
) -> None:
    """A network blip must not silently downgrade the fleet to Free."""
    aioclient_mock.get(f"{API}/config", exc=TimeoutError())
    client = FleetClient(async_get_clientsession(hass), BASE_URL, "token")

    with pytest.raises(FleetConnectionError):
        await client.async_is_premium()


@pytest.mark.parametrize("status", [402, 403, 404])
async def test_permission_errors_degrade_to_free(hass, aioclient_mock, status) -> None:
    """A role or licence that genuinely cannot read /config still degrades."""
    aioclient_mock.get(f"{API}/config", status=status)
    client = FleetClient(async_get_clientsession(hass), BASE_URL, "token")

    assert await client.async_is_premium() is False


async def test_rejected_token_during_setup_triggers_reauth(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """A 401 on the config endpoint reaches the reauth flow."""
    mock_fleet(aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/version", json=VERSION_RESPONSE)
    aioclient_mock.get(f"{API}/config", status=401)
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == "reauth" for flow in flows)


async def test_metadata_not_refetched_every_poll(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """Version and tier are not worth a request on every cycle."""
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)

    await poll(hass, entry, aioclient_mock)
    requested = [str(call[1]) for call in aioclient_mock.mock_calls]
    assert not any(url.endswith("/config") for url in requested)


async def test_metadata_refreshed_periodically(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """A licence change is picked up without a reload."""
    entry = await setup_with(
        hass, mock_config_entry, aioclient_mock, config=FREE_CONFIG_RESPONSE
    )
    coordinator = entry.runtime_data.summary
    assert coordinator.premium is False

    # Fleet is upgraded to Premium underneath us.
    coordinator._polls_since_metadata = METADATA_REFRESH_EVERY - 1
    await poll(hass, entry, aioclient_mock, config=PREMIUM_CONFIG_RESPONSE)

    assert coordinator.premium is True
    state = hass.states.get("binary_sensor.fleet_compliance")
    assert state.attributes["basis"] == "critical_policies"
