"""Regression tests for the 0.7 code review fixes."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import aiohttp
from homeassistant.helpers import device_registry as dr

from custom_components.fleetdm.const import (
    CONF_PER_HOST_ENTITIES,
    CONF_WEBHOOKS,
    PER_HOST_ENTITY_THRESHOLD,
    PER_HOST_OFF,
)
from custom_components.fleetdm.coordinator import METADATA_REFRESH_EVERY

from .conftest import (
    API,
    HOST_DESKTOP,
    HOST_LAPTOP,
    POLICY_GATEKEEPER,
    VERSION_RESPONSE,
    activities_payload,
    get_device,
    host,
    hosts_payload,
    mock_fleet,
    policies_payload,
    remock_fleet,
)
from .test_drift import failing, poll, setup_with
from .test_hosts import inventory_poll
from .test_webhooks import polled, webhook_activity

EVENT_ENTITY = "event.fleet_fleet_events"


# --- Event entity replay --------------------------------------------------------


async def test_failed_poll_does_not_replay_events(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """A failed refresh must not re-fire the previous cycle's events.

    Coordinators notify listeners when a refresh fails, while their data is
    still the last successful cycle. Replaying on every notification fired
    that cycle's events a second time.
    """
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)
    await poll(
        hass,
        entry,
        aioclient_mock,
        policies=policies_payload(failing(POLICY_GATEKEEPER, 2)),
    )
    fired_at = hass.states.get(EVENT_ENTITY).state

    # Fleet becomes unreachable for one poll.
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/host_summary", exc=TimeoutError())
    await entry.runtime_data.summary.async_refresh()
    await hass.async_block_till_done()
    assert entry.runtime_data.summary.last_update_success is False

    # And comes back with nothing new.
    await poll(
        hass,
        entry,
        aioclient_mock,
        policies=policies_payload(failing(POLICY_GATEKEEPER, 2)),
    )

    assert hass.states.get(EVENT_ENTITY).state == fired_at


async def test_new_cycle_events_still_replayed(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """The guard only skips repeats; a genuinely new transition still fires."""
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)
    before = hass.states.get(EVENT_ENTITY).state

    await poll(
        hass,
        entry,
        aioclient_mock,
        policies=policies_payload(failing(POLICY_GATEKEEPER, 2)),
    )

    state = hass.states.get(EVENT_ENTITY)
    assert state.state != before
    assert state.attributes["event_type"] == "policy_newly_failing"


# --- Hub device version -----------------------------------------------------------


async def test_hub_version_follows_fleet_upgrade(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """A Fleet upgrade reaches the hub device without a restart."""
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)
    assert get_device(hass, entry.entry_id, entry.entry_id).sw_version == "4.52.0"

    entry.runtime_data.summary._polls_since_metadata = METADATA_REFRESH_EVERY - 1
    await poll(
        hass, entry, aioclient_mock, version={**VERSION_RESPONSE, "version": "4.94.0"}
    )

    assert get_device(hass, entry.entry_id, entry.entry_id).sw_version == "4.94.0"


# --- Host devices when per-host entities are off -----------------------------------


async def test_gated_fleet_removes_host_devices(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """Growing past the threshold removes the devices along with the entities."""
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)
    assert get_device(hass, entry.entry_id, f"{entry.entry_id}_host_1") is not None

    many = hosts_payload(
        *(host(i, f"Host {i}") for i in range(1, PER_HOST_ENTITY_THRESHOLD + 2))
    )
    await inventory_poll(hass, entry, aioclient_mock, hosts=many)

    assert get_device(hass, entry.entry_id, f"{entry.entry_id}_host_1") is None
    # The hub stays.
    assert get_device(hass, entry.entry_id, entry.entry_id) is not None


async def test_option_off_removes_host_devices(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """Turning per-host entities off leaves no empty devices behind."""
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)
    hass.config_entries.async_update_entry(
        entry, options={CONF_PER_HOST_ENTITIES: PER_HOST_OFF}
    )
    await hass.async_block_till_done()

    assert get_device(hass, entry.entry_id, f"{entry.entry_id}_host_1") is None
    assert get_device(hass, entry.entry_id, f"{entry.entry_id}_host_2") is None


async def test_device_removable_when_per_host_off(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """A host device can be deleted from the UI once nothing will recreate it."""
    from custom_components.fleetdm import async_remove_config_entry_device

    entry = await setup_with(hass, mock_config_entry, aioclient_mock)
    stale = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("fleetdm", f"{entry.entry_id}_host_1")},
    )
    # Host 1 still exists in Fleet, so while per-host entities are on: no.
    assert await async_remove_config_entry_device(hass, entry, stale) is False

    hass.config_entries.async_update_entry(
        entry, options={CONF_PER_HOST_ENTITIES: PER_HOST_OFF}
    )
    await hass.async_block_till_done()

    assert await async_remove_config_entry_device(hass, entry, stale) is True


# --- API robustness ------------------------------------------------------------------


async def test_html_page_is_an_unexpected_response(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """A 200 text/html page is not reported as an HTTP error."""
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)
    remock_fleet(aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.get(
        f"{API}/host_summary",
        exc=aiohttp.ContentTypeError(Mock(real_url="x"), (), status=200),
    )

    await entry.runtime_data.summary.async_refresh()

    err = entry.runtime_data.summary.last_exception
    assert err.translation_key == "unexpected_response"


async def test_malformed_host_is_skipped(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """One unreadable host no longer takes the whole host list down."""
    broken: dict[str, Any] = {"display_name": "No ID"}
    entry = await setup_with(
        hass,
        mock_config_entry,
        aioclient_mock,
        hosts=hosts_payload(HOST_LAPTOP, broken, HOST_DESKTOP),
    )

    inventory = entry.runtime_data.inventory
    assert inventory.last_update_success is True
    assert sorted(inventory.data.hosts_by_id) == [1, 2]


# --- Recorder ---------------------------------------------------------------------


def test_long_list_attributes_not_recorded() -> None:
    """List attributes with no natural bound stay out of the database."""
    from custom_components.fleetdm.binary_sensor import FleetComplianceBinarySensor
    from custom_components.fleetdm.sensor import (
        FleetHostsUnencryptedSensor,
        FleetOsVersionsSensor,
        FleetSummarySensor,
        FleetVulnerableSoftwareSensor,
    )

    expected = {
        FleetSummarySensor: "policies",
        FleetVulnerableSoftwareSensor: "most_widespread",
        FleetOsVersionsSensor: "versions",
        FleetHostsUnencryptedSensor: "hosts",
        FleetComplianceBinarySensor: "triggering_policies",
    }
    for cls, attribute in expected.items():
        assert attribute in cls._unrecorded_attributes, cls.__name__


# --- Webhook bookkeeping -------------------------------------------------------------


async def test_no_store_writes_until_webhooks_deliver(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """With webhooks on but Fleet silent, polls do not rewrite the store."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_WEBHOOKS: True}
    )
    mock_fleet(aioclient_mock)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    entry = mock_config_entry
    inventory = entry.runtime_data.inventory
    revision = inventory._matcher.revision

    body = webhook_activity()
    await inventory_poll(
        hass, entry, aioclient_mock, activities=activities_payload(polled(50, body))
    )

    # The poll fired the enrollment and kept a sighting in memory, but with no
    # webhook ever delivered that is not a reason to save.
    assert inventory._matcher.revision == revision
    assert len(inventory._matcher.as_json()) == 1
