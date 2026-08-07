"""Event platform for the Fleet integration.

The `event` entity is the timeline/UI surface for compliance drift. For
automations prefer the matching bus events (``fleetdm_policy_failing`` and
``fleetdm_policy_recovered``), which the coordinator fires once per transition
with the full payload — an `event` entity can only hold one event at a time, so
several policies flipping in the same poll are best consumed from the bus.
"""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import FleetConfigEntry
from .const import EVENT_TYPES
from .coordinator import FleetSummaryCoordinator
from .entity import FleetEntity, fleet_unique_id


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FleetConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Fleet event entity."""
    async_add_entities([FleetEventEntity(entry.runtime_data.summary, entry)])


class FleetEventEntity(FleetEntity, EventEntity):
    """Surfaces Fleet compliance drift as Home Assistant events."""

    _attr_event_types = EVENT_TYPES
    _attr_translation_key = "fleet_events"

    def __init__(
        self, coordinator: FleetSummaryCoordinator, entry: FleetConfigEntry
    ) -> None:
        """Initialise the event entity."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = fleet_unique_id(entry.entry_id, "events")

    @callback
    def _handle_coordinator_update(self) -> None:
        """Replay this cycle's drift events onto the entity."""
        data = self.coordinator.data
        if data is not None:
            for event in data.events:
                self._trigger_event(event.event_type, event.data)
                self.async_write_ha_state()
        super()._handle_coordinator_update()
