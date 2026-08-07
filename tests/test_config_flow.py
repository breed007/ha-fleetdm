"""Tests for the Fleet config, options and reauth flows."""

from __future__ import annotations

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType

from custom_components.fleetdm.const import (
    CONF_API_TOKEN,
    CONF_REDACT_HOSTNAMES,
    CONF_SUMMARY_INTERVAL,
    CONF_URL,
    CONF_VERIFY_SSL,
    DOMAIN,
)

from .conftest import API, BASE_URL, VERSION_RESPONSE, mock_fleet

USER_INPUT = {
    CONF_URL: BASE_URL,
    CONF_API_TOKEN: "test-token",
    CONF_VERIFY_SSL: True,
}


async def test_user_flow_success(hass, aioclient_mock) -> None:
    """A valid URL and token creates an entry keyed on the normalised URL."""
    mock_fleet(aioclient_mock)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "fleet.example.com"
    assert result["data"][CONF_URL] == BASE_URL
    assert result["data"][CONF_API_TOKEN] == "test-token"
    assert result["result"].unique_id == BASE_URL


@pytest.mark.parametrize(
    ("raw_url", "expected"),
    [
        ("https://fleet.example.com/", BASE_URL),
        ("fleet.example.com", BASE_URL),
        ("  https://fleet.example.com  ", BASE_URL),
    ],
)
async def test_url_normalisation(hass, aioclient_mock, raw_url, expected) -> None:
    """Equivalent URL spellings normalise to one unique ID."""
    mock_fleet(aioclient_mock)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_URL: raw_url}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == expected


async def test_invalid_auth(hass, aioclient_mock) -> None:
    """A 401 from Fleet shows the invalid_auth error and lets the user retry."""
    aioclient_mock.get(f"{API}/version", status=401)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    # Recovering with a good token in the same flow works.
    aioclient_mock.clear_requests()
    mock_fleet(aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_cannot_connect(hass, aioclient_mock) -> None:
    """A connection failure shows the cannot_connect error."""
    aioclient_mock.get(f"{API}/version", exc=TimeoutError())

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_unknown_error(hass, aioclient_mock) -> None:
    """A 500 from Fleet surfaces as the generic unknown error."""
    aioclient_mock.get(f"{API}/version", status=500)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}


async def test_non_json_response_is_unknown(hass, aioclient_mock) -> None:
    """Pointing at something that is not a Fleet server fails cleanly."""
    aioclient_mock.get(f"{API}/version", text="<html>not fleet</html>")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}


async def test_duplicate_aborts(hass, aioclient_mock, mock_config_entry) -> None:
    """Adding the same Fleet server twice aborts."""
    mock_config_entry.add_to_hass(hass)
    mock_fleet(aioclient_mock)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_duplicate_aborts_on_equivalent_url(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """A differently spelled URL for a configured server still aborts."""
    mock_config_entry.add_to_hass(hass)
    mock_fleet(aioclient_mock)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_URL: "https://fleet.example.com/"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_success(hass, aioclient_mock, mock_config_entry) -> None:
    """Reauth replaces the token on the existing entry."""
    mock_config_entry.add_to_hass(hass)
    mock_fleet(aioclient_mock)

    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: "new-token"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_API_TOKEN] == "new-token"
    # The URL is untouched by reauth.
    assert mock_config_entry.data[CONF_URL] == BASE_URL


async def test_reauth_rejects_bad_token(
    hass, aioclient_mock, mock_config_entry
) -> None:
    """A still-invalid token keeps the user on the reauth form."""
    mock_config_entry.add_to_hass(hass)
    aioclient_mock.get(f"{API}/version", status=401)

    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: "still-bad"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert mock_config_entry.data[CONF_API_TOKEN] == "test-token"


async def test_options_flow(hass, setup_integration) -> None:
    """Options are persisted and the entry reloads."""
    result = await hass.config_entries.options.async_init(setup_integration.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SUMMARY_INTERVAL: 120, CONF_REDACT_HOSTNAMES: False},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert setup_integration.options[CONF_SUMMARY_INTERVAL] == 120
    assert setup_integration.options[CONF_REDACT_HOSTNAMES] is False


async def test_options_change_poll_interval(hass, setup_integration) -> None:
    """A new summary interval is applied to the coordinator after reload."""
    result = await hass.config_entries.options.async_init(setup_integration.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SUMMARY_INTERVAL: 300, CONF_REDACT_HOSTNAMES: True},
    )
    await hass.async_block_till_done()

    coordinator = setup_integration.runtime_data.summary
    assert coordinator.update_interval.total_seconds() == 300


async def test_reconfigure_rotates_token(
    hass, aioclient_mock, setup_integration
) -> None:
    """Reconfigure swaps the token in place, the path for planned rotation."""
    result = await setup_integration.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_URL: BASE_URL, CONF_API_TOKEN: "rotated-token", CONF_VERIFY_SSL: True},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert setup_integration.data[CONF_API_TOKEN] == "rotated-token"


async def test_reconfigure_rejects_different_server(
    hass, aioclient_mock, setup_integration
) -> None:
    """Repointing an entry at another Fleet server is refused."""
    other = "https://fleet2.example.com"
    aioclient_mock.get(f"{other}/api/latest/fleet/version", json=VERSION_RESPONSE)

    result = await setup_integration.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_URL: other, CONF_API_TOKEN: "test-token", CONF_VERIFY_SSL: True},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_server"
    assert setup_integration.data[CONF_URL] == BASE_URL


async def test_unexpected_exception_is_unknown(hass, aioclient_mock) -> None:
    """An unforeseen exception is caught and shown as the unknown error."""
    aioclient_mock.get(f"{API}/version", exc=RuntimeError("boom"))

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}


@pytest.mark.parametrize(
    ("mock_kwargs", "expected"),
    [
        ({"exc": TimeoutError()}, "cannot_connect"),
        ({"status": 500}, "unknown"),
    ],
)
async def test_reauth_transport_errors(
    hass, aioclient_mock, mock_config_entry, mock_kwargs, expected
) -> None:
    """Reauth surfaces connection and server errors distinctly from bad tokens."""
    mock_config_entry.add_to_hass(hass)
    aioclient_mock.get(f"{API}/version", **mock_kwargs)

    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: "new-token"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}


@pytest.mark.parametrize(
    ("mock_kwargs", "expected"),
    [
        ({"status": 401}, "invalid_auth"),
        ({"exc": TimeoutError()}, "cannot_connect"),
        ({"status": 500}, "unknown"),
    ],
)
async def test_reconfigure_errors(
    hass, aioclient_mock, setup_integration, mock_kwargs, expected
) -> None:
    """Reconfigure keeps the user on the form when the new settings fail."""
    result = await setup_integration.start_reconfigure_flow(hass)

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/version", **mock_kwargs)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_URL: BASE_URL, CONF_API_TOKEN: "bad", CONF_VERIFY_SSL: True},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}
    assert setup_integration.data[CONF_API_TOKEN] == "test-token"
