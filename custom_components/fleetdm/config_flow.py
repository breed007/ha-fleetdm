"""Config, options and reauth flows for the Fleet integration."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    FleetAuthError,
    FleetClient,
    FleetConnectionError,
    FleetError,
    normalize_url,
)
from .const import (
    CONF_API_TOKEN,
    CONF_INVENTORY_INTERVAL,
    CONF_MISSING_AFTER_HOURS,
    CONF_PER_HOST_ENTITIES,
    CONF_REDACT_HOSTNAMES,
    CONF_SUMMARY_INTERVAL,
    CONF_URL,
    CONF_VERIFY_SSL,
    CONF_VULNERABILITY_SENSORS,
    DEFAULT_INVENTORY_INTERVAL,
    DEFAULT_MISSING_AFTER_HOURS,
    DEFAULT_REDACT_HOSTNAMES,
    DEFAULT_SUMMARY_INTERVAL,
    DEFAULT_VERIFY_SSL,
    DEFAULT_VULNERABILITY_SENSORS,
    DOMAIN,
    MAX_INVENTORY_INTERVAL,
    MAX_MISSING_AFTER_HOURS,
    MAX_SUMMARY_INTERVAL,
    MIN_INVENTORY_INTERVAL,
    MIN_MISSING_AFTER_HOURS,
    MIN_SUMMARY_INTERVAL,
    PER_HOST_ENTITY_THRESHOLD,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.URL)
        ),
        vol.Required(CONF_API_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Required(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): BooleanSelector(),
    }
)

STEP_REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


async def _async_validate(hass: Any, url: str, token: str, verify_ssl: bool) -> str:
    """Validate credentials against Fleet and return the normalised base URL.

    Raises the API exception types unchanged so callers can map them to form
    errors.
    """
    base_url = normalize_url(url)
    session = async_get_clientsession(hass, verify_ssl=verify_ssl)
    client = FleetClient(session, base_url, token)
    await client.async_get_version()
    return base_url


class FleetConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Fleet config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the Fleet URL and API token."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                base_url = await _async_validate(
                    self.hass,
                    user_input[CONF_URL],
                    user_input[CONF_API_TOKEN],
                    user_input[CONF_VERIFY_SSL],
                )
            except FleetAuthError:
                errors["base"] = "invalid_auth"
            except (FleetConnectionError, ValueError):
                errors["base"] = "cannot_connect"
            except FleetError:
                errors["base"] = "unknown"
            except Exception:
                _LOGGER.exception("Unexpected error validating Fleet connection")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(base_url)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=urlsplit(base_url).hostname or "Fleet",
                    data={**user_input, CONF_URL: base_url},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle a token that Fleet has started rejecting."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Prompt for a replacement API token."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            try:
                await _async_validate(
                    self.hass,
                    reauth_entry.data[CONF_URL],
                    user_input[CONF_API_TOKEN],
                    reauth_entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                )
            except FleetAuthError:
                errors["base"] = "invalid_auth"
            except (FleetConnectionError, ValueError):
                errors["base"] = "cannot_connect"
            except FleetError:
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates={CONF_API_TOKEN: user_input[CONF_API_TOKEN]},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={"url": reauth_entry.data[CONF_URL]},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update connection settings without deleting the entry.

        This is the supported path for planned API token rotation, where the old
        token still works and there is no 401 to trigger reauth.
        """
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            try:
                base_url = await _async_validate(
                    self.hass,
                    user_input[CONF_URL],
                    user_input[CONF_API_TOKEN],
                    user_input[CONF_VERIFY_SSL],
                )
            except FleetAuthError:
                errors["base"] = "invalid_auth"
            except (FleetConnectionError, ValueError):
                errors["base"] = "cannot_connect"
            except FleetError:
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(base_url)
                # Repointing an existing entry at a different Fleet server would
                # silently orphan its entities and drift history.
                self._abort_if_unique_id_mismatch(reason="wrong_server")
                return self.async_update_reload_and_abort(
                    entry, data_updates={**user_input, CONF_URL: base_url}
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA,
                user_input
                or {
                    CONF_URL: entry.data[CONF_URL],
                    CONF_VERIFY_SSL: entry.data.get(
                        CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL
                    ),
                },
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> FleetOptionsFlow:
        """Return the options flow handler."""
        return FleetOptionsFlow()


class FleetOptionsFlow(OptionsFlow):
    """Handle Fleet integration options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options

        # Default the per-host toggle to whatever the size rule would choose, so
        # the form shows what is actually happening rather than a blank control
        # the user has to guess the meaning of.
        host_count = 0
        runtime = getattr(self.config_entry, "runtime_data", None)
        if runtime is not None and runtime.inventory.data is not None:
            host_count = len(runtime.inventory.data.hosts)
        per_host_default = options.get(
            CONF_PER_HOST_ENTITIES, host_count <= PER_HOST_ENTITY_THRESHOLD
        )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SUMMARY_INTERVAL,
                    default=options.get(
                        CONF_SUMMARY_INTERVAL, DEFAULT_SUMMARY_INTERVAL
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SUMMARY_INTERVAL,
                        max=MAX_SUMMARY_INTERVAL,
                        step=1,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_INVENTORY_INTERVAL,
                    default=options.get(
                        CONF_INVENTORY_INTERVAL, DEFAULT_INVENTORY_INTERVAL
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_INVENTORY_INTERVAL,
                        max=MAX_INVENTORY_INTERVAL,
                        step=1,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_PER_HOST_ENTITIES, default=per_host_default
                ): BooleanSelector(),
                vol.Required(
                    CONF_MISSING_AFTER_HOURS,
                    default=options.get(
                        CONF_MISSING_AFTER_HOURS, DEFAULT_MISSING_AFTER_HOURS
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_MISSING_AFTER_HOURS,
                        max=MAX_MISSING_AFTER_HOURS,
                        step=1,
                        unit_of_measurement="h",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_VULNERABILITY_SENSORS,
                    default=options.get(
                        CONF_VULNERABILITY_SENSORS, DEFAULT_VULNERABILITY_SENSORS
                    ),
                ): BooleanSelector(),
                vol.Required(
                    CONF_REDACT_HOSTNAMES,
                    default=options.get(
                        CONF_REDACT_HOSTNAMES, DEFAULT_REDACT_HOSTNAMES
                    ),
                ): BooleanSelector(),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            description_placeholders={"host_count": str(host_count)},
        )
