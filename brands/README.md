# Brand assets

These are the images for the [home-assistant/brands](https://github.com/home-assistant/brands)
repository. They are **not** used by the integration at runtime — Home Assistant
and HACS fetch them from the brands repository by domain, so they only take
effect once the brands PR is merged.

## Files

| File | Size | Notes |
|---|---|---|
| `custom_integrations/fleetdm/icon.png` | 256×256 | Square, trimmed |
| `custom_integrations/fleetdm/icon@2x.png` | 512×512 | Optional but included |
| `custom_integrations/fleetdm/logo.png` | 651×256 | Shortest side 256, within the 128–256 rule |
| `custom_integrations/fleetdm/logo@2x.png` | 1301×512 | Optional but included |

All are PNG with transparency and trimmed to the artwork, per the brands rules.

## Regenerating

```bash
pip install Pillow
python scripts/generate_brand_assets.py
```

The logo wordmark uses a system font and is currently generated on macOS. On a
machine without one of the fonts listed in the script, logo generation is
skipped and only the icons are written — `icon.png` is the only file HACS
strictly requires.

## About the artwork

A shield for compliance, carrying a 3×3 grid of host dots.

| | col 0 | col 1 | col 2 |
|---|---|---|---|
| **row 0** | green | green | green |
| **row 1** | green | green | white |
| **row 2** | green | white | white |

Two colours, both sampled from Fleet's own logo
(`fleetdm.com/images/logo-blue-118x41@2x.png`):

- `#63C740` — Fleet green, for the six filled dots
- `#192147` — Fleet navy, for the shield gradient and the wordmark

Fleet's logo mark is itself a 3×3 dot grid, and the three white cells here are
exactly the positions it leaves empty. Reading the green dots as passing hosts
and the white ones as neutral also happens to describe what the integration
reports, which the earlier multicolour version did not.

It does **not** use Home Assistant branding. The brands repository forbids that
for custom integrations — "Custom integrations must not use Home Assistant
branded images, as this might confuse the end-user into thinking that the
integration is an internal/official integration."

### Trademark note

The mark is Fleet-adjacent on purpose: it borrows two brand colours and a grid
arrangement so it reads as related to Fleet at a glance. It is not a copy of
Fleet's logo, and it is not the Fleet wordmark's typeface.

Fleet's name and branding are still their trademarks, and this project is not
affiliated with or endorsed by them. If you want certainty before the brands PR
makes it public, a short note to Fleet costs little — their community Slack is
active and they are generally friendly toward ecosystem projects.

**This is generated from a script, not designed.** If you want a considered
identity before it becomes the project's public face, replace these files —
nothing else depends on them.

## Submitting

1. Fork [home-assistant/brands](https://github.com/home-assistant/brands).
2. Copy `custom_integrations/fleetdm/` from this directory into the fork's
   `custom_integrations/` directory.
3. Open a PR. Brands PRs are usually reviewed quickly.

The domain directory name must be `fleetdm`, matching `domain` in
[manifest.json](../custom_components/fleetdm/manifest.json).
