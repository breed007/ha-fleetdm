"""Fixtures for the Fleet integration tests."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.fleetdm.const import (
    CONF_API_TOKEN,
    CONF_URL,
    CONF_VERIFY_SSL,
    DOMAIN,
)

BASE_URL = "https://fleet.example.com"
API = f"{BASE_URL}/api/latest/fleet"

VERSION_RESPONSE = {
    "version": "4.52.0",
    "branch": "main",
    "revision": "abc123",
    "go_version": "go1.22.5",
    "build_date": "2025-01-15",
    "build_user": "runner",
}

# Sanitised shape of a small mixed macOS / Windows / Linux fleet.
HOST_SUMMARY_RESPONSE = {
    "totals_hosts_count": 15,
    "online_count": 12,
    "offline_count": 2,
    "missing_30_days_count": 1,
    "mia_count": 1,
    "new_count": 0,
    "all_linux_count": 3,
}

POLICY_GATEKEEPER = {
    "id": 1,
    "name": "Gatekeeper enabled",
    "query": "SELECT 1 FROM gatekeeper WHERE assessments_enabled = 1;",
    "description": "Checks if Gatekeeper is enabled on macOS devices",
    "critical": False,
    "team_id": None,
    "resolution": "Enable Gatekeeper in System Settings",
    "platform": "darwin",
    "passing_host_count": 8,
    "failing_host_count": 0,
    "host_count_updated_at": "2025-01-20T15:23:57Z",
}

POLICY_BITLOCKER = {
    "id": 2,
    "name": "Windows disks encrypted",
    "query": "SELECT 1 FROM bitlocker_info WHERE protection_status = 1;",
    "description": "Checks if the hard disk is encrypted on Windows devices",
    "critical": True,
    "team_id": None,
    "resolution": "Turn on BitLocker",
    "platform": "windows",
    "passing_host_count": 3,
    "failing_host_count": 0,
    "host_count_updated_at": "2025-01-20T15:23:57Z",
}

FREE_CONFIG_RESPONSE = {
    "org_info": {"org_name": "Homelab"},
    "license": {"tier": "free", "expiration": "0001-01-01T00:00:00Z"},
}

PREMIUM_CONFIG_RESPONSE = {
    "org_info": {"org_name": "Homelab"},
    "license": {"tier": "premium", "expiration": "2031-01-01T00:00:00Z"},
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading of the custom integration in every test."""
    return


def policies_payload(*policies: dict[str, Any]) -> dict[str, Any]:
    """Wrap policy dicts in the API's envelope."""
    return {"policies": list(policies)}


def mock_fleet(
    aioclient_mock: AiohttpClientMocker,
    *,
    version: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
    policies: dict[str, Any] | None = None,
) -> None:
    """Register a full set of successful Fleet endpoints."""
    aioclient_mock.get(f"{API}/version", json=version or VERSION_RESPONSE)
    aioclient_mock.get(f"{API}/config", json=config or FREE_CONFIG_RESPONSE)
    aioclient_mock.get(f"{API}/host_summary", json=summary or HOST_SUMMARY_RESPONSE)
    aioclient_mock.get(
        f"{API}/global/policies",
        json=policies or policies_payload(POLICY_GATEKEEPER, POLICY_BITLOCKER),
    )


def remock_fleet(aioclient_mock: AiohttpClientMocker, **kwargs: Any) -> None:
    """Reset the mocked endpoints, e.g. to change policy state between polls."""
    aioclient_mock.clear_requests()
    mock_fleet(aioclient_mock, **kwargs)


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a configured Fleet config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="fleet.example.com",
        unique_id=BASE_URL,
        data={
            CONF_URL: BASE_URL,
            CONF_API_TOKEN: "test-token",
            CONF_VERIFY_SSL: True,
        },
        options={},
    )


@pytest.fixture
async def setup_integration(hass, mock_config_entry, aioclient_mock):
    """Set up the integration with default mocked responses."""
    mock_fleet(aioclient_mock)
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry
