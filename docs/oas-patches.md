# OAS policy — the specs are **not** modified

**This repository performs no OpenAPI rewriting.** The documents under
`openapi/` are Aruba's published specifications, byte-for-byte:

```
https://api.arubacloud.com/openapi/<provider>.json
```

KOG adapts to the published contract, not the other way round. A vendor's API
description is the authority; a generator that requires it to be edited before it
can be consumed has moved its own limitations onto the user.

> This page previously documented a `scripts/patch_oas.py` that made ~4,150
> edits. That script is **deleted**. The history of why each transformation
> existed and how each was retired is kept below, because it is the clearest
> measure of how far the tooling has come.

## Enforcement

`openapi/CHECKSUMS.txt` records the sha256 of every vendored document, and
`scripts/validate.py` recomputes them on every run. Any drift — including a
well-meaning "fix" — fails validation:

```
openapi/metering.json: MODIFIED (sha256 differs from CHECKSUMS.txt)
```

To refresh from upstream, replace the files and regenerate the manifest:

```sh
curl -sO https://api.arubacloud.com/openapi/network-provider.json   # etc.
(cd openapi && shasum -a 256 *.json | sort -k2 > CHECKSUMS.txt)
```

## What it cost, and how that resolved

Consuming the specs unmodified briefly surfaced one real gap: oasgen supported
only `type: http` (`bearer`/`basic`) schemes and **silently skipped** anything
else, so the 7 providers declaring `apiKey`-in-header generated a
`<Kind>Configuration` with no `authentication` block — 24 of 34 resources could
not authenticate.

That is now **shipped** (oasgen 0.19.0 + RDC 0.19.0,
[#49](https://github.com/braghettos/krateo-oasgen-provider/issues/49)): both
scheme shapes generate a usable auth block, and an unsupported scheme is no
longer skipped in silence.

| Aruba spec | Declared scheme | Generated auth block |
|---|---|---|
| compute, database, schedule | `type: http, scheme: bearer` | `authentication.bearer` |
| network, container, security, storage, baremetal, project, metering | `type: apiKey, in: header, name: Authorization` | `authentication.apiKey` (`header` + `valuePrefix: 'Bearer '`) |

**Nothing in Aruba's documents had to change** — which was the point. The episode
is the policy's best evidence: refusing to patch turned a hidden, permanent edit
to a vendor contract into a visible, tracked, and ultimately *fixed* limitation in
the tool. Aruba is also inconsistent with itself here (3 of 11 specs already use
`http`/`bearer`), so it remains legitimate feedback to them — but a spec being
imperfect is not licence for KOG to rewrite it.

See [authentication](authentication.md) for the `valuePrefix` detail that Aruba
specifically needs.

## History — every transformation that used to exist, and why it went

| Former transformation | Count | Why it is gone |
|---|------:|---|
| coerce `additionalProperties: {schema}` → `true` | 42 | oasgen 0.18.0 emits a **typed map** ([#45](https://github.com/braghettos/krateo-oasgen-provider/issues/45)) |
| rename baremetal monitor param `{id}` → `{operationId}` | 1 | `async.poll.handleParam: id` binds the handle to Aruba's own name ([#46](https://github.com/braghettos/krateo-oasgen-provider/issues/46)) |
| strip `nullable: true` | ~4119 | **No-op.** Zero references in oasgen's `oas2jsonschema` or anywhere in RDC; removing the strip left all 34 RestDefinitions byte-identical |
| strip `readOnly` / `writeOnly` | 25 | **No-op**, same evidence |
| merge compute `v1.1` create into the base document | 1 | Dead weight: CloudServer delegates create via `createApiRef`, so the v1.1 `POST` was never referenced by any verb |
| security scheme `apiKey` → `http`/`bearer` | 8 | **The one that was real** — filed as [#49](https://github.com/braghettos/krateo-oasgen-provider/issues/49) instead of patched, and **shipped** in oasgen/RDC 0.19.0 |

Three of those six were fixed upstream, two were never load-bearing, and one was
unnecessary. **None of them is a patch any more.**

## Constructs consumed as-is (documented, not "fixed")

- `nullable`, `readOnly`/`writeOnly` — ignored by the toolchain; harmless (§A1, §A3).
- `additionalProperties: {schema}` — native typed maps since oasgen 0.18.0 (§A2).
- `format`, `number`/`double`, constraint keywords — see §A4–A6.
- `allOf` — merged natively by oasgen.

## Reproducing / auditing

Every generated artifact derives from these untouched documents:

```sh
python3 scripts/generate_restdefinitions.py   # -> restdefinitions/
python3 scripts/gen_configmaps.py             # -> configmaps/ (embeds the specs verbatim)
python3 scripts/gen_samples_and_coverage.py   # -> samples/ + docs/coverage.md
python3 scripts/gen_provider_docs.py          # -> docs/providers/
python3 scripts/validate.py                   # incl. the sha256 immutability check
```
