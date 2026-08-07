"""Fixtures for the Fleet integration tests."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from homeassistant.util import dt as dt_util
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


def hosts_payload(*hosts: dict[str, Any]) -> dict[str, Any]:
    """Wrap host dicts in the API's envelope."""
    return {"hosts": list(hosts)}


def activities_payload(*activities: dict[str, Any]) -> dict[str, Any]:
    """Wrap activity dicts in the API's envelope, newest first as Fleet does."""
    return {"activities": sorted(activities, key=lambda a: -a["id"])}


def host(
    host_id: int,
    name: str,
    *,
    status: str = "online",
    seen_hours_ago: float = 0.1,
    failing_policies: int = 0,
    platform: str = "darwin",
) -> dict[str, Any]:
    """Build a host payload shaped like a real Fleet host list entry."""
    seen = dt_util.utcnow() - timedelta(hours=seen_hours_ago)
    return {
        "id": host_id,
        "display_name": name,
        "hostname": name.lower().replace(" ", "-"),
        "computer_name": name,
        "platform": platform,
        "os_version": "macOS 15.2" if platform == "darwin" else "Ubuntu 24.04",
        "status": status,
        "primary_ip": f"192.168.10.{host_id}",
        "hardware_model": "MacBookPro18,3",
        "osquery_version": "5.12.1",
        "seen_time": seen.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_restarted_at": "2026-08-01T09:00:00Z",
        "gigs_disk_space_available": 210.5,
        "percent_disk_space_available": 42,
        "issues": {"failing_policies_count": failing_policies, "total_issues_count": 0},
    }


def enrolment_activity(activity_id: int, host_id: int, name: str) -> dict[str, Any]:
    """Build a `fleet_enrolled` activity, the real type a Fleet 4.x server emits."""
    return {
        "id": activity_id,
        "type": "fleet_enrolled",
        "created_at": "2026-08-07T12:00:00Z",
        "details": {
            "host_id": host_id,
            "host_display_name": name,
            "host_serial": f"SERIAL{host_id}",
        },
    }


HOST_LAPTOP = host(1, "Ada Laptop", failing_policies=2)
HOST_DESKTOP = host(2, "Grace Desktop", status="offline", seen_hours_ago=3)

VULNERABLE_SOFTWARE_RESPONSE = {
    "count": 151,
    "counts_updated_at": "2026-08-07T15:00:00Z",
    "meta": {"has_next_results": False, "has_previous_results": False},
    "software_titles": [
        {
            "id": 10,
            "name": "Google Chrome",
            "display_name": "Google Chrome",
            "source": "apps",
            "hosts_count": 9,
            "versions_count": 2,
            "versions": [
                {"id": 1, "version": "120.0", "vulnerabilities": ["CVE-1", "CVE-2"]},
                {"id": 2, "version": "121.0", "vulnerabilities": ["CVE-2", "CVE-3"]},
            ],
        },
        {
            "id": 11,
            "name": "curl",
            "display_name": "curl",
            "source": "deb_packages",
            "hosts_count": 3,
            "versions_count": 1,
            "versions": [{"id": 3, "version": "8.5", "vulnerabilities": ["CVE-9"]}],
        },
    ],
}


def mock_fleet(
    aioclient_mock: AiohttpClientMocker,
    *,
    version: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
    policies: dict[str, Any] | None = None,
    hosts: dict[str, Any] | None = None,
    activities: dict[str, Any] | None = None,
    software: dict[str, Any] | None = None,
) -> None:
    """Register a full set of successful Fleet endpoints."""
    aioclient_mock.get(f"{API}/version", json=version or VERSION_RESPONSE)
    aioclient_mock.get(f"{API}/config", json=config or FREE_CONFIG_RESPONSE)
    aioclient_mock.get(f"{API}/host_summary", json=summary or HOST_SUMMARY_RESPONSE)
    aioclient_mock.get(
        f"{API}/policies",
        json=policies or policies_payload(POLICY_GATEKEEPER, POLICY_BITLOCKER),
    )
    aioclient_mock.get(
        f"{API}/hosts", json=hosts or hosts_payload(HOST_LAPTOP, HOST_DESKTOP)
    )
    aioclient_mock.get(f"{API}/activities", json=activities or activities_payload())
    aioclient_mock.get(
        f"{API}/software/titles", json=software or VULNERABLE_SOFTWARE_RESPONSE
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
