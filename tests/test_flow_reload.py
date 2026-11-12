"""Reauth and reconfigure reload the entry exactly once.

Regression cover for 0.7.1. Both flows used async_update_reload_and_abort while
the entry also had an update listener that reloads on any change, so a token
rotation reloaded twice, and Home Assistant 2026.11 warns about that
combination ahead of changing it in 2026.12.
"""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType

import custom_components.fleetdm as integration
from custom_components.fleetdm.const import CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL

from .conftest import API, BASE_URL, remock_fleet

WARNING = "should use it for scheduling a reload"


async def test_reconfigure_reloads_once(hass, setup_integration, caplog) -> None:
    """A planned token rotation sets the entry up again exactly once."""
    with patch.object(
        integration, "async_setup_entry", wraps=integration.async_setup_entry
    ) as setup:
        result = await setup_integration.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_URL: BASE_URL, CONF_API_TOKEN: "rotated", CONF_VERIFY_SSL: True},
        )
        await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert setup.call_count == 1
    assert setup_integration.state is ConfigEntryState.LOADED
    assert WARNING not in caplog.text


async def test_reauth_on_loaded_entry_reloads_once(
    hass, setup_integration, caplog
) -> None:
    """A token revoked while running is replaced with a single reload."""
    with patch.object(
        integration, "async_setup_entry", wraps=integration.async_setup_entry
    ) as setup:
        result = await setup_integration.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_TOKEN: "new-token"}
        )
        await hass.async_block_till_done()

    assert result["reason"] == "reauth_successful"
    assert setup.call_count == 1
    assert setup_integration.data[CONF_API_TOKEN] == "new-token"
    assert WARNING not in caplog.text


async def test_reauth_recovers_entry_that_never_loaded(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """An entry whose setup failed on a rejected token comes back after reauth.

    It never got as far as registering its update listener, so nothing but
    the flow itself can reload it.
    """
    aioclient_mock.get(f"{API}/version", status=401)
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    assert not mock_config_entry.update_listeners

    remock_fleet(aioclient_mock)
    flow = next(
        f
        for f in hass.config_entries.flow.async_progress()
        if f["context"]["source"] == "reauth"
    )
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {CONF_API_TOKEN: "new-token"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.state is ConfigEntryState.LOADED
