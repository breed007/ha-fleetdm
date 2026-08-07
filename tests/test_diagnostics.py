"""Tests for Fleet diagnostics redaction."""

from __future__ import annotations

from custom_components.fleetdm.const import (
    CONF_API_TOKEN,
    CONF_REDACT_HOSTNAMES,
    CONF_URL,
)
from custom_components.fleetdm.diagnostics import (
    async_get_config_entry_diagnostics,
)


async def test_token_always_redacted(hass, setup_integration) -> None:
    """The API token never appears in diagnostics, whatever the options say."""
    hass.config_entries.async_update_entry(
        setup_integration, options={CONF_REDACT_HOSTNAMES: False}
    )
    await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, setup_integration)

    assert diagnostics["entry"]["data"][CONF_API_TOKEN] == "**REDACTED**"
    assert "test-token" not in str(diagnostics)


async def test_hostname_redacted_by_default(hass, setup_integration) -> None:
    """Hostname redaction is on unless the operator turns it off."""
    diagnostics = await async_get_config_entry_diagnostics(hass, setup_integration)

    assert diagnostics["hostnames_redacted"] is True
    assert diagnostics["entry"]["data"][CONF_URL] == "https://**REDACTED**"
    assert "fleet.example.com" not in str(diagnostics)


async def test_hostname_kept_when_redaction_disabled(hass, setup_integration) -> None:
    """Turning redaction off keeps the URL for easier debugging."""
    hass.config_entries.async_update_entry(
        setup_integration, options={CONF_REDACT_HOSTNAMES: False}
    )
    await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, setup_integration)

    assert diagnostics["hostnames_redacted"] is False
    assert diagnostics["entry"]["data"][CONF_URL] == "https://fleet.example.com"


async def test_diagnostics_content(hass, setup_integration) -> None:
    """Diagnostics carry the data a drift bug report actually needs."""
    diagnostics = await async_get_config_entry_diagnostics(hass, setup_integration)

    assert diagnostics["server"]["version"] == "4.52.0"
    assert diagnostics["server"]["premium"] is False
    assert diagnostics["host_summary"]["online"] == 12
    assert diagnostics["coordinator"]["last_update_success"] is True
    assert diagnostics["coordinator"]["update_interval_seconds"] == 60

    policy_names = {policy["name"] for policy in diagnostics["policies"]}
    assert policy_names == {"Gatekeeper enabled", "Windows disks encrypted"}
