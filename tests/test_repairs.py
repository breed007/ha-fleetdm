"""Tests for the repair issues raised when a list hits the pagination cap."""

from __future__ import annotations

from homeassistant.helpers import issue_registry as ir

from custom_components.fleetdm.api import (
    HOSTS_PER_PAGE,
    MAX_PAGES,
    POLICIES_PER_PAGE,
)
from custom_components.fleetdm.const import DOMAIN, ISSUE_TRACKER_URL
from custom_components.fleetdm.issues import truncation_issue_id

from .conftest import host, hosts_payload, policies_payload
from .test_drift import poll, setup_with
from .test_hosts import inventory_poll

# A page that is always full never ends the pagination loop on its own.
FULL_HOST_PAGE = hosts_payload(
    *(host(i, f"Host {i}") for i in range(1, HOSTS_PER_PAGE + 1))
)
FULL_POLICY_PAGE = policies_payload(
    *({"id": i, "name": f"Policy {i}"} for i in range(1, POLICIES_PER_PAGE + 1))
)


def _issue(hass, entry, feed: str) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(
        DOMAIN, truncation_issue_id(entry.entry_id, feed)
    )


async def test_no_issue_for_a_normal_fleet(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """Nothing is raised when every list fits."""
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)

    assert _issue(hass, entry, "hosts") is None
    assert _issue(hass, entry, "policies") is None


async def test_truncated_hosts_raise_and_clear_an_issue(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """A host list cut short is shown to the user, and clears once it fits."""
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)

    await inventory_poll(hass, entry, aioclient_mock, hosts=FULL_HOST_PAGE)

    issue = _issue(hass, entry, "hosts")
    assert issue is not None
    assert issue.translation_key == "hosts_truncated"
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.is_fixable is False
    assert issue.learn_more_url == ISSUE_TRACKER_URL
    assert issue.translation_placeholders == {
        "server": "fleet.example.com",
        "count": str(MAX_PAGES * HOSTS_PER_PAGE),
    }
    # The coordinator still works with what it read.
    assert entry.runtime_data.inventory.last_update_success is True

    await inventory_poll(hass, entry, aioclient_mock)

    assert _issue(hass, entry, "hosts") is None


async def test_truncated_policies_raise_and_clear_an_issue(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """The same for the policy library, on the summary coordinator."""
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)

    await poll(hass, entry, aioclient_mock, policies=FULL_POLICY_PAGE)

    issue = _issue(hass, entry, "policies")
    assert issue is not None
    assert issue.translation_key == "policies_truncated"
    assert issue.translation_placeholders["count"] == str(MAX_PAGES * POLICIES_PER_PAGE)

    await poll(hass, entry, aioclient_mock)

    assert _issue(hass, entry, "policies") is None


async def test_issues_removed_on_unload(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """An unloaded entry leaves no stale notice behind."""
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)
    await inventory_poll(hass, entry, aioclient_mock, hosts=FULL_HOST_PAGE)
    assert _issue(hass, entry, "hosts") is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert _issue(hass, entry, "hosts") is None


async def test_issues_removed_with_the_entry(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """Deleting the entry deletes its issues too."""
    entry = await setup_with(hass, mock_config_entry, aioclient_mock)
    await poll(hass, entry, aioclient_mock, policies=FULL_POLICY_PAGE)
    assert _issue(hass, entry, "policies") is not None

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert _issue(hass, entry, "policies") is None
