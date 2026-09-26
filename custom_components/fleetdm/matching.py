"""Match activities delivered by webhook against the polled activity feed.

With webhooks on, most activities reach Home Assistant twice: pushed by Fleet
the moment they happen, then again when the inventory coordinator next reads
the activity feed. Polling stays on because Fleet does not retry a failed
delivery, so a webhook sent while Home Assistant was restarting is simply lost
and only the poll can recover it. Each activity must still fire exactly once,
whichever path reports it first.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

# How far apart the two reports of one activity's timestamp may be. Fleet sends
# the webhook and stores the row with the same timestamp, but the database may
# round it to a precision the webhook's JSON does not.
TIMESTAMP_TOLERANCE = timedelta(seconds=2)


def activity_key(activity_type: str, details: dict[str, Any]) -> str:
    """Identify an activity by what it says, since webhooks carry no ID."""
    canonical = json.dumps(
        [activity_type, details], sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:24]


@dataclass(slots=True)
class _Sighting:
    """One activity fired by one path, waiting for the other to report it."""

    source: str
    key: str
    created_at: datetime | None
    recorded_at: datetime


class ActivityMatcher:
    """Lets each Fleet activity fire once, whichever path sees it first.

    Matching is one-to-one. Two identical activities in the same second, such
    as one user logging in twice, are two sightings on each path, and each
    report consumes exactly one sighting from the other path. Otherwise the
    second login would be swallowed as a duplicate of the first.
    """

    def __init__(self, retention: timedelta) -> None:
        """Keep unmatched sightings for ``retention`` before giving up on them.

        This must outlast the gap between a webhook and the next poll, which is
        up to one inventory interval, and Fleet's retry window for a webhook
        that arrives after the poll.
        """
        self._retention = retention
        self._pending: list[_Sighting] = []

    def claim(
        self,
        source: str,
        activity_type: str,
        created_at: datetime | None,
        details: dict[str, Any],
    ) -> bool:
        """Return whether this report should fire an event.

        False means the other path already fired the same activity, and its
        sighting is used up. True means this is the first report, and it is
        recorded so the other path's report is recognized later.
        """
        now = dt_util.utcnow()
        self._expire(now)
        key = activity_key(activity_type, details)

        for index, sighting in enumerate(self._pending):
            if (
                sighting.source != source
                and sighting.key == key
                and _close(sighting.created_at, created_at)
            ):
                del self._pending[index]
                return False

        self._pending.append(_Sighting(source, key, created_at, now))
        return True

    def _expire(self, now: datetime) -> None:
        cutoff = now - self._retention
        self._pending = [s for s in self._pending if s.recorded_at >= cutoff]

    def as_json(self) -> list[list[str | None]]:
        """Serialize pending sightings for the coordinator's store."""
        return [
            [
                s.source,
                s.key,
                s.created_at.isoformat() if s.created_at else None,
                s.recorded_at.isoformat(),
            ]
            for s in self._pending
        ]

    def restore(self, stored: Any) -> None:
        """Load sightings saved before a restart, skipping anything malformed.

        Without this, an activity pushed just before Home Assistant restarted
        would fire again when the first poll after the restart reads it.
        """
        self._pending = []
        if not isinstance(stored, list):
            return
        for item in stored:
            try:
                source, key, created_at, recorded_at = item
                recorded = datetime.fromisoformat(recorded_at)
                created = datetime.fromisoformat(created_at) if created_at else None
            except (TypeError, ValueError):
                continue
            self._pending.append(_Sighting(source, key, created, recorded))
        self._expire(dt_util.utcnow())


def _close(first: datetime | None, second: datetime | None) -> bool:
    """Whether two timestamps can be the same activity.

    A missing timestamp never matches: firing twice is a smaller failure than
    silently swallowing a distinct activity.
    """
    if first is None or second is None:
        return False
    return abs(first - second) <= TIMESTAMP_TOLERANCE
