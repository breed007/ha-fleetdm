# Brand assets

The integration ships its own icon and logo in
[`custom_components/fleetdm/brand/`](../custom_components/fleetdm/brand). Home
Assistant reads them from there directly.

| File | Size |
|---|---|
| `icon.png` | 256×256 |
| `icon@2x.png` | 512×512 |
| `logo.png` | 651×256 (shortest side within the 128–256 rule) |
| `logo@2x.png` | 1301×512 (shortest side within the 256–512 rule) |

All PNG with transparency, trimmed to the artwork.

## Regenerating

```bash
pip install Pillow
python scripts/generate_brand_assets.py
```

The wordmark in the logo uses a system font and is currently generated on macOS.
On a machine without one of the fonts listed in the script, logo generation is
skipped and only the icons are written — `icon.png` is the only file HACS
strictly requires.

## Do not submit these to home-assistant/brands

Home Assistant **no longer accepts brand icons for custom integrations** in the
[home-assistant/brands](https://github.com/home-assistant/brands) repository.
Since Home Assistant 2026.3, custom integrations provide their own icons from
the `brand/` folder above, and that is the only supported route. See the
[Brands Proxy API announcement](https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api).

We tried anyway ([brands#10930](https://github.com/home-assistant/brands/pull/10930))
and it was closed with exactly that explanation, even though its CI passed. The
CI passing is not a signal that the submission is wanted.

### Consequence for older Home Assistant

The `brand/` mechanism only works on **2026.3 and later**. This integration
supports 2025.2+, so users between 2025.2 and 2026.2 see Home Assistant's
generic placeholder icon instead.

That is cosmetic only — nothing functional depends on it — and it is not worth
raising the minimum Home Assistant version to fix. The gap closes on its own as
users upgrade.

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
and the white ones as neutral also describes what the integration reports.

It does **not** use Home Assistant branding, which would wrongly imply this is
an official integration.

## Known: HACS shows "icon not available"

The icon renders correctly on **Settings → Devices & Services**, which is where
Home Assistant itself draws it. HACS's own dashboard and update cards show a
grey *"icon not available"* placeholder instead.

That is a HACS bug, not a problem with these files. HACS still fetches icons
from the brands CDN rather than through Home Assistant's Brands Proxy API, so
it never looks at the `brand/` folder — and the CDN has no entry for any custom
integration added since Home Assistant stopped accepting them in 2026.3. Every
recently-published custom integration is affected, not just this one.

Tracked upstream:

- [hacs/integration#5171](https://github.com/hacs/integration/issues/5171) — dashboard doesn't show local brand icons
- [hacs/integration#5179](https://github.com/hacs/integration/issues/5179) — HACS should use the Brands Proxy API
- [hacs/integration#5223](https://github.com/hacs/integration/issues/5223) — downloads panel shows 'icon not available'
- [hacs/integration#5402](https://github.com/hacs/integration/issues/5402) — local branding works, HACS still shows the placeholder

**Do not cut a release to "fix" this.** Nothing shipped from here changes it.

### Trademark note

The mark is Fleet-adjacent on purpose: it borrows two brand colours and a grid
arrangement so it reads as related to Fleet at a glance. It is not a copy of
Fleet's logo, and it is not the Fleet wordmark's typeface.

Fleet's name and branding are still their trademarks, and this project is not
affiliated with or endorsed by them.

**This is generated from a script, not designed.** If you want a considered
identity, replace these files — nothing else depends on them.
