"""Tests for receiving Fleet's webhooks.

Payload shapes follow Fleet's own source (server/activity and server/webhooks,
v4.92.1): the activities webhook sends timestamp, actor fields, type and the
same details it stores, but no activity ID; the failing policies webhook sends
a timestamp, the full policy and the hosts newly failing it.
"""

from __future__ import annotations

import re
from datetime import timedelta
from http import HTTPStatus
from typing import Any

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.fleetdm.const import (
    CONF_ACTIVITY_EVENTS,
    CONF_API_TOKEN,
    CONF_URL,
    CONF_VERIFY_SSL,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOKS,
    DOMAIN,
    EVENT_ACTIVITY,
    EVENT_HOST_ENROLLED,
    EVENT_POLICY_HOSTS_FAILING,
)
from custom_components.fleetdm.diagnostics import async_get_config_entry_diagnostics

from .conftest import BASE_URL, activities_payload, enrollment_activity, mock_fleet
from .test_hosts import inventory_poll

ENROLLED_AT = "2026-08-07T12:00:00Z"


def webhook_activity(
    activity_type: str = "fleet_enrolled",
    details: dict[str, Any] | None = None,
    timestamp: str = ENROLLED_AT,
) -> dict[str, Any]:
    """Build an activities webhook body, as Fleet's fireActivityWebhook does."""
    if details is None:
        details = {
            "host_id": 9,
            "host_display_name": "New Host",
            "host_serial": "SERIAL9",
        }
    return {
        "timestamp": timestamp,
        "actor_full_name": None,
        "actor_id": None,
        "actor_email": None,
        "type": activity_type,
        "details": details,
    }


def polled(activity_id: int, body: dict[str, Any], created_at: str | None = None):
    """Return the same activity as the polled feed has it, with its ID."""
    return {
        "id": activity_id,
        "type": body["type"],
        "created_at": created_at or body["timestamp"],
        "details": body["details"],
    }


FAILING_POLICY_BODY = {
    "timestamp": "2026-09-25T12:00:00.123456789Z",
    "policy": {
        "id": 101,
        "name": "Gatekeeper enabled",
        "query": "SELECT 1 FROM gatekeeper WHERE assessments_enabled = 1;",
        "critical": True,
        "description": "Gatekeeper blocks unsigned apps.",
        "resolution": "Turn Gatekeeper on in System Settings.",
        "platform": "darwin",
        "passing_host_count": 10,
        "failing_host_count": 2,
    },
    "hosts": [
        {
            "id": 1,
            "hostname": "ada-laptop.local",
            "display_name": "Ada Laptop",
            "url": "https://fleet.example.com/hosts/1",
        },
        {
            "id": 7,
            "hostname": "carol-mbp.local",
            "display_name": "Carol MBP",
            "url": "https://fleet.example.com/hosts/7",
        },
    ],
}


@pytest.fixture
async def webhook_entry(hass, aioclient_mock, mock_config_entry) -> MockConfigEntry:
    """Set up an entry with webhooks turned on."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_WEBHOOKS: True}
    )
    mock_fleet(aioclient_mock)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry


@pytest.fixture
async def post(hass, hass_client_no_auth):
    """Post a body to an entry's webhook and return the HTTP status."""
    clients = []

    async def _post(entry: MockConfigEntry, body: Any, *, raw: str | None = None):
        # Created on first use: HTTP only exists once an entry has set up.
        if not clients:
            clients.append(await hass_client_no_auth())
        client = clients[0]
        url = f"/api/webhook/{entry.data[CONF_WEBHOOK_ID]}"
        if raw is not None:
            response = await client.post(url, data=raw)
        else:
            response = await client.post(url, json=body)
        await hass.async_block_till_done()
        return response.status

    return _post


# --- Setup and configuration -------------------------------------------------


async def test_existing_entry_migrated_with_webhook_id(hass, setup_integration) -> None:
    """Entries from before 0.6 gain a webhook ID when they load."""
    assert setup_integration.minor_version == 2
    assert re.fullmatch(r"[0-9a-f]{64}", setup_integration.data[CONF_WEBHOOK_ID])


