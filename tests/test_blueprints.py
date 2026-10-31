"""End-to-end tests for the automation blueprints in blueprints/automation/fleetdm.

Each blueprint is installed into a test Home Assistant, instantiated as an
automation, and triggered the way the integration actually would, so a typo in
an event type or input name fails here rather than silently never firing.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.fleetdm.const import (
    EVENT_HOST_ENROLLED,
    EVENT_HOST_MISSING,
    EVENT_POLICY_FAILING,
)

SOURCE = Path(__file__).parents[1] / "blueprints" / "automation" / "fleetdm"
RAN = "fleetdm_blueprint_ran"


@pytest.fixture(autouse=True)
def install_blueprints(hass, tmp_path) -> None:
    """Copy the blueprints into a throwaway config directory."""
    hass.config.config_dir = str(tmp_path)
    target = tmp_path / "blueprints" / "automation" / "fleetdm"
    target.mkdir(parents=True)
    for blueprint in SOURCE.glob("*.yaml"):
        shutil.copy(blueprint, target / blueprint.name)


async def use_blueprint(hass, name: str, **inputs: Any) -> None:
    """Set up one automation from a blueprint, recording what its actions see."""
    actions = [{"event": RAN, "event_data": {"seen": "{{ trigger.platform }}"}}]
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "use_blueprint": {
                    "path": f"fleetdm/{name}",
                    "input": {"actions": actions, **inputs},
                }
            }
        },
    )
    await hass.async_block_till_done()
    automations = hass.states.async_entity_ids("automation")
    assert len(automations) == 1
    # A blueprint that fails validation still creates an automation, but an
    # unavailable one; this catches that.
    assert hass.states.get(automations[0]).state == "on"


def test_every_blueprint_is_tested() -> None:
    """A new blueprint must come with a test here."""
    assert sorted(p.name for p in SOURCE.glob("*.yaml")) == [
        "host_enrolled.yaml",
        "host_missing.yaml",
        "policy_failing.yaml",
        "unencrypted_disk.yaml",
    ]


@pytest.mark.parametrize(
    ("name", "event_type"),
    [
        ("host_missing.yaml", EVENT_HOST_MISSING),
        ("host_enrolled.yaml", EVENT_HOST_ENROLLED),
        ("policy_failing.yaml", EVENT_POLICY_FAILING),
    ],
)
async def test_event_blueprints_fire(hass, name, event_type) -> None:
    """Each event blueprint runs its actions on the integration's bus event."""
    ran = async_capture_events(hass, RAN)
    await use_blueprint(hass, name)

    hass.bus.async_fire(event_type, {"host_name": "Ada Laptop", "critical": False})
    await hass.async_block_till_done()

    assert len(ran) == 1
    assert ran[0].data["seen"] == "event"


async def test_policy_blueprint_critical_only(hass) -> None:
    """With critical only on, a non-critical policy is ignored."""
    ran = async_capture_events(hass, RAN)
    await use_blueprint(hass, "policy_failing.yaml", critical_only=True)

    hass.bus.async_fire(EVENT_POLICY_FAILING, {"policy_name": "A", "critical": False})
    await hass.async_block_till_done()
    assert ran == []

    hass.bus.async_fire(EVENT_POLICY_FAILING, {"policy_name": "B", "critical": True})
    await hass.async_block_till_done()
    assert len(ran) == 1


async def test_unencrypted_disk_blueprint(hass) -> None:
    """The disk blueprint fires when the count rises above zero."""
    entity_id = "sensor.fleet_hosts_without_disk_encryption"
    hass.states.async_set(entity_id, "0")
    ran = async_capture_events(hass, RAN)
    await use_blueprint(hass, "unencrypted_disk.yaml", sensor=entity_id)

    hass.states.async_set(entity_id, "2", {"hosts": ["Grace Desktop", "Host 7"]})
    await hass.async_block_till_done()

    assert len(ran) == 1
    assert ran[0].data["seen"] == "numeric_state"
