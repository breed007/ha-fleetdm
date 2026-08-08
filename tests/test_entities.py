"""Tests for dynamic policy entities and Free/Premium degradation."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er

from custom_components.fleetdm.const import DOMAIN

from .conftest import (
    API,
    FREE_CONFIG_RESPONSE,
    HOST_LAPTOP,
    POLICY_BITLOCKER,
    POLICY_GATEKEEPER,
    PREMIUM_CONFIG_RESPONSE,
    VULNERABLE_SOFTWARE_RESPONSE,
    activities_payload,
    hosts_payload,
    labels_payload,
    policies_payload,
)
from .test_drift import failing, poll, setup_with

POLICY_FIREWALL = {
    "id": 3,
    "name": "Firewall enabled",
    "query": "SELECT 1 FROM alf WHERE global_state >= 1;",
    "description": "Checks the macOS application firewall",
    "critical": False,
    "team_id": None,
    "resolution": "Turn on the firewall",
    "platform": "darwin",
    "passing_host_count": 8,
    "failing_host_count": 0,
    "host_count_updated_at": "2025-01-20T15:23:57Z",
}


async def test_policy_entities_created(hass, setup_integration) -> None:
    """Each global policy gets a problem binary sensor."""
    gatekeeper = hass.states.get("binary_sensor.fleet_gatekeeper_enabled")
    assert gatekeeper is not None
    assert gatekeeper.state == "off"
    assert gatekeeper.attributes["policy_id"] == 1
    assert gatekeeper.attributes["passing_host_count"] == 8
    assert gatekeeper.attributes["device_class"] == "problem"
    assert gatekeeper.attributes["host_count_updated_at"] == "2025-01-20T15:23:57Z"

    assert hass.states.get("binary_sensor.fleet_windows_disks_encrypted") is not None


async def test_policy_failing_sensor_disabled_by_default(
    hass, setup_integration
) -> None:
    """The per-policy count sensor is registered but not enabled."""
    registry = er.async_get(hass)
    unique_id = f"{setup_integration.entry_id}_policy_1_failing_hosts"
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)

    assert entity_id == "sensor.fleet_gatekeeper_enabled_failing_hosts"
    assert (
        registry.async_get(entity_id).disabled_by
        is er.RegistryEntryDisabler.INTEGRATION
    )
    assert hass.states.get(entity_id) is None


async def test_new_policy_adds_entity(hass, aioclient_mock, mock_config_entry) -> None:
    """A policy created in Fleet appears without reloading the integration."""
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)
    assert hass.states.get("binary_sensor.fleet_firewall_enabled") is None

    await poll(
        hass,
        entry,
        aioclient_mock,
        policies=policies_payload(POLICY_GATEKEEPER, POLICY_BITLOCKER, POLICY_FIREWALL),
    )

    assert hass.states.get("binary_sensor.fleet_firewall_enabled") is not None


async def test_deleted_policy_removes_entity(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """A policy deleted in Fleet has its entity purged from the registry."""
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)
    registry = er.async_get(hass)
    unique_id = f"{entry.entry_id}_policy_2_compliance"
    assert registry.async_get_entity_id("binary_sensor", DOMAIN, unique_id)

    await poll(
        hass, entry, aioclient_mock, policies=policies_payload(POLICY_GATEKEEPER)
    )

    assert registry.async_get_entity_id("binary_sensor", DOMAIN, unique_id) is None
    assert hass.states.get("binary_sensor.fleet_windows_disks_encrypted") is None


async def test_policy_rename_follows(hass, aioclient_mock, mock_config_entry) -> None:
    """Renaming a policy in Fleet updates the name but keeps the entity."""
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)
    entity_id = "binary_sensor.fleet_gatekeeper_enabled"
    assert hass.states.get(entity_id).attributes["friendly_name"] == (
        "Fleet Gatekeeper enabled"
    )

    renamed = {**POLICY_GATEKEEPER, "name": "Gatekeeper must be on"}
    await poll(
        hass,
        entry,
        aioclient_mock,
        policies=policies_payload(renamed, POLICY_BITLOCKER),
    )

    state = hass.states.get(entity_id)
    # Same entity (IDs key on the stable Fleet policy ID), new display name.
    assert state is not None
    assert state.attributes["friendly_name"] == "Fleet Gatekeeper must be on"


async def test_free_tier_compliance_uses_all_policies(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """On Free tier there is no critical flag, so any failure is a problem."""
    await setup_with(
        hass,
        mock_config_entry,
        aioclient_mock,
        config=FREE_CONFIG_RESPONSE,
        policies=policies_payload(failing(POLICY_GATEKEEPER, 1), POLICY_BITLOCKER),
    )

    state = hass.states.get("binary_sensor.fleet_compliance")
    assert state.state == "on"
    assert state.attributes["basis"] == "all_policies"
    assert state.attributes["premium"] is False
    assert state.attributes["triggering_policies"] == ["Gatekeeper enabled"]


async def test_premium_compliance_only_tracks_critical(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """On Premium the compliance sensor watches only critical policies."""
    entry = await setup_with(
        hass,
        mock_config_entry,
        aioclient_mock,
        config=PREMIUM_CONFIG_RESPONSE,
        policies=policies_payload(failing(POLICY_GATEKEEPER, 1), POLICY_BITLOCKER),
    )

    state = hass.states.get("binary_sensor.fleet_compliance")
    assert state.state == "off"
    assert state.attributes["basis"] == "critical_policies"
    assert state.attributes["failing_policy_count"] == 1

    # The critical policy failing does trip it.
    await poll(
        hass,
        entry,
        aioclient_mock,
        config=PREMIUM_CONFIG_RESPONSE,
        policies=policies_payload(
            failing(POLICY_GATEKEEPER, 1), failing(POLICY_BITLOCKER, 1)
        ),
    )
    state = hass.states.get("binary_sensor.fleet_compliance")
    assert state.state == "on"
    assert state.attributes["triggering_policies"] == ["Windows disks encrypted"]


async def test_policy_without_critical_field(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """A Free-tier payload with no `critical` key parses without error."""
    bare: dict[str, Any] = {
        key: value
        for key, value in POLICY_GATEKEEPER.items()
        if key not in ("critical", "resolution", "description")
    }
    await setup_with(
        hass,
        mock_config_entry,
        aioclient_mock,
        config=FREE_CONFIG_RESPONSE,
        policies=policies_payload(bare),
    )

    state = hass.states.get("binary_sensor.fleet_gatekeeper_enabled")
    assert state is not None
    assert state.attributes["critical"] is False


async def test_forbidden_config_endpoint_degrades_to_free(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """An Observer token that cannot read /config still sets up, as Free."""
    aioclient_mock.get(f"{API}/version", json={"version": "4.52.0"})
    aioclient_mock.get(f"{API}/config", status=403)
    aioclient_mock.get(
        f"{API}/host_summary",
        json={
            "totals_hosts_count": 15,
            "online_count": 12,
            "offline_count": 2,
            "missing_30_days_count": 1,
            "new_count": 0,
        },
    )
    aioclient_mock.get(
        f"{API}/policies",
        json=policies_payload(failing(POLICY_GATEKEEPER, 1)),
    )
    aioclient_mock.get(f"{API}/hosts", json=hosts_payload(HOST_LAPTOP))
    aioclient_mock.get(f"{API}/activities", json=activities_payload())
    aioclient_mock.get(f"{API}/software/titles", json=VULNERABLE_SOFTWARE_RESPONSE)
    aioclient_mock.get(f"{API}/labels", json=labels_payload())
    mock_config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    state = hass.states.get("binary_sensor.fleet_compliance")
    assert state.state == "on"
    assert state.attributes["premium"] is False


async def test_legacy_mia_count_fallback(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """Older servers that only emit mia_count still populate the missing sensor."""
    await setup_with(
        hass,
        mock_config_entry,
        aioclient_mock,
        summary={
            "totals_hosts_count": 15,
            "online_count": 12,
            "offline_count": 2,
            "mia_count": 1,
            "new_count": 0,
        },
    )

    assert hass.states.get("sensor.fleet_hosts_missing").state == "1"


async def test_policies_failing_attributes(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """The failing-policies sensor lists offenders worst-first."""
    await setup_with(
        hass,
        mock_config_entry,
        aioclient_mock,
        policies=policies_payload(
            failing(POLICY_GATEKEEPER, 1), failing(POLICY_BITLOCKER, 3)
        ),
    )

    state = hass.states.get("sensor.fleet_policies_failing")
    assert state.state == "2"
    names = [policy["name"] for policy in state.attributes["policies"]]
    assert names == ["Windows disks encrypted", "Gatekeeper enabled"]


async def test_coordinator_recovers_after_failure(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """A transient server error marks entities unavailable, then recovers."""
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)
    assert hass.states.get("sensor.fleet_hosts_online").state == "12"

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/host_summary", exc=TimeoutError())
    aioclient_mock.get(f"{API}/policies", exc=TimeoutError())
    await entry.runtime_data.summary.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.fleet_hosts_online").state == "unavailable"

    await poll(hass, entry, aioclient_mock)
    assert hass.states.get("sensor.fleet_hosts_online").state == "12"