async def test_newer_entry_version_refused(hass, aioclient_mock) -> None:
    """An entry written by a future major version is not guessed at."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        unique_id=BASE_URL,
        data={CONF_URL: BASE_URL, CONF_API_TOKEN: "t", CONF_VERIFY_SSL: True},
    )
    entry.add_to_hass(hass)
    mock_fleet(aioclient_mock)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.MIGRATION_ERROR


async def test_new_entry_gets_webhook_id(hass, aioclient_mock) -> None:
    """A freshly configured entry has its webhook ID from the start."""
    mock_fleet(aioclient_mock)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_URL: BASE_URL, CONF_API_TOKEN: "test-token", CONF_VERIFY_SSL: True},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert re.fullmatch(r"[0-9a-f]{64}", result["data"][CONF_WEBHOOK_ID])
    assert result["result"].minor_version == 2


async def test_options_form_shows_webhook_url(hass, setup_integration) -> None:
    """The URL to paste into Fleet is on the options form."""
    result = await hass.config_entries.options.async_init(setup_integration.entry_id)

    url = result["description_placeholders"]["webhook_url"]
    assert url.endswith(f"/api/webhook/{setup_integration.data[CONF_WEBHOOK_ID]}")


async def test_webhook_off_by_default(
    hass, aioclient_mock, setup_integration, post
) -> None:
    """Without the option, deliveries fire nothing and no sensor exists."""
    events = async_capture_events(hass, EVENT_HOST_ENROLLED)

    await post(setup_integration, webhook_activity())

    assert events == []
    assert hass.states.get("sensor.fleet_last_webhook_received") is None


async def test_webhook_stops_on_unload(hass, webhook_entry, post) -> None:
    """An unloaded entry no longer accepts deliveries."""
    events = async_capture_events(hass, EVENT_HOST_ENROLLED)
    assert await hass.config_entries.async_unload(webhook_entry.entry_id)
    await hass.async_block_till_done()

    await post(webhook_entry, webhook_activity())

    assert events == []


async def test_only_post_accepted(hass, webhook_entry, hass_client_no_auth) -> None:
    """Fleet only ever posts; anything else is refused."""
    client = await hass_client_no_auth()
    response = await client.get(f"/api/webhook/{webhook_entry.data[CONF_WEBHOOK_ID]}")
    assert response.status == HTTPStatus.METHOD_NOT_ALLOWED


# --- Activities --------------------------------------------------------------


async def test_enrollment_fires_immediately(hass, webhook_entry, post) -> None:
    """A pushed enrollment fires without waiting for the inventory poll."""
    events = async_capture_events(hass, EVENT_HOST_ENROLLED)

    assert await post(webhook_entry, webhook_activity()) == HTTPStatus.OK

    assert len(events) == 1
    assert events[0].data["host_id"] == 9
    assert events[0].data["host_name"] == "New Host"
    assert events[0].data["source"] == "webhook"
    # Fleet assigns the ID only when it stores the activity, after sending.
    assert events[0].data["activity_id"] is None

    state = hass.states.get("event.fleet_fleet_events")
    assert state.attributes["event_type"] == "host_enrolled"


async def test_webhook_then_poll_fires_once(
    hass, aioclient_mock, webhook_entry, post
) -> None:
    """The poll's later sighting of a pushed activity is not an event."""
    events = async_capture_events(hass, EVENT_HOST_ENROLLED)
    body = webhook_activity()

    await post(webhook_entry, body)
    await inventory_poll(
        hass,
        webhook_entry,
        aioclient_mock,
        activities=activities_payload(polled(50, body)),
    )

    assert len(events) == 1


async def test_poll_then_webhook_fires_once(
    hass, aioclient_mock, webhook_entry, post
) -> None:
    """A webhook arriving after the poll saw the activity is also dropped.

    Fleet sends the webhook asynchronously and retries a rate-limited one for
    up to 30 minutes, so the poll can win.
    """
    events = async_capture_events(hass, EVENT_HOST_ENROLLED)
    body = webhook_activity()

    await inventory_poll(
        hass,
        webhook_entry,
        aioclient_mock,
        activities=activities_payload(polled(50, body)),
    )
    assert len(events) == 1
    assert events[0].data["source"] == "poll"

    await post(webhook_entry, body)

    assert len(events) == 1


