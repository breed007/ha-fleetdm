"""Diagnostics support for the Fleet integration."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import FleetConfigEntry
from .const import (
    CONF_API_TOKEN,
    CONF_REDACT_HOSTNAMES,
    CONF_URL,
    DEFAULT_REDACT_HOSTNAMES,
)

REDACTED = "**REDACTED**"

# The token is redacted unconditionally. Nothing in a diagnostics download
# should ever be able to authenticate against a Fleet server.
TO_REDACT = {CONF_API_TOKEN}


def _redact_url(url: str) -> str:
    """Strip the host from a URL while keeping its shape for debugging."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, REDACTED, parts.path, "", ""))


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: FleetConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data.summary
    redact_hostnames = entry.options.get(
        CONF_REDACT_HOSTNAMES, DEFAULT_REDACT_HOSTNAMES
    )

    entry_data = async_redact_data(dict(entry.data), TO_REDACT)
    if redact_hostnames and CONF_URL in entry_data:
        entry_data[CONF_URL] = _redact_url(entry_data[CONF_URL])

    data = coordinator.data
    payload: dict[str, Any] = {
        "entry": {
            "data": entry_data,
            "options": dict(entry.options),
        },
        "hostnames_redacted": redact_hostnames,
        "server": {
            # Build metadata is useful for triage and identifies the server
            # version, not the operator.
            "version": coordinator.version.get("version"),
            "branch": coordinator.version.get("branch"),
            "build_date": coordinator.version.get("build_date"),
            "premium": coordinator.premium,
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
        },
    }

    if data is not None:
        payload["host_summary"] = {
            "total": data.summary.total,
            "online": data.summary.online,
            "offline": data.summary.offline,
            "missing": data.summary.missing,
            "new": data.summary.new,
        }
        payload["compliance_problem"] = data.compliance_problem
        # Policy names and counts are kept even when hostname redaction is on:
        # they describe the operator's compliance rules, not their machines, and
        # they are what a bug report about drift detection actually needs.
        payload["policies"] = [
            {
                "id": policy.id,
                "name": policy.name,
                "platform": policy.platform,
                "critical": policy.critical,
                "passing_host_count": policy.passing_host_count,
                "failing_host_count": policy.failing_host_count,
                "host_count_updated_at": policy.host_count_updated_at,
            }
            for policy in data.policies
        ]
        payload["last_cycle_events"] = [
            {"event_type": event.event_type, "policy_id": event.data.get("policy_id")}
            for event in data.events
        ]

    inventory = entry.runtime_data.inventory
    payload["inventory"] = {
        "last_update_success": inventory.last_update_success,
        "update_interval_seconds": (
            inventory.update_interval.total_seconds()
            if inventory.update_interval
            else None
        ),
        "missing_after_hours": inventory.missing_after.total_seconds() / 3600,
    }

    if (inv := inventory.data) is not None:
        payload["inventory"]["host_count"] = len(inv.hosts)
        # Hosts are the one place diagnostics carry genuinely identifying data:
        # machine names and LAN addresses. Redaction is on by default for
        # exactly this, and it replaces rather than omits so the shape of a bug
        # report stays readable.
        payload["hosts"] = [
            {
                "id": host.id,
                "name": REDACTED if redact_hostnames else host.display_name,
                "hostname": REDACTED if redact_hostnames else host.hostname,
                "primary_ip": REDACTED if redact_hostnames else host.primary_ip,
                "platform": host.platform,
                "os_version": host.os_version,
                "status": host.status,
                "failing_policies_count": host.failing_policies_count,
                "seen_time": host.seen_time.isoformat() if host.seen_time else None,
            }
            for host in inv.hosts
        ]
        if inv.vulnerable is not None:
            payload["vulnerable_software"] = {
                "count": inv.vulnerable.count,
                "counts_updated_at": (
                    inv.vulnerable.counts_updated_at.isoformat()
                    if inv.vulnerable.counts_updated_at
                    else None
                ),
                # Software titles describe what is installed, not who runs it,
                # so they are kept even when hostnames are redacted.
                "most_widespread": [
                    {
                        "name": title.name,
                        "hosts_count": title.hosts_count,
                        "cve_count": title.cve_count,
                    }
                    for title in inv.vulnerable.worst
                ],
            }

    return payload
