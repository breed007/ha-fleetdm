# Publishing to HACS

Two separate things, in this order:

1. **Ship a release** so people can install via a custom repository. Works
   immediately, no approval needed.
2. **Get into the HACS default list** so people can find it without adding a
   custom repository. Needs a PR to `hacs/default`.

---

## Before the first release

- [ ] Confirm the URLs in `manifest.json` point at the real repository:
      `grep -o 'https://github.com/[^"]*' custom_components/fleetdm/manifest.json`
- [ ] Push to `https://github.com/breed007/ha-fleetdm`
- [ ] Confirm CI is green: `Tests` (3 Home Assistant versions), `ruff`,
      `hassfest`, and `HACS`
- [x] Set the repository **description** — required for the default list.
      Already set.
- [x] Set repository **topics** — required for the default list. Already set:
      `home-assistant`, `homeassistant`, `hacs`, `custom-component`, `fleetdm`,
      `osquery`, `security`, `compliance`, `home-automation`, `device-management`
- [ ] Confirm **Issues are enabled** — required for the default list
- [ ] Enable **private vulnerability reporting** (Settings → Security), which
      [SECURITY.md](../SECURITY.md) points people to

## Cutting a release

The release workflow **fails the build if the git tag does not match `version`
in `manifest.json`** — that mismatch is the most common way to ship a wrong
version number, since HACS installs the manifest version rather than the tag.

HACS installs from the repository source, so no build artefact is needed.
`zip_release` was deliberately not used: it requires a published release before
the HACS validation action can resolve the repository's contents, which makes
CI fail on every commit until the first release exists.

1. [ ] Bump `version` in [manifest.json](../custom_components/fleetdm/manifest.json)
2. [ ] Move the `Unreleased` section in [CHANGELOG.md](../CHANGELOG.md) under
       the new version, and update the link refs at the bottom
3. [ ] Commit, then tag: `git tag v0.1.0 && git push --tags`
4. [ ] Create a **GitHub Release** from the tag — a bare tag is not enough for
       HACS, it needs an actual release

## Verifying installation as a user would

Before submitting to the default list, install it the way a stranger will:

1. [ ] HACS → three-dot menu → Custom repositories → add
       `https://github.com/breed007/ha-fleetdm`, category **Integration**
2. [ ] Install, restart, and add the integration
3. [ ] Confirm setup completes in under two minutes with just a URL and an
       API-only Observer token
4. [ ] Confirm entities appear and the hub device links back to your Fleet UI

## Submitting to the HACS default list

Requirements, from [hacs.xyz](https://hacs.xyz/docs/publish/include/):

- [ ] Repository is public and on GitHub
- [ ] Repository has a description
- [ ] Repository has topics
- [ ] Issues are enabled
- [ ] At least one **release** exists (not just a tag)
- [ ] HACS Action passes with no errors and no ignores
- [ ] hassfest Action passes
- [ ] `hacs.json` present with at least a `name`
- [ ] Valid `manifest.json`
- [ ] Exactly one integration under `custom_components/`
- [x] An icon exists — shipped in `custom_components/fleetdm/brand/`

Then:

1. Fork [hacs/default](https://github.com/hacs/default)
2. Add `breed007/ha-fleetdm` to the `integration` file, **in alphabetical
   order** — not appended at the end
3. Branch off `master`; do not push to `master` directly
4. Open the PR from your personal account (org accounts block maintainer edits)
   and fill in the template accurately

## Brand icon — nothing to submit

Home Assistant **no longer accepts custom integration icons** into the
home-assistant/brands repository. Since 2026.3, integrations ship their own from
`custom_components/fleetdm/brand/`, which this one does.

We confirmed this the hard way: [brands#10930](https://github.com/home-assistant/brands/pull/10930)
passed every CI check and was still closed as out of policy. Don't re-submit.

Users on Home Assistant 2025.2–2026.2 see a generic placeholder icon, since the
`brand/` folder only works on 2026.3+. Cosmetic only. See
[BRAND_ASSETS.md](BRAND_ASSETS.md).

## After it lands

- [ ] Consider announcing in the [Fleet community Slack](https://fleetdm.com/support)
      — per the project's own notes, feedback from operators running 200+ host
      fleets is the open question for per-host entity gating in Phase 2
- [ ] Consider a [Home Assistant Community](https://community.home-assistant.io/)
      forum post