async def test_database_rounding_still_matches(
    hass, aioclient_mock, webhook_entry, post
) -> None:
    """A stored timestamp rounded to the second still matches the webhook's."""
    events = async_capture_events(hass, EVENT_HOST_ENROLLED)
    body = webhook_activity(timestamp="2026-08-07T12:00:00.700000123Z")

    await post(webhook_entry, body)
    await inventory_poll(
        hass,
        webhook_entry,
        aioclient_mock,
        activities=activities_payload(polled(50, body, "2026-08-07T12:00:01Z")),
    )

    assert len(events) == 1


async def test_identical_activities_each_fire_once(
    hass, aioclient_mock, webhook_entry, post
) -> None:
    """Two genuinely separate identical activities are not merged.

    One user logging in twice in the same second produces two activities with
    the same type, details and timestamp.
    """
    # Changing options reloads the entry, which re-registers the webhook.
    hass.config_entries.async_update_entry(
        webhook_entry, options={CONF_WEBHOOKS: True, CONF_ACTIVITY_EVENTS: True}
    )
    await hass.async_block_till_done()
    events = async_capture_events(hass, EVENT_ACTIVITY)
    body = webhook_activity("user_logged_in", {"public_ip": "192.0.2.10"})

    await post(webhook_entry, body)
    await post(webhook_entry, body)
    assert len(events) == 2

    await inventory_poll(
        hass,
        webhook_entry,
        aioclient_mock,
        activities=activities_payload(polled(60, body), polled(61, body)),
    )
    assert len(events) == 2

    # A third, identical login the webhook never delivered is still caught.
    await inventory_poll(
        hass,
        webhook_entry,
        aioclient_mock,
        activities=activities_payload(
            polled(60, body), polled(61, body), polled(62, body)
        ),
    )
    assert len(events) == 3


async def test_other_activities_need_the_option(hass, webhook_entry, post) -> None:
    """Pushed audit activities follow the same opt-in as polled ones."""
    events = async_capture_events(hass, EVENT_ACTIVITY)

    await post(webhook_entry, webhook_activity("user_logged_in", {"public_ip": "x"}))

    assert events == []


async def test_other_activities_fire_when_enabled(
    hass, aioclient_mock, mock_config_entry, post
) -> None:
    """With activity events on, any activity type is pushed through."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_WEBHOOKS: True, CONF_ACTIVITY_EVENTS: True}
    )
    mock_fleet(aioclient_mock)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    events = async_capture_events(hass, EVENT_ACTIVITY)

    await post(mock_config_entry, webhook_activity("ran_script", {"host_id": 7}))

    assert len(events) == 1
    assert events[0].data["activity_type"] == "ran_script"
    assert events[0].data["details"] == {"host_id": 7}
    assert events[0].data["source"] == "webhook"


async def test_pushed_activity_survives_restart(
    hass, aioclient_mock, webhook_entry, post
) -> None:
    """A delivery just before a restart is not re-fired by the next poll."""
    events = async_capture_events(hass, EVENT_HOST_ENROLLED)
    body = webhook_activity()
    await post(webhook_entry, body)
    # Let the delayed save land.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done()

    assert await hass.config_entries.async_reload(webhook_entry.entry_id)
    await hass.async_block_till_done()
    await inventory_poll(
        hass,
        webhook_entry,
        aioclient_mock,
        activities=activities_payload(polled(50, body)),
    )

    assert len(events) == 1


async def test_polling_unchanged_without_webhooks(
    hass, aioclient_mock, setup_integration
) -> None:
    """With webhooks off, polled enrollments fire exactly as before."""
    events = async_capture_events(hass, EVENT_HOST_ENROLLED)

    await inventory_poll(
        hass,
        setup_integration,
        aioclient_mock,
        activities=activities_payload(enrollment_activity(50, 9, "New Host")),
    )

    assert len(events) == 1
    assert events[0].data["activity_id"] == 50
    assert events[0].data["source"] == "poll"


# --- Failing policies ----------------------------------------------------------


async def test_failing_policy_report(hass, webhook_entry, post) -> None:
    """Fleet's report of newly failing hosts becomes one event per delivery."""
    events = async_capture_events(hass, EVENT_POLICY_HOSTS_FAILING)

    assert await post(webhook_entry, FAILING_POLICY_BODY) == HTTPStatus.OK

    assert len(events) == 1
    data = events[0].data
    assert data["policy_id"] == 101
    assert data["policy_name"] == "Gatekeeper enabled"
    assert data["critical"] is True
    assert data["host_count"] == 2
    assert data["hosts"][1] == {
        "host_id": 7,
        "host_name": "Carol MBP",
        "hostname": "carol-mbp.local",
        "url": "https://fleet.example.com/hosts/7",
    }
    assert data["reported_at"].startswith("2026-09-25T12:00:00.123456")
    assert data["entry_id"] == webhook_entry.entry_id

    state = hass.states.get("event.fleet_fleet_events")
    assert state.attributes["event_type"] == "policy_hosts_failing"


