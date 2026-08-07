## What does this change?

<!-- Brief description. Link the issue it closes, if there is one. -->

## Checklist

- [ ] `pytest tests/` passes
- [ ] `ruff check` and `ruff format --check` pass
- [ ] New or changed behaviour has a test
- [ ] User-facing strings updated in **both** `strings.json` and `translations/en.json`
- [ ] No new entries in `manifest.json` `requirements`

## If this touches the Fleet API

- [ ] Only `GET` requests are issued
- [ ] `401` still raises reauth; `402`/`403` still degrade gracefully
- [ ] Works with a least-privilege Observer token
- [ ] Fleet Free still works — no Premium-only field is required

## If this touches drift detection

Confirm the contract in `tests/test_drift.py` still holds:

- [ ] No event storm when the integration is first added
- [ ] Exactly one event per transition
- [ ] No duplicate events across a Home Assistant restart
- [ ] No lost transitions from while Home Assistant was down
