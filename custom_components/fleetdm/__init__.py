"""The Fleet (fleetdm.com) integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .api import FleetClient
from .const import (
    CONF_API_TOKEN,
    CONF_URL,
    CONF_VERIFY_SSL,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOKS,
    DEFAULT_VERIFY_SSL,
    DEFAULT_WEBHOOKS,
    STORAGE_KEY_INVENTORY_TEMPLATE,
    STORAGE_KEY_TEMPLATE,
    STORAGE_VERSION,
)
from .coordinator import FleetInventoryCoordinator, FleetSummaryCoordinator
from .entity import (
    async_setup_host_device_sync,
    host_id_from_identifiers,
    hub_device_info,
)
from .issues import async_delete_issues
from .webhook_handler import FleetWebhookStatus, async_register_webhook

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.EVENT,
    Platform.SENSOR,
]


@dataclass(slots=True)
class FleetRuntimeData:
    """Objects shared between the config entry and its platforms."""

    client: FleetClient
    summary: FleetSummaryCoordinator
    inventory: FleetInventoryCoordinator
    hub_device_id: str
    webhook_status: FleetWebhookStatus = field(default_factory=FleetWebhookStatus)


type FleetConfigEntry = ConfigEntry[FleetRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: FleetConfigEntry) -> bool:
    """Set up Fleet from a config entry."""
    session = async_get_clientsession(
        hass, verify_ssl=entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
    )
    client = FleetClient(session, entry.data[CONF_URL], entry.data[CONF_API_TOKEN])

    summary = FleetSummaryCoordinator(hass, entry, client)
    await summary.async_config_entry_first_refresh()

    inventory = FleetInventoryCoordinator(hass, entry, client)
    await inventory.async_config_entry_first_refresh()

    # Registered here rather than left to the first hub entity, so host devices
    # can link to it by registry ID however the platforms happen to be ordered.
    hub = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, **hub_device_info(entry.entry_id, summary)
    )

    entry.runtime_data = FleetRuntimeData(
        client=client, summary=summary, inventory=inventory, hub_device_id=hub.id
    )

    async_setup_host_device_sync(hass, entry, inventory)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    # After the platforms, so the event entity is listening before the first
    # delivery can arrive.
    if entry.options.get(CONF_WEBHOOKS, DEFAULT_WEBHOOKS):
        async_register_webhook(hass, entry)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Bring entries created by older releases up to date.

    1.2 adds a webhook ID. It is generated whether or not webhooks are used,
    so the URL shown in the options form is stable from the start.
    """
    if entry.version > 1:
        # Created by a newer release this one does not understand.
        return False

    if entry.minor_version < 2:
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_WEBHOOK_ID: webhook.async_generate_id()},
            minor_version=2,
        )
        _LOGGER.debug("Migrated Fleet entry %s to version 1.2", entry.entry_id)
    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    entry: FleetConfigEntry,
    device: dr.DeviceEntry,
) -> bool:
    """Allow deleting a host device from the UI once Fleet has dropped it.

    Refuses while the host still exists, since the device would simply be
    recreated on the next refresh, and refuses for the hub device, which
    belongs to the config entry itself.
    """
    host_id = host_id_from_identifiers(entry.entry_id, device.identifiers)
    if host_id is None:
        return False
    return host_id not in entry.runtime_data.inventory.data.hosts_by_id


async def async_unload_entry(hass: HomeAssistant, entry: FleetConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        # Raised afresh on the next poll if still true; stale once unloaded.
        async_delete_issues(hass, entry.entry_id)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up persisted event state and repair issues for a deleted entry."""
    async_delete_issues(hass, entry.entry_id)
    for template in (STORAGE_KEY_TEMPLATE, STORAGE_KEY_INVENTORY_TEMPLATE):
        store = Store[dict[str, Any]](
            hass, STORAGE_VERSION, template.format(entry_id=entry.entry_id)
        )
        await store.async_remove()


async def _async_options_updated(hass: HomeAssistant, entry: FleetConfigEntry) -> None:
    """Reload the entry so new intervals and entity choices take effect."""
    await hass.config_entries.async_reload(entry.entry_id)
