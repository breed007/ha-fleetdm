# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Meets every rule of Home Assistant's integration quality scale, so the manifest
now declares **platinum**.

### Added

- **A repair notice when a list is cut short.** Hosts and policies are read up
  to a 2,000-item safety limit. Past it, the extra objects silently had no
  entities and fired no events, with only a log line to say so. That now shows
  under Settings → System → Repairs, and clears itself once the list fits.
- **Translated error messages.** Connection, authentication, permission and
  unexpected-response failures from Fleet are raised with translation keys
  instead of English strings built in the API client.
- **Strict type checking** in CI: mypy with the settings Home Assistant core
  applies to its strictly typed integrations.

### Fixed

- **Host devices used a deprecated way to link to the Fleet hub.** On Home
  Assistant 2026.9 this logged a deprecation warning per host device, and it
  stops working in 2027.8. Host devices now link by the hub's registry ID where
  Home Assistant supports it, and the hub device is registered at setup so that
  ID always exists.
- A Fleet response that is valid JSON but not an object is now reported as an
  unexpected response instead of failing later with an `AttributeError`.

### Changed

- The pagination safety cap for hosts and policies now logs at debug level;
  the repair notice replaces the warning that was repeated on every poll.

- CI now tests against Home Assistant 2026.9, replacing 2026.7. The tests use
  the scoped device-registry lookup that 2026.9 requires, falling back to the
  old lookup on 2025.2, which predates it. The integration code itself needed
  no change.
- US English throughout code, comments and docs. User-facing strings,
  entity IDs and event payloads are unchanged.

## [0.4.0] - 2026-08-09

A bug-fix and hardening release, plus five new sensors. Several fixes change
behavior you will notice — those are marked below.

### Added

- Per-host **disk free** as a percentage (enabled) and in gigabytes (disabled),
  plus **osquery version** and **MDM enrollment status** as disabled diagnostics.
  All read from the host list already being fetched, so they cost nothing extra.
- **`sensor.fleet_hosts_on_vulnerable_os`** — how many hosts run an OS version
  with known CVEs, with the full OS spread as an attribute. Fleet aggregates
  this server-side, so it is one request whatever the fleet size.
- Opt-in **`fleetdm_activity`** events covering the rest of Fleet's audit feed.
  The Fleet activity type travels in the payload rather than becoming its own
  Home Assistant event type, so new Fleet activity types work without a release
  here. Off by default: an active Fleet writes a great many of these.
- `quality_scale.yaml` declaring silver, with exemptions recorded and three
  rules honestly marked todo.

### Fixed

- **Premium detection swallowed authentication and connection errors.** It
  caught `FleetError`, which `FleetAuthError` and `FleetConnectionError` both
  subclass, so a rejected token never reached the reauth flow and a brief
  network failure silently pinned the integration to Free-tier compliance
  semantics until the next reload.
- **Hosts deleted from Fleet left orphaned devices** in the registry, and could
  not be removed from the UI either. Devices are now reconciled on every
  inventory refresh, and manual removal is allowed once Fleet has dropped the
  host. *(User-visible: stale host devices will disappear on upgrade.)*
- **Host device details went stale.** Name, model and OS version were captured
  once when the entity was created, so a rename or OS upgrade in Fleet never
  reached Home Assistant without a reload.
- **A host that never checked in could never be reported missing** — the most
  missing a host can be. It now falls back to enrollment time for a grace period.
  *(User-visible: such hosts will start reporting missing.)*
- **Saving the options form disabled per-host auto-gating permanently.** The
  setting is now tri-state (auto/on/off) and round-trips unchanged; the gate is
  also re-evaluated on every refresh instead of only at platform setup.
- The event entity now derives availability from both coordinators, so a
  persistently failing inventory coordinator can no longer stop host events
  while still appearing healthy.
- The activity feed warns when the pagination cap truncates it, matching the
  hosts and policies paths.
- Reauth and reconfigure gained the catch-all error guard the initial setup step
  already had, so unexpected failures show a form error rather than a traceback.
- Server version and license tier are re-read periodically instead of once at
  setup, so a Fleet upgrade or license change is picked up without a reload.

### Changed

- `PARALLEL_UPDATES = 0` on all platforms.
- Entity names embedding a policy or label name use translation placeholders
  instead of f-strings, so the surrounding wording is translatable. Renames
  still follow, which needed care: Home Assistant caches entity names.
- CI actions pinned to commits rather than moving branches.

## [0.3.0] - 2026-08-08

Per-label host counts. Labels are available on Fleet Free, so unlike teams
this works on every tier.

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
  now share one implementation, parameterized by how to read IDs from the
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
  options it honored; these now control real behavior.
- A second, slower "inventory" coordinator for the host list, vulnerable
  software and activity feed, keeping the expensive calls off the fast cycle.

### Notes

- Fleet's host enrollment activity is `fleet_enrolled`, not the `host_enrolled`
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
  URL normalization, and duplicate-server detection
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

[Unreleased]: https://github.com/breed007/ha-fleetdm/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/breed007/ha-fleetdm/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/breed007/ha-fleetdm/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/breed007/ha-fleetdm/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/breed007/ha-fleetdm/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/breed007/ha-fleetdm/releases/tag/v0.1.0
