"""Constants for the Fleet integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "fleetdm"

# Config entry keys
CONF_URL: Final = "url"
CONF_API_TOKEN: Final = "api_token"
CONF_VERIFY_SSL: Final = "verify_ssl"

# Options keys
CONF_SUMMARY_INTERVAL: Final = "summary_interval"
CONF_INVENTORY_INTERVAL: Final = "inventory_interval"
CONF_PER_HOST_ENTITIES: Final = "per_host_entities"
CONF_VULNERABILITY_SENSORS: Final = "vulnerability_sensors"
CONF_LABEL_SENSORS: Final = "label_sensors"
CONF_MISSING_AFTER_HOURS: Final = "missing_after_hours"
CONF_REDACT_HOSTNAMES: Final = "redact_hostnames_in_diagnostics"

# Option defaults
DEFAULT_SUMMARY_INTERVAL: Final = 60
DEFAULT_INVENTORY_INTERVAL: Final = 300
DEFAULT_VERIFY_SSL: Final = True
DEFAULT_VULNERABILITY_SENSORS: Final = True
DEFAULT_LABEL_SENSORS: Final = True
DEFAULT_MISSING_AFTER_HOURS: Final = 24
DEFAULT_REDACT_HOSTNAMES: Final = True

# Option bounds (enforced by the options flow schema)
MIN_SUMMARY_INTERVAL: Final = 30
MAX_SUMMARY_INTERVAL: Final = 3600
MIN_INVENTORY_INTERVAL: Final = 60
MAX_INVENTORY_INTERVAL: Final = 86400
MIN_MISSING_AFTER_HOURS: Final = 1
MAX_MISSING_AFTER_HOURS: Final = 720

# Fleet size above which per-host entities must be opted into explicitly.
PER_HOST_ENTITY_THRESHOLD: Final = 50

# Bus event types. Fired alongside the `event` entity so that automations can
# trigger on a plain `event` trigger with a full payload.
EVENT_POLICY_FAILING: Final = "fleetdm_policy_failing"
EVENT_POLICY_RECOVERED: Final = "fleetdm_policy_recovered"
EVENT_HOST_ENROLLED: Final = "fleetdm_host_enrolled"
EVENT_HOST_MISSING: Final = "fleetdm_host_missing"

# `event` entity event types.
EVENT_TYPE_POLICY_NEWLY_FAILING: Final = "policy_newly_failing"
EVENT_TYPE_POLICY_RECOVERED: Final = "policy_recovered"
EVENT_TYPE_HOST_ENROLLED: Final = "host_enrolled"
EVENT_TYPE_HOST_WENT_MISSING: Final = "host_went_missing"

EVENT_TYPES: Final = [
    EVENT_TYPE_POLICY_NEWLY_FAILING,
    EVENT_TYPE_POLICY_RECOVERED,
    EVENT_TYPE_HOST_ENROLLED,
    EVENT_TYPE_HOST_WENT_MISSING,
]

# Storage for drift state, so events survive a Home Assistant restart without
# either re-firing for already-failing policies or losing transitions that
# happened while Home Assistant was down.
STORAGE_VERSION: Final = 1
STORAGE_KEY_TEMPLATE: Final = f"{DOMAIN}.drift.{{entry_id}}"
STORAGE_KEY_INVENTORY_TEMPLATE: Final = f"{DOMAIN}.inventory.{{entry_id}}"

# Fleet's activity type for a host enrolling. The spec for this integration
# assumed "host_enrolled"; a live 4.x server actually emits "fleet_enrolled".
# Both are accepted so the event works across Fleet versions.
ACTIVITY_TYPES_HOST_ENROLLED: Final = frozenset({"fleet_enrolled", "host_enrolled"})

MANUFACTURER: Final = "Fleet Device Management"
