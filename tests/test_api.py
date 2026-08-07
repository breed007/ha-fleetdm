"""Unit tests for the Fleet API client helpers."""

from __future__ import annotations

import pytest
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.fleetdm.api import (
    MAX_PAGES,
    POLICIES_PER_PAGE,
    FleetClient,
    FleetError,
    FleetHostSummary,
    FleetPolicy,
    normalize_url,
)

from .conftest import API, BASE_URL


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://fleet.example.com", "https://fleet.example.com"),
        ("https://fleet.example.com/", "https://fleet.example.com"),
        ("https://fleet.example.com///", "https://fleet.example.com"),
        ("  https://fleet.example.com  ", "https://fleet.example.com"),
        ("fleet.example.com", "https://fleet.example.com"),
        ("http://fleet.local:8080", "http://fleet.local:8080"),
        # A Fleet server behind a subpath keeps its path.
        ("https://example.com/fleet/", "https://example.com/fleet"),
    ],
)
def test_normalize_url(raw: str, expected: str) -> None:
    """URLs normalise to a stable unique ID."""
    assert normalize_url(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "/"])
def test_normalize_url_rejects_empty(raw: str) -> None:
    """An empty URL is rejected rather than producing a bogus unique ID."""
    with pytest.raises(ValueError, match="must not be empty"):
        normalize_url(raw)


def test_policy_from_json_minimal() -> None:
    """A policy payload with only an ID parses with safe defaults."""
    policy = FleetPolicy.from_json({"id": 7})

    assert policy.id == 7
    assert policy.name == "Policy 7"
    assert policy.critical is False
    assert policy.passing_host_count == 0
    assert policy.failing_host_count == 0
    assert policy.is_failing is False
    assert policy.host_count_updated_at is None


def test_policy_from_json_handles_null_fields() -> None:
    """Explicit nulls are coerced rather than crashing."""
    policy = FleetPolicy.from_json(
        {
            "id": 8,
            "name": "Firewall on",
            "description": None,
            "resolution": None,
            "platform": None,
            "passing_host_count": None,
            "failing_host_count": 4,
        }
    )

    assert policy.description == ""
    assert policy.platform == ""
    assert policy.passing_host_count == 0
    assert policy.is_failing is True


def test_host_summary_prefers_new_missing_field() -> None:
    """missing_30_days_count wins over the legacy mia_count."""
    summary = FleetHostSummary.from_json(
        {
            "totals_hosts_count": 10,
            "online_count": 7,
            "offline_count": 2,
            "missing_30_days_count": 1,
            "mia_count": 99,
            "new_count": 3,
        }
    )

    assert summary.missing == 1
    assert summary.total == 10
    assert summary.new == 3


def test_host_summary_defaults_to_zero() -> None:
    """An empty summary payload yields zeros, not errors."""
    summary = FleetHostSummary.from_json({})

    assert (summary.total, summary.online, summary.offline) == (0, 0, 0)
    assert (summary.missing, summary.new) == (0, 0)


async def test_policies_paginate(hass, aioclient_mock) -> None:
    """A policy library larger than one page is read in full."""

    def page(start: int, count: int) -> dict:
        return {
            "policies": [
                {"id": start + i, "name": f"Policy {start + i}"} for i in range(count)
            ]
        }

    # Page 0 is full, page 1 is short: the short page ends the loop.
    aioclient_mock.get(
        f"{API}/policies",
        params={"page": "0", "per_page": str(POLICIES_PER_PAGE)},
        json=page(0, POLICIES_PER_PAGE),
    )
    aioclient_mock.get(
        f"{API}/policies",
        params={"page": "1", "per_page": str(POLICIES_PER_PAGE)},
        json=page(POLICIES_PER_PAGE, 5),
    )

    client = FleetClient(async_get_clientsession(hass), BASE_URL, "token")
    policies = await client.async_get_global_policies()

    assert len(policies) == POLICIES_PER_PAGE + 5
    assert policies[0].id == 0
    assert policies[-1].id == POLICIES_PER_PAGE + 4


async def test_policies_pagination_safety_cap(hass, aioclient_mock, caplog) -> None:
    """A server that never returns a short page is stopped at the cap."""
    aioclient_mock.get(
        f"{API}/policies",
        json={
            "policies": [
                {"id": i, "name": f"Policy {i}"} for i in range(POLICIES_PER_PAGE)
            ]
        },
    )

    client = FleetClient(async_get_clientsession(hass), BASE_URL, "token")
    policies = await client.async_get_global_policies()

    assert len(policies) == MAX_PAGES * POLICIES_PER_PAGE
    assert "safety cap" in caplog.text


async def test_policies_falls_back_to_legacy_path(hass, aioclient_mock) -> None:
    """An older Fleet that only has /global/policies still works."""
    aioclient_mock.get(f"{API}/policies", status=404)
    aioclient_mock.get(
        f"{API}/global/policies",
        json={"policies": [{"id": 1, "name": "Gatekeeper enabled"}]},
    )

    client = FleetClient(async_get_clientsession(hass), BASE_URL, "token")
    policies = await client.async_get_global_policies()

    assert [p.name for p in policies] == ["Gatekeeper enabled"]


async def test_resolved_policy_path_is_cached(hass, aioclient_mock) -> None:
    """The working route is probed once, not re-probed on every poll."""
    aioclient_mock.get(f"{API}/policies", status=404)
    aioclient_mock.get(f"{API}/global/policies", json={"policies": []})

    client = FleetClient(async_get_clientsession(hass), BASE_URL, "token")
    await client.async_get_global_policies()
    calls_after_first = len(aioclient_mock.mock_calls)

    await client.async_get_global_policies()
    # Second fetch is a single request: no repeat probe of the dead route.
    assert len(aioclient_mock.mock_calls) - calls_after_first == 1


async def test_no_policy_endpoint_raises(hass, aioclient_mock) -> None:
    """If no known route exists, the error names what was tried."""
    aioclient_mock.get(f"{API}/policies", status=404)
    aioclient_mock.get(f"{API}/global/policies", status=404)

    client = FleetClient(async_get_clientsession(hass), BASE_URL, "token")
    with pytest.raises(FleetError, match="Could not find a policies endpoint"):
        await client.async_get_global_policies()
