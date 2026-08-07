"""Tests for host enrolment and host-missing events.

These carry the same guarantees as policy drift: no storm when the integration
is first added, exactly one event per transition, and nothing duplicated or lost
across a Home Assistant restart.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.fleetdm.const import (
    CONF_MISSING_AFTER_HOURS,
    EVENT_HOST_ENROLLED,
    EVENT_HOST_MISSING,
    EVENT_TYPE_HOST_ENROLLED,
)

from .conftest import (
    HOST_LAPTOP,
    activities_payload,
    enrolment_activity,
    host,
    hosts_payload,
    mock_fleet,
)
from .test_drift import setup_with
from .test_hosts import inventory_poll


async def test_first_poll_seeds_enrolments_silently(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """Existing enrolment activity must not fire events at setup."""
    events = async_capture_events(hass, EVENT_HOST_ENROLLED)

    await setup_with(
        hass,
        mock_config_entry,
        aioclient_mock,
        activities=activities_payload(
            enrolment_activity(10, 1, "Ada Laptop"),
            enrolment_activity(11, 2, "Grace Desktop"),
        ),
    )

    assert events == []


async def test_new_enrolment_fires_once(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """A host enrolling after setup fires exactly one event."""
    events = async_capture_events(hass, EVENT_HOST_ENROLLED)
    entry = await setup_with(
        hass,
        mock_config_entry,
        aioclient_mock,
        activities=activities_payload(enrolment_activity(10, 1, "Ada Laptop")),
    )
    assert events == []

    await inventory_poll(
        hass,
        entry,
        aioclient_mock,
        activities=activities_payload(
            enrolment_activity(10, 1, "Ada Laptop"),
            enrolment_activity(11, 3, "Carol Server"),
        ),
    )

    assert len(events) == 1
    assert events[0].data["host_id"] == 3
    assert events[0].data["host_name"] == "Carol Server"
    assert events[0].data["host_serial"] == "SERIAL3"

    # The watermark stops the same activity firing again.
    await inventory_poll(
        hass,
        entry,
        aioclient_mock,
        activities=activities_payload(
            enrolment_activity(10, 1, "Ada Laptop"),
            enrolment_activity(11, 3, "Carol Server"),
        ),
    )
    assert len(events) == 1


async def test_non_enrolment_activities_ignored(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """Unrelated audit entries advance the watermark but fire nothing."""
    events = async_capture_events(hass, EVENT_HOST_ENROLLED)
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)

    await inventory_poll(
        hass,
        entry,
        aioclient_mock,
        activities=activities_payload(
            {"id": 20, "type": "user_logged_in", "created_at": None, "details": {}},
            {"id": 21, "type": "live_query", "created_at": None, "details": {}},
        ),
    )

    assert events == []


async def test_host_going_missing_fires_once(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """A host crossing the threshold fires one event, then stays quiet."""
    events = async_capture_events(hass, EVENT_HOST_MISSING)
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_MISSING_AFTER_HOURS: 4}
    )
    mock_fleet(aioclient_mock, hosts=hosts_payload(host(1, "Ada Laptop")))
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert events == []

    # The same host, now well past the threshold.
    await inventory_poll(
        hass,
        mock_config_entry,
        aioclient_mock,
        hosts=hosts_payload(host(1, "Ada Laptop", seen_hours_ago=30)),
    )
    assert len(events) == 1
    assert events[0].data["host_id"] == 1
    assert events[0].data["host_name"] == "Ada Laptop"
    assert events[0].data["unseen_hours"] >= 4

    # Still missing on later polls: no repeat.
    for _ in range(2):
        await inventory_poll(
            hass,
            mock_config_entry,
            aioclient_mock,
            hosts=hosts_payload(host(1, "Ada Laptop", seen_hours_ago=40)),
        )
    assert len(events) == 1


async def test_already_missing_host_seeds_silently(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """Adding the integration to a fleet with stale hosts fires nothing."""
    events = async_capture_events(hass, EVENT_HOST_MISSING)

    await setup_with(
        hass,
        mock_config_entry,
        aioclient_mock,
        hosts=hosts_payload(
            host(1, "Ada Laptop", seen_hours_ago=200),
            host(2, "Grace Desktop", seen_hours_ago=300),
        ),
    )

    assert events == []
    # The state is still reported: only the notification is withheld.
    assert hass.states.get("binary_sensor.ada_laptop_missing").state == "on"


async def test_missing_survives_restart_without_duplicates(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """Restarting must not re-fire for a host that was already missing."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_MISSING_AFTER_HOURS: 4}
    )
    mock_fleet(aioclient_mock, hosts=hosts_payload(host(1, "Ada Laptop")))
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    events = async_capture_events(hass, EVENT_HOST_MISSING)
    stale = hosts_payload(host(1, "Ada Laptop", seen_hours_ago=30))
    await inventory_poll(hass, mock_config_entry, aioclient_mock, hosts=stale)
    assert len(events) == 1

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    aioclient_mock.clear_requests()
    mock_fleet(
        aioclient_mock, hosts=hosts_payload(host(1, "Ada Laptop", seen_hours_ago=40))
    )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert len(events) == 1


async def test_deleted_missing_host_fires_nothing(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """A host deleted from Fleet is gone, not newly missing."""
    events = async_capture_events(hass, EVENT_HOST_MISSING)
    entry = await setup_with(
        hass, mock_config_entry, aioclient_mock, hosts=hosts_payload(HOST_LAPTOP)
    )

    await inventory_poll(hass, entry, aioclient_mock, hosts=hosts_payload())
    assert events == []


async def test_event_entity_records_enrolment(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """Host events land on the same event entity as policy drift."""
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)

    await inventory_poll(
        hass,
        entry,
        aioclient_mock,
        activities=activities_payload(enrolment_activity(50, 9, "New Host")),
    )

    state = hass.states.get("event.fleet_fleet_events")
    assert state.attributes["event_type"] == EVENT_TYPE_HOST_ENROLLED
    assert state.attributes["host_name"] == "New Host"


async def test_inventory_state_cleared_on_entry_removal(
    hass, aioclient_mock, mock_config_entry, hass_storage
) -> None:
    """Removing the entry deletes both persisted baselines."""
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)
    keys = (
        f"fleetdm.drift.{entry.entry_id}",
        f"fleetdm.inventory.{entry.entry_id}",
    )
    for key in keys:
        assert key in hass_storage

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    for key in keys:
        assert hass_storage.get(key) is None
