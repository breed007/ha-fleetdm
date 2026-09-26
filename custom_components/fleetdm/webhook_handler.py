"""Receive Fleet's webhooks.

Fleet can push two things this integration uses. The activities webhook fires
the moment any audit activity is recorded, which turns host enrollments and
opt-in activity events from "within one inventory interval" into seconds. The
failing policies webhook names the hosts that started failing a policy, which
polling cannot see at all.

Webhooks add to polling rather than replacing it. Fleet does not retry a failed
delivery, apart from a rate-limited one, so anything pushed while Home Assistant
is down is lost unless the next poll recovers it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from http import HTTPStatus
from typing import TYPE_CHECKING

from aiohttp import hdrs, web
from homeassistant.components import webhook
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.util import dt as dt_util

from .api import FleetFailingPolicyReport, FleetWebhookActivity, parse_webhook_payload
from .const import (
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_LOCAL_ONLY,
    DEFAULT_WEBHOOK_LOCAL_ONLY,
    DOMAIN,
    SIGNAL_WEBHOOK_RECEIVED,
)

if TYPE_CHECKING:
    from . import FleetConfigEntry

_LOGGER = logging.getLogger(__name__)

KIND_ACTIVITY = "activity"
KIND_FAILING_POLICY = "failing_policy"
KIND_IGNORED = "ignored"


@dataclass(slots=True)
class FleetWebhookStatus:
    """What has arrived so far, for the diagnostic sensor and diagnostics.

    Mostly here to answer "is Fleet actually reaching me?" after setting the
    URL in Fleet, which otherwise has no visible answer until an event fires.
    """

    last_received: datetime | None = None
    last_kind: str | None = None
    counts: dict[str, int] = field(default_factory=dict)

    def record(self, kind: str) -> None:
        """Note one delivery."""
        self.last_received = dt_util.utcnow()
        self.last_kind = kind
        self.counts[kind] = self.counts.get(kind, 0) + 1


@callback
def async_register_webhook(hass: HomeAssistant, entry: FleetConfigEntry) -> None:
    """Start accepting Fleet's webhooks for this entry until it unloads."""
    webhook_id: str = entry.data[CONF_WEBHOOK_ID]

    async def _handle(
        hass: HomeAssistant, webhook_id: str, request: web.Request
    ) -> web.Response:
        return await _async_handle_webhook(hass, entry, request)

    webhook.async_register(
        hass,
        DOMAIN,
        f"Fleet ({entry.title})",
        webhook_id,
        _handle,
        local_only=entry.options.get(
            CONF_WEBHOOK_LOCAL_ONLY, DEFAULT_WEBHOOK_LOCAL_ONLY
        ),
        allowed_methods=[hdrs.METH_POST],
    )
    entry.async_on_unload(lambda: webhook.async_unregister(hass, webhook_id))


@callback
def async_webhook_url(hass: HomeAssistant, webhook_id: str) -> str:
    """Return the URL to paste into Fleet.

    Prefers Home Assistant's internal URL, because the expected setup is a Fleet
    server on the same network. Falls back to the bare path when Home Assistant
    has no URL configured, so the form can still show what to append.
    """
    try:
        return webhook.async_generate_url(
            hass, webhook_id, allow_ip=True, prefer_external=False
        )
    except NoURLAvailableError:
        return webhook.async_generate_path(webhook_id)


async def _async_handle_webhook(
    hass: HomeAssistant, entry: FleetConfigEntry, request: web.Request
) -> web.Response:
    """Parse one delivery and hand it to the coordinator that owns it.

    Fleet's own webhooks this integration does not use get a 200 rather than an
    error, so pointing all of Fleet's webhooks at one URL is harmless. A body
    that is not what any Fleet webhook sends gets a 400, which Fleet logs.
    """
    try:
        data = await request.json()
    except ValueError:
        _LOGGER.debug("Ignoring a Fleet webhook body that is not JSON")
        return web.Response(status=HTTPStatus.BAD_REQUEST)
    if not isinstance(data, dict):
        _LOGGER.debug("Ignoring a Fleet webhook body that is not a JSON object")
        return web.Response(status=HTTPStatus.BAD_REQUEST)

    try:
        payload = parse_webhook_payload(data)
    except (KeyError, TypeError, ValueError) as err:
        _LOGGER.debug("Ignoring an unreadable Fleet webhook payload: %s", err)
        return web.Response(status=HTTPStatus.BAD_REQUEST)

    runtime = entry.runtime_data
    if isinstance(payload, FleetWebhookActivity):
        kind = KIND_ACTIVITY
        _LOGGER.debug("Fleet webhook: activity %s", payload.type)
        await runtime.inventory.async_process_webhook_activity(payload)
    elif isinstance(payload, FleetFailingPolicyReport):
        kind = KIND_FAILING_POLICY
        _LOGGER.debug(
            "Fleet webhook: %d hosts failing policy %s",
            len(payload.hosts),
            payload.policy.id,
        )
        runtime.summary.async_process_failing_policy_report(payload)
    else:
        kind = KIND_IGNORED
        _LOGGER.debug(
            "Ignoring a Fleet webhook this integration does not use (keys: %s)",
            sorted(data),
        )

    runtime.webhook_status.record(kind)
    async_dispatcher_send(hass, SIGNAL_WEBHOOK_RECEIVED.format(entry_id=entry.entry_id))
    return web.Response(status=HTTPStatus.OK)
