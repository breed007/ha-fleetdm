"""The Fleet (fleetdm.com) integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .api import FleetClient
from .const import (
    CONF_API_TOKEN,
    CONF_URL,
    CONF_VERIFY_SSL,
    DEFAULT_VERIFY_SSL,
    STORAGE_KEY_TEMPLATE,
    STORAGE_VERSION,
)
from .coordinator import FleetSummaryCoordinator

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


type FleetConfigEntry = ConfigEntry[FleetRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: FleetConfigEntry) -> bool:
    """Set up Fleet from a config entry."""
    session = async_get_clientsession(
        hass, verify_ssl=entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
    )
    client = FleetClient(session, entry.data[CONF_URL], entry.data[CONF_API_TOKEN])

    coordinator = FleetSummaryCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = FleetRuntimeData(client=client, summary=coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: FleetConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up persisted drift state when the entry is deleted."""
    store = Store[dict](
        hass, STORAGE_VERSION, STORAGE_KEY_TEMPLATE.format(entry_id=entry.entry_id)
    )
    await store.async_remove()


async def _async_options_updated(hass: HomeAssistant, entry: FleetConfigEntry) -> None:
    """Reload the entry so new poll intervals take effect."""
    await hass.config_entries.async_reload(entry.entry_id)
