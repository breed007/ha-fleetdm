"""Unit tests for matching webhook deliveries against polled activities."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from freezegun import freeze_time

from custom_components.fleetdm.matching import ActivityMatcher, activity_key

AT = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)
DETAILS = {"host_id": 9, "host_display_name": "New Host"}


def matcher() -> ActivityMatcher:
    return ActivityMatcher(retention=timedelta(hours=2))


def test_key_ignores_detail_order() -> None:
    """Fleet's JSON key order is not something to depend on."""
    assert activity_key("t", {"a": 1, "b": 2}) == activity_key("t", {"b": 2, "a": 1})
    assert activity_key("t", {"a": 1}) != activity_key("u", {"a": 1})


def test_second_path_is_suppressed() -> None:
    """Whichever path reports second does not fire."""
    m = matcher()
    assert m.claim("webhook", "fleet_enrolled", AT, DETAILS) is True
    assert m.claim("poll", "fleet_enrolled", AT, DETAILS) is False


def test_same_path_never_matches_itself() -> None:
    """Two webhooks for identical activities are two activities."""
    m = matcher()
    assert m.claim("webhook", "fleet_enrolled", AT, DETAILS) is True
    assert m.claim("webhook", "fleet_enrolled", AT, DETAILS) is True


def test_matching_is_one_to_one() -> None:
    """Each sighting is consumed once, so a third report still fires."""
    m = matcher()
    m.claim("webhook", "t", AT, DETAILS)
    m.claim("webhook", "t", AT, DETAILS)
    assert m.claim("poll", "t", AT, DETAILS) is False
    assert m.claim("poll", "t", AT, DETAILS) is False
    assert m.claim("poll", "t", AT, DETAILS) is True


def test_timestamp_tolerance() -> None:
    """Rounding is tolerated; a genuinely later activity is not a match."""
    m = matcher()
    m.claim("webhook", "t", AT, DETAILS)
    assert m.claim("poll", "t", AT + timedelta(seconds=1), DETAILS) is False

    m.claim("webhook", "t", AT, DETAILS)
    assert m.claim("poll", "t", AT + timedelta(seconds=30), DETAILS) is True


def test_different_details_do_not_match() -> None:
    """Two enrollments at the same moment are different hosts."""
    m = matcher()
    m.claim("webhook", "fleet_enrolled", AT, {"host_id": 1})
    assert m.claim("poll", "fleet_enrolled", AT, {"host_id": 2}) is True


def test_missing_timestamp_never_matches() -> None:
    """Better to fire twice than to swallow a distinct activity."""
    m = matcher()
    m.claim("webhook", "t", None, DETAILS)
    assert m.claim("poll", "t", None, DETAILS) is True


def test_sightings_expire() -> None:
    """An unmatched sighting is given up on after the retention period."""
    m = matcher()
    with freeze_time(AT):
        m.claim("webhook", "t", AT, DETAILS)
    with freeze_time(AT + timedelta(hours=3)):
        assert m.claim("poll", "t", AT, DETAILS) is True


def test_round_trip_through_storage() -> None:
    """Pending sightings survive being saved and restored."""
    m = matcher()
    m.claim("webhook", "t", AT, DETAILS)
    m.claim("webhook", "t", None, DETAILS)

    restored = matcher()
    restored.restore(m.as_json())

    assert restored.claim("poll", "t", AT, DETAILS) is False
    assert restored.as_json()[0][2] is None


def test_restore_skips_malformed() -> None:
    """A corrupt store loses those sightings, not the integration."""
    m = matcher()
    m.restore(
        [
            ["webhook", "k", "not a date", "2026-09-25T12:00:00+00:00"],
            ["too", "short"],
            None,
        ]
    )
    assert m.as_json() == []

    m.restore("not a list")
    assert m.as_json() == []
