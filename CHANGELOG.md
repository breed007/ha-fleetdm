# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-08-07

First release. Fleet-level monitoring and per-policy compliance, read-only.

### Added

- Config flow with connection validation against `/api/latest/fleet/version`,
  URL normalisation, and duplicate-server detection
- Reauth flow, triggered automatically when Fleet rejects the stored token
- Reconfigure flow, for planned API token rotation before the old token is
  revoked
- Fleet-level sensors: hosts online, offline, missing, new, total, and failing
  policy count
- `binary_sensor.fleet_compliance` problem sensor, tracking critical policies on
  Fleet Premium and all policies on Fleet Free
- Per-policy problem binary sensors, created and removed dynamically as policies
  change in Fleet, keyed on the stable Fleet policy ID so renames follow
- Per-policy failing-host-count sensors, disabled by default
- Compliance drift events on the bus (`fleetdm_policy_failing`,
  `fleetdm_policy_recovered`) and an `event.fleet_fleet_events` entity
- Diagnostics with unconditional API token redaction and optional hostname
  redaction, on by default
- Options for poll interval and diagnostics redaction

### Notes

- Fleet Free is fully supported. The Premium-only `critical` policy flag is
  detected at setup, and the compliance sensor falls back to watching all
  policies when it is unavailable
- Drift events fire once per transition. Adding the integration to a fleet with
  existing failures does not fire an event storm, and restarting Home Assistant
  neither duplicates nor loses events
- Host counts come from `/host_summary` in a single request per cycle
- Global policies are paginated explicitly, so a large policy library is not
  silently truncated by a server-side page size default
- Fleet renamed its global policies route when it dropped "global" from its
  team terminology. Both spellings are supported: the integration probes
  `/policies` then `/global/policies` and caches whichever the server answers,
  so current and older Fleet releases both work
- Verified against a live Fleet server: 14 hosts, 46 global policies, Free
  tier, with the drift baseline seeding silently rather than firing an event
  for each of the 23 already-failing policies

[Unreleased]: https://github.com/breed007/ha-fleetdm/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/breed007/ha-fleetdm/releases/tag/v0.1.0
