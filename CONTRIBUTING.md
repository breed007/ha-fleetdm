# Contributing

Thanks for your interest. This is a read-only Home Assistant integration for
[Fleet](https://fleetdm.com), and contributions are welcome.

## Getting set up

Home Assistant 2026.7 and later require Python 3.14. If you are testing against
an older Home Assistant release, match its Python requirement — the CI matrix in
[.github/workflows/pytest.yml](.github/workflows/pytest.yml) lists the pairings.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-test.txt
```

Run the checks CI runs:

```bash
.venv/bin/python -m pytest tests/ --cov=custom_components.fleetdm --cov-report=term-missing
.venv/bin/ruff check custom_components/ tests/
.venv/bin/ruff format --check custom_components/ tests/
```

To test against a specific Home Assistant release, install the matching
`pytest-homeassistant-custom-component` pin instead of `requirements-test.txt`.

## What we look for in a PR

- **Tests.** Coverage is currently 98% and CI enforces a floor of 85%. Bug fixes
  should come with a test that fails without the fix.
- **No new runtime dependencies.** The integration deliberately has an empty
  `requirements` list in `manifest.json`; it uses Home Assistant's shared
  aiohttp session and nothing else.
- **`strings.json` and `translations/en.json` must stay identical.** Any
  user-facing string goes in both.
- **Free tier must keep working.** Fleet Premium-only fields (`critical`, and
  vulnerability severity in later phases) must degrade rather than error. See
  `compliance_problem` in [coordinator.py](custom_components/fleetdm/coordinator.py)
  for the pattern.

## The read-only boundary

This integration issues `GET` requests only. It has no code path that writes to
Fleet, runs live queries, or modifies hosts, and the documented setup is a
least-privilege Observer token.

Please do not open PRs that add host-modifying behaviour, MDM commands, or
script execution. Running *pre-existing saved queries* is on the roadmap for a
later phase, and needs a deliberate design with the privilege trade-off spelled
out for users — not an incremental PR.

If you are adding a new Fleet API call, add it to
[api.py](custom_components/fleetdm/api.py) and keep the exception mapping
intact: `401` means reauth, `402`/`403` mean "this role or licence tier cannot
do this" and must degrade gracefully.

## Drift events

Compliance drift is the feature most people install this for, and it is the
easiest thing to break. If you touch `_compute_drift`, the tests in
[tests/test_drift.py](tests/test_drift.py) encode the contract:

- No event storm when the integration is first added
- Exactly one event per transition, never repeated while the state persists
- No duplicates across a Home Assistant restart
- No lost transitions that happened while Home Assistant was down
- Deleting a failing policy is not a "recovered" event

## Reporting bugs

Use the issue templates. For anything involving wrong values or missing
entities, attach a diagnostics download — the API token is always redacted, and
hostname redaction is on by default.

## Security issues

Please do not open a public issue. See [SECURITY.md](SECURITY.md).
