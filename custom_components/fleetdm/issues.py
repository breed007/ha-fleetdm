"""Repair issues raised by the Fleet integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .api import FEED_HOSTS, FEED_POLICIES
from .const import DOMAIN, ISSUE_TRACKER_URL

TRUNCATION_FEEDS = (FEED_POLICIES, FEED_HOSTS)


def truncation_issue_id(entry_id: str, feed: str) -> str:
    """Build the issue ID for a feed cut short on one config entry."""
    return f"{entry_id}_{feed}_truncated"


@callback
def async_sync_truncation_issue(
    hass: HomeAssistant,
    entry: ConfigEntry,
    feed: str,
    *,
    truncated: bool,
    count: int,
) -> None:
    """Raise or clear the issue for a list that hit the pagination cap.

    A capped read is not an error, so the coordinator keeps working with what it
    got. But the missing objects have no entities and fire no events, which is
    silent data loss unless it is shown somewhere the user will look. The issue
    clears itself on the first read that is no longer cut short.
    """
    issue_id = truncation_issue_id(entry.entry_id, feed)
    if not truncated:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return

    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        learn_more_url=ISSUE_TRACKER_URL,
        translation_key=f"{feed}_truncated",
        translation_placeholders={"server": entry.title, "count": str(count)},
    )


@callback
def async_delete_issues(hass: HomeAssistant, entry_id: str) -> None:
    """Remove every issue belonging to a config entry."""
    for feed in TRUNCATION_FEEDS:
        ir.async_delete_issue(hass, DOMAIN, truncation_issue_id(entry_id, feed))
