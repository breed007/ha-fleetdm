"""Tests for translated exceptions.

Coordinator failures are what users see in the logs and on the integration
card, so they are raised with translation keys rather than English strings from
the API client.
"""

from __future__ import annotations

import json
from pathlib import Path
from string import Formatter

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.fleetdm.const import DOMAIN

from .conftest import API, BASE_URL, remock_fleet
from .test_drift import setup_with

STRINGS = json.loads(
    (Path(__file__).parents[1] / "custom_components/fleetdm/strings.json").read_text()
)


def _placeholders(template: str) -> set[str]:
    return {name for _, name, _, _ in Formatter().parse(template) if name}


async def _failing_summary_poll(hass, entry, aioclient_mock, **mock) -> Exception:
    """Run one summary poll against a broken endpoint; return what it raised."""
    remock_fleet(aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/host_summary", **mock)
    coordinator = entry.runtime_data.summary
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.last_update_success is False
    return coordinator.last_exception


@pytest.mark.parametrize(
    ("mock", "error_type", "key", "placeholders"),
    [
        (
            {"exc": TimeoutError()},
            UpdateFailed,
            "cannot_connect",
            {"url": BASE_URL},
        ),
        (
            {"status": 401},
            ConfigEntryAuthFailed,
            "invalid_auth",
            {"path": "/host_summary"},
        ),
        (
            {"status": 403},
            UpdateFailed,
            "forbidden",
            {"path": "/host_summary", "status": "403"},
        ),
        (
            {"status": 404},
            UpdateFailed,
            "endpoint_not_found",
            {"path": "/host_summary"},
        ),
        (
            {"status": 500},
            UpdateFailed,
            "http_error",
            {"path": "/host_summary", "status": "500"},
        ),
        (
            {"text": "<html>not fleet</html>"},
            UpdateFailed,
            "unexpected_response",
            {"path": "/host_summary"},
        ),
        (
            {"json": ["not", "an", "object"]},
            UpdateFailed,
            "unexpected_response",
            {"path": "/host_summary"},
        ),
    ],
)
async def test_coordinator_errors_are_translated(
    hass, aioclient_mock, mock_config_entry, mock, error_type, key, placeholders
) -> None:
    """Every failure mode maps to a translation that exists and fits."""
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)

    err = await _failing_summary_poll(hass, entry, aioclient_mock, **mock)

    assert isinstance(err, error_type)
    assert err.translation_domain == DOMAIN
    assert err.translation_key == key
    assert err.translation_placeholders == placeholders
    # The string exists, and uses exactly the placeholders supplied.
    template = STRINGS["exceptions"][key]["message"]
    assert _placeholders(template) == set(placeholders)


async def test_translated_message_reaches_the_log(
    hass, aioclient_mock, mock_config_entry, caplog
) -> None:
    """The English translation, not the key, is what gets logged."""
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)

    err = await _failing_summary_poll(hass, entry, aioclient_mock, exc=TimeoutError())

    # Home Assistant drops the trailing period from translated exceptions.
    expected = f"Could not reach the Fleet server at {BASE_URL}"
    assert str(err) == expected
    assert expected in caplog.text


async def test_missing_policies_route_is_translated(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """A server with neither policies route says what was tried."""
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)
    # A fresh client probes every known route again.
    entry.runtime_data.summary.client._policies_path = None

    remock_fleet(aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/host_summary", json={})
    aioclient_mock.get(f"{API}/policies", status=404)
    aioclient_mock.get(f"{API}/global/policies", status=404)
    coordinator = entry.runtime_data.summary
    await coordinator.async_refresh()

    err = coordinator.last_exception
    assert err.translation_key == "no_policies_endpoint"
    assert err.translation_placeholders == {"paths": "/policies, /global/policies"}


def test_every_exception_string_has_a_message() -> None:
    """Hassfest requires a message for each exception translation."""
    for key, value in STRINGS["exceptions"].items():
        assert value["message"], key
