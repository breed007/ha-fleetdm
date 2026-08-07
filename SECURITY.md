# Security Policy

## Reporting a vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

Use GitHub's private vulnerability reporting:
[Report a vulnerability](https://github.com/breed007/ha-fleetdm/security/advisories/new).

Please include the affected version, what an attacker can achieve, and steps to
reproduce. You will get an acknowledgement, and I will let you know when a fix
ships and credit you unless you would rather stay anonymous.

This is a hobby project maintained in spare time — please set your expectations
for response time accordingly. It is not a commercial product with an SLA.

## Scope

This integration talks to a Fleet server that manages endpoints, so its security
properties matter more than a typical Home Assistant integration.

**In scope:**

- Leaking the Fleet API token (logs, diagnostics, config entry handling, error
  messages)
- Any code path that sends a non-`GET` request to Fleet
- SSL verification being bypassed when the user has not opted out
- Diagnostics redaction failing to redact what it claims to redact

**Out of scope:**

- Vulnerabilities in Fleet itself — report those to
  [Fleet's security process](https://github.com/fleetdm/fleet/security)
- Vulnerabilities in Home Assistant core
- The user choosing to disable SSL verification, or choosing to configure a
  higher-privileged token than the documented Observer role

## Design commitments

These are properties the integration intends to hold. A break in any of them is
a valid security report:

1. **Read-only.** Only `GET` requests are issued, to `/version`, `/config`,
   `/host_summary`, and `/global/policies`. There is no code path that writes to
   Fleet, executes queries, or modifies hosts.
2. **Least privilege.** A Fleet Observer token is sufficient. Where a token
   cannot read an endpoint, the integration degrades instead of demanding more
   privilege.
3. **The token is never written to diagnostics**, regardless of user settings.
4. **The token is never logged**, including in error messages.

## Token handling

The Fleet API token is stored in the Home Assistant config entry, like every
other integration credential. It is protected by whatever protects your Home
Assistant configuration directory — treat that directory as holding a
credential to your device management platform.

Use a dedicated API-only user with the Observer role, as described in the
[README](README.md#creating-an-api-token-do-this-first), and rotate it with the
integration's Reconfigure flow rather than reusing a personal account's token.
