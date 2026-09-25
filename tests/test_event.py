"""Tests for the event entity's dual-coordinator wiring.

The entity carries policy drift from the fast summary cycle and host events
from the slow inventory cycle. That split was previously only covered
indirectly, and its availability ignored the inventory coordinator entirely.
"""

from __future__ import annotations

from custom_components.fleetdm.const import (
    EVENT_TYPE_HOST_ENROLLED,
    EVENT_TYPE_POLICY_NEWLY_FAILING,
    EVENT_TYPES,
)

from .conftest import (
    API,
    POLICY_BITLOCKER,
    POLICY_GATEKEEPER,
    activities_payload,
    enrollment_activity,
    mock_fleet,
    policies_payload,
)
from .test_drift import failing, poll, setup_with
from .test_hosts import inventory_poll

ENTITY = "event.fleet_fleet_events"


async def test_event_entity_declares_all_types(hass, setup_integration) -> None:
    """Every event type the integration can fire is declared on the entity."""
    state = hass.states.get(ENTITY)
    assert state is not None
    assert set(state.attributes["event_types"]) == set(EVENT_TYPES)


async def test_carries_events_from_both_coordinators(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """Policy drift and host enrollment land on the same timeline."""
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)

    await poll(
        hass,
        entry,
        aioclient_mock,
        policies=policies_payload(POLICY_GATEKEEPER, failing(POLICY_BITLOCKER, 2)),
    )
    assert (
        hass.states.get(ENTITY).attributes["event_type"]
        == EVENT_TYPE_POLICY_NEWLY_FAILING
    )

    await inventory_poll(
        hass,
        entry,
        aioclient_mock,
        activities=activities_payload(enrollment_activity(77, 9, "New Host")),
    )
    assert hass.states.get(ENTITY).attributes["event_type"] == EVENT_TYPE_HOST_ENROLLED


async def test_unavailable_when_inventory_fails(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """A failing inventory coordinator must not look healthy.

    Regression: availability came from the summary coordinator alone, so host
    events could stop arriving while the entity still reported available.
    """
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)
    assert hass.states.get(ENTITY).state != "unavailable"

    aioclient_mock.clear_requests()
    mock_fleet(aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/hosts", exc=TimeoutError())
    aioclient_mock.get(f"{API}/activities", exc=TimeoutError())
    aioclient_mock.get(f"{API}/software/titles", exc=TimeoutError())
    aioclient_mock.get(f"{API}/labels", exc=TimeoutError())
    await entry.runtime_data.inventory.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY).state == "unavailable"


async def test_unavailable_when_summary_fails(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """The summary coordinator failing also marks the entity unavailable."""
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/host_summary", exc=TimeoutError())
    aioclient_mock.get(f"{API}/policies", exc=TimeoutError())
    await entry.runtime_data.summary.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY).state == "unavailable"


async def test_recovers_when_both_healthy_again(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """Availability returns once the failing coordinator recovers."""
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/hosts", exc=TimeoutError())
    aioclient_mock.get(f"{API}/activities", exc=TimeoutError())
    aioclient_mock.get(f"{API}/software/titles", exc=TimeoutError())
    aioclient_mock.get(f"{API}/labels", exc=TimeoutError())
    await entry.runtime_data.inventory.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == "unavailable"

    await inventory_poll(hass, entry, aioclient_mock)
    assert hass.states.get(ENTITY).state != "unavailable"
