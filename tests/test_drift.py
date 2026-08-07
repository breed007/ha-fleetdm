"""Tests for compliance drift detection.

These cover the integration's headline feature and its hardest requirement:
exactly one event per transition, no storm on first setup, and no duplicated or
lost events across a Home Assistant restart.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.fleetdm.const import (
    EVENT_POLICY_FAILING,
    EVENT_POLICY_RECOVERED,
    EVENT_TYPE_POLICY_NEWLY_FAILING,
    EVENT_TYPE_POLICY_RECOVERED,
)

from .conftest import (
    POLICY_BITLOCKER,
    POLICY_GATEKEEPER,
    mock_fleet,
    policies_payload,
    remock_fleet,
)


def failing(policy: dict[str, Any], count: int = 1) -> dict[str, Any]:
    """Return a copy of a policy with `count` hosts failing."""
    passing = max(policy["passing_host_count"] - count, 0)
    return {**policy, "failing_host_count": count, "passing_host_count": passing}


async def poll(hass, entry, aioclient_mock, **kwargs: Any) -> None:
    """Re-mock Fleet and run one coordinator cycle."""
    remock_fleet(aioclient_mock, **kwargs)
    await entry.runtime_data.summary.async_refresh()
    await hass.async_block_till_done()


async def setup_with(hass, entry, aioclient_mock, **kwargs: Any):
    """Set up the entry against a specific Fleet state."""
    mock_fleet(aioclient_mock, **kwargs)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_first_poll_seeds_silently(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """Setting up against an already-failing fleet must not fire an event storm."""
    events = async_capture_events(hass, EVENT_POLICY_FAILING)

    await setup_with(
        hass,
        mock_config_entry,
        aioclient_mock,
        policies=policies_payload(
            failing(POLICY_GATEKEEPER, 3), failing(POLICY_BITLOCKER, 2)
        ),
    )

    assert events == []
    # The state is still reported correctly; only the notifications are withheld.
    assert hass.states.get("sensor.fleet_policies_failing").state == "2"
    assert hass.states.get("binary_sensor.fleet_compliance").state == "on"


async def test_transition_fires_once(hass, aioclient_mock, mock_config_entry) -> None:
    """A policy that starts failing fires exactly one event, then stays quiet."""
    events = async_capture_events(hass, EVENT_POLICY_FAILING)
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)
    assert events == []

    await poll(
        hass,
        entry,
        aioclient_mock,
        policies=policies_payload(POLICY_GATEKEEPER, failing(POLICY_BITLOCKER, 2)),
    )
    assert len(events) == 1
    assert events[0].data["policy_id"] == 2
    assert events[0].data["policy_name"] == "Windows disks encrypted"
    assert events[0].data["failing_host_count"] == 2
    assert events[0].data["critical"] is True
    assert events[0].data["resolution"] == "Turn on BitLocker"

    # Still failing across several more polls: no repeats.
    for count in (2, 3, 3):
        await poll(
            hass,
            entry,
            aioclient_mock,
            policies=policies_payload(
                POLICY_GATEKEEPER, failing(POLICY_BITLOCKER, count)
            ),
        )
    assert len(events) == 1


async def test_recovery_fires_once(hass, aioclient_mock, mock_config_entry) -> None:
    """A policy returning to passing fires a single recovery event."""
    recovered = async_capture_events(hass, EVENT_POLICY_RECOVERED)
    entry = await setup_with(
        hass,
        mock_config_entry,
        aioclient_mock,
        policies=policies_payload(POLICY_GATEKEEPER, failing(POLICY_BITLOCKER, 2)),
    )

    await poll(hass, entry, aioclient_mock)
    assert len(recovered) == 1
    assert recovered[0].data["policy_id"] == 2
    assert recovered[0].data["failing_host_count"] == 0

    await poll(hass, entry, aioclient_mock)
    assert len(recovered) == 1


async def test_no_events_across_restart_when_state_unchanged(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """Restarting Home Assistant must not re-fire for already-failing policies."""
    failing_state = policies_payload(POLICY_GATEKEEPER, failing(POLICY_BITLOCKER, 2))
    entry = await setup_with(
        hass, mock_config_entry, aioclient_mock, policies=failing_state
    )

    # Establish a real transition so the baseline is not just the seed.
    events = async_capture_events(hass, EVENT_POLICY_FAILING)
    await poll(
        hass,
        entry,
        aioclient_mock,
        policies=policies_payload(
            failing(POLICY_GATEKEEPER, 1), failing(POLICY_BITLOCKER, 2)
        ),
    )
    assert len(events) == 1

    # Simulate a restart: unload and set up again against unchanged Fleet state.
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    remock_fleet(
        aioclient_mock,
        policies=policies_payload(
            failing(POLICY_GATEKEEPER, 1), failing(POLICY_BITLOCKER, 2)
        ),
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert len(events) == 1


async def test_transition_during_downtime_is_detected(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """A policy that starts failing while HA is down still fires on restart."""
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    events = async_capture_events(hass, EVENT_POLICY_FAILING)
    remock_fleet(
        aioclient_mock,
        policies=policies_payload(POLICY_GATEKEEPER, failing(POLICY_BITLOCKER, 5)),
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["policy_id"] == 2
    assert events[0].data["failing_host_count"] == 5


async def test_deleted_failing_policy_does_not_report_recovery(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """Deleting a failing policy in Fleet is not a recovery."""
    recovered = async_capture_events(hass, EVENT_POLICY_RECOVERED)
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)

    await poll(
        hass,
        entry,
        aioclient_mock,
        policies=policies_payload(POLICY_GATEKEEPER, failing(POLICY_BITLOCKER, 2)),
    )

    # The policy disappears from Fleet entirely.
    await poll(
        hass, entry, aioclient_mock, policies=policies_payload(POLICY_GATEKEEPER)
    )
    assert recovered == []

    # And it is not resurrected as a phantom transition later either.
    await poll(
        hass, entry, aioclient_mock, policies=policies_payload(POLICY_GATEKEEPER)
    )
    assert recovered == []


async def test_multiple_transitions_in_one_poll(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """Two policies flipping in the same cycle produce two distinct bus events."""
    events = async_capture_events(hass, EVENT_POLICY_FAILING)
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)

    await poll(
        hass,
        entry,
        aioclient_mock,
        policies=policies_payload(
            failing(POLICY_GATEKEEPER, 1), failing(POLICY_BITLOCKER, 2)
        ),
    )

    assert len(events) == 2
    assert {event.data["policy_id"] for event in events} == {1, 2}


async def test_event_entity_reflects_drift(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """The event entity records the transition type."""
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)

    await poll(
        hass,
        entry,
        aioclient_mock,
        policies=policies_payload(POLICY_GATEKEEPER, failing(POLICY_BITLOCKER, 2)),
    )
    state = hass.states.get("event.fleet_fleet_events")
    assert state.attributes["event_type"] == EVENT_TYPE_POLICY_NEWLY_FAILING
    assert state.attributes["policy_name"] == "Windows disks encrypted"

    await poll(hass, entry, aioclient_mock)
    state = hass.states.get("event.fleet_fleet_events")
    assert state.attributes["event_type"] == EVENT_TYPE_POLICY_RECOVERED


async def test_drift_state_cleared_on_entry_removal(
    hass, aioclient_mock, mock_config_entry, hass_storage
) -> None:
    """Removing the entry deletes its persisted drift state."""
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)
    key = f"fleetdm.drift.{entry.entry_id}"
    assert key in hass_storage

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert hass_storage.get(key) is None
