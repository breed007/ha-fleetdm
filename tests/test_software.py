"""Tests for the vulnerable software sensor."""

from __future__ import annotations

from custom_components.fleetdm.const import CONF_VULNERABILITY_SENSORS

from .conftest import mock_fleet
from .test_drift import setup_with

SENSOR = "sensor.fleet_vulnerable_software"


async def test_vulnerable_software_count(hass, setup_integration) -> None:
    """The sensor reports the exact total Fleet gives, not the sample size."""
    state = hass.states.get(SENSOR)
    assert state is not None
    # 151 titles in total, even though only 2 came back in the sample.
    assert state.state == "151"
    assert state.attributes["counts_updated_at"] == "2026-08-07T15:00:00+00:00"


async def test_most_widespread_attribute(hass, setup_integration) -> None:
    """The attribute lists the worst titles with de-duplicated CVE counts."""
    worst = hass.states.get(SENSOR).attributes["most_widespread"]

    assert [t["name"] for t in worst] == ["Google Chrome", "curl"]
    # Chrome has CVE-1, CVE-2 on one version and CVE-2, CVE-3 on another:
    # three distinct CVEs, not four.
    assert worst[0]["cve_count"] == 3
    assert worst[0]["hosts_count"] == 9
    assert worst[1]["cve_count"] == 1


async def test_no_invented_severity(hass, setup_integration) -> None:
    """Severity is Fleet Premium only, so it must not appear on Free."""
    attributes = hass.states.get(SENSOR).attributes
    assert "severity" not in attributes
    for title in attributes["most_widespread"]:
        assert "severity" not in title
        assert "cvss" not in title


async def test_sensor_absent_when_disabled(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """Turning the option off removes the sensor and stops the request."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={CONF_VULNERABILITY_SENSORS: False}
    )
    mock_fleet(aioclient_mock)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(SENSOR) is None
    requested = [str(call[1]) for call in aioclient_mock.mock_calls]
    assert not any("software/titles" in url for url in requested)


async def test_survives_empty_software_response(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """A fleet with nothing vulnerable reports zero rather than erroring."""
    await setup_with(
        hass,
        mock_config_entry,
        aioclient_mock,
        software={"count": 0, "software_titles": []},
    )

    state = hass.states.get(SENSOR)
    assert state.state == "0"
    assert state.attributes["most_widespread"] == []
    assert state.attributes["counts_updated_at"] is None
