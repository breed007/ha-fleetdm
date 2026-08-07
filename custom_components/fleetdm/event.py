"""Event platform for the Fleet integration.

The `event` entity is the timeline/UI surface for everything Fleet tells us has
changed. For automations prefer the matching bus events
(``fleetdm_policy_failing``, ``fleetdm_policy_recovered``,
``fleetdm_host_enrolled`` and ``fleetdm_host_missing``), which the coordinators
fire once per transition with the full payload — an `event` entity can only hold
one event at a time, so several transitions in the same poll are best consumed
from the bus.
"""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import FleetConfigEntry
from .const import EVENT_TYPES
from .coordinator import FleetInventoryCoordinator, FleetSummaryCoordinator
from .entity import FleetEntity, fleet_unique_id


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FleetConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Fleet event entity."""
    async_add_entities(
        [
            FleetEventEntity(
                entry.runtime_data.summary, entry.runtime_data.inventory, entry
            )
        ]
    )


class FleetEventEntity(FleetEntity, EventEntity):
    """Surfaces Fleet compliance and host events as Home Assistant events.

    Listens to both coordinators. Policy drift arrives on the fast summary
    cycle, host enrolment and host-missing on the slower inventory cycle, but
    they belong on one timeline rather than being split across two entities by
    an implementation detail.
    """

    _attr_event_types = EVENT_TYPES
    _attr_translation_key = "fleet_events"

    def __init__(
        self,
        coordinator: FleetSummaryCoordinator,
        inventory: FleetInventoryCoordinator,
        entry: FleetConfigEntry,
    ) -> None:
        """Initialise the event entity."""
        super().__init__(coordinator, entry)
        self._inventory = inventory
        self._attr_unique_id = fleet_unique_id(entry.entry_id, "events")

    async def async_added_to_hass(self) -> None:
        """Subscribe to the inventory coordinator as well as the summary one."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._inventory.async_add_listener(self._handle_inventory_update)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Replay this cycle's policy drift events onto the entity."""
        if (data := self.coordinator.data) is not None:
            self._replay(data.events)
        super()._handle_coordinator_update()

    @callback
    def _handle_inventory_update(self) -> None:
        """Replay this cycle's host events onto the entity."""
        if (data := self._inventory.data) is not None:
            self._replay(data.events)

    @callback
    def _replay(self, events) -> None:
        """Trigger each event, writing state so automations see every one."""
        for event in events:
            self._trigger_event(event.event_type, event.data)
            self.async_write_ha_state()