# --- Everything else -------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        # Vulnerabilities webhook.
        {
            "timestamp": "2026-09-25T12:00:00Z",
            "vulnerability": {"cve": "CVE-2026-0001", "hosts_affected": []},
        },
        # Host status webhook.
        {"text": "More than 10% of your hosts...", "data": {"unseen_hosts": 3}},
    ],
)
async def test_unused_fleet_webhooks_accepted_and_ignored(
    hass, webhook_entry, post, body
) -> None:
    """Pointing every Fleet webhook at one URL is harmless."""
    events = async_capture_events(hass, EVENT_HOST_ENROLLED)

    assert await post(webhook_entry, body) == HTTPStatus.OK

    assert events == []
    assert webhook_entry.runtime_data.webhook_status.counts == {"ignored": 1}


@pytest.mark.parametrize(
    ("body", "raw"),
    [
        (None, "not json"),
        (["a", "list"], None),
        ({"policy": {"name": "no id"}, "hosts": []}, None),
        ({**FAILING_POLICY_BODY, "hosts": [{"hostname": "no id"}]}, None),
    ],
)
async def test_malformed_bodies_rejected(hass, webhook_entry, post, body, raw) -> None:
    """Anything no Fleet webhook would send is a 400, and fires nothing."""
    events = async_capture_events(hass, EVENT_POLICY_HOSTS_FAILING)

    assert await post(webhook_entry, body, raw=raw) == HTTPStatus.BAD_REQUEST

    assert events == []
    assert webhook_entry.runtime_data.webhook_status.counts == {}


async def test_last_webhook_sensor(hass, webhook_entry, post) -> None:
    """The diagnostic sensor shows when Fleet last got through."""
    state = hass.states.get("sensor.fleet_last_webhook_received")
    assert state is not None
    assert state.state == "unknown"

    await post(webhook_entry, FAILING_POLICY_BODY)
    await post(webhook_entry, webhook_activity())

    state = hass.states.get("sensor.fleet_last_webhook_received")
    assert dt_util.parse_datetime(state.state) is not None
    assert state.attributes["last_kind"] == "activity"
    assert state.attributes["deliveries_since_start"] == {
        "failing_policy": 1,
        "activity": 1,
    }


async def test_diagnostics_redact_webhook_id(hass, webhook_entry, post) -> None:
    """The webhook ID is a credential, so it never appears in diagnostics."""
    await post(webhook_entry, webhook_activity())

    diagnostics = await async_get_config_entry_diagnostics(hass, webhook_entry)

    assert diagnostics["entry"]["data"][CONF_WEBHOOK_ID] == "**REDACTED**"
    assert webhook_entry.data[CONF_WEBHOOK_ID] not in str(diagnostics)
    assert diagnostics["webhooks"]["enabled"] is True
    assert diagnostics["webhooks"]["local_only"] is True
    assert diagnostics["webhooks"]["deliveries_since_start"] == {"activity": 1}
