# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Per-label host count sensors.** One sensor per Fleet label showing how many
  hosts match it, created and removed as labels change. Fleet's built-in labels
  are registered but **disabled by default**: several are always empty on any
  given fleet, and "All Hosts" only restates `sensor.fleet_hosts_total`. Labels
  you created yourself are enabled, since those encode a distinction you chose
  to define. Labels work on Fleet Free.
- A `label_sensors` option to turn the whole set off, which also stops the
  request being made.

### Fixed

- Label membership is read from Fleet's `count` field rather than `host_count`.
  Fleet omits `host_count` entirely for a label with no hosts but always sends
  `count`, so reading the former would have made every empty label report
  nothing at all instead of zero. Verified against a live server, where 7 of 19
  labels were affected.

### Changed

- The three near-identical dynamic-entity helpers for policies, hosts and labels
  now share one implementation, parameterised by how to read IDs from the
  coordinator. Adding a third copy would have been worse than collapsing them.

## [0.2.0] - 2026-08-07

Phase 2. Per-host visibility, vulnerable software, and events for hosts
enrolling or going quiet. No breaking changes: everything from 0.1.x keeps
working unchanged, and the new per-host entities appear automatically on
fleets of 50 hosts or fewer.

### Added

- **Per-host devices and entities.** Each enrolled host becomes its own device
  linked to the Fleet hub, with online and missing binary sensors, a failing
  policy count, and a boot-time sensor (disabled by default). Created
  automatically for fleets of 50 hosts or fewer; above that they are opt-in, so
  a large fleet cannot produce thousands of entities by surprise.
- **`sensor.fleet_vulnerable_software`** — the number of software titles with
  known CVEs, with the most widespread titles as an attribute. Deliberately
  carries no severity: CVSS and EPSS are Fleet Premium fields, and inventing a
  severity from the CVE count would be worse than omitting it.
- **`fleetdm_host_enrolled` and `fleetdm_host_missing` events**, alongside the
  existing policy drift events and on the same event entity. Both follow the
  same rules: nothing fires at setup, one event per transition, and no
  duplicates or losses across a restart.
- Options for the inventory interval, per-host entities, the missing threshold
  and the vulnerable software sensor. Phase 1 deliberately shipped only the
  options it honoured; these now control real behaviour.
- A second, slower "inventory" coordinator for the host list, vulnerable
  software and activity feed, keeping the expensive calls off the fast cycle.

### Notes

- Fleet's host enrolment activity is `fleet_enrolled`, not the `host_enrolled`
  this project's own spec assumed. Both are accepted so the event works across
  Fleet versions.
- Per-host data comes from the `/hosts` list, which already carries
  `issues.failing_policies_count`. No per-host detail request is made, so a
  large fleet costs one paginated read rather than a request per host.
- Diagnostics now contain host names and IP addresses. Hostname redaction is on
  by default and covers them; software titles are kept, since they describe what
  is installed rather than who runs it.

## [0.1.1] - 2026-08-07

Icon and documentation only. No functional change to the integration.

### Added

- hDPI `icon@2x.png` and `logo@2x.png`, which were missing from the brand folder
  so high-density displays fell back to the smaller images
- Screenshots in the README: the setup dialog, and the device page showing host
  counts beside per-policy compliance sensors

### Changed

- The icon and logo now ship only in `custom_components/fleetdm/brand/`, which
  Home Assistant 2026.3+ reads directly. Home Assistant no longer accepts custom
  integration icons into its brands repository, so the duplicate copy staged for
  that submission has been removed. Users on Home Assistant 2025.2–2026.2 see the
  generic placeholder icon; this is cosmetic and closes as users upgrade.

### Documentation

- Recommend configuring Fleet by hostname rather than IP. A certificate issued
  for the hostname fails verification when connecting by IP, and the tempting
  fix is to disable verification rather than use the name the certificate is for.

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

[Unreleased]: https://github.com/breed007/ha-fleetdm/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/breed007/ha-fleetdm/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/breed007/ha-fleetdm/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/breed007/ha-fleetdm/releases/tag/v0.1.0
