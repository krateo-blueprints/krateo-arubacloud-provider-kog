---
type: Runbook
title: krateo-arubacloud-provider-kog — release
description: How the aruba-cloudserver-environment Composition chart is packaged and published as an OCI artifact, how the CompositionDefinition version tracks it, and how the generated manifests are refreshed from updated Aruba specs.
resource: oci://ghcr.io/krateo-blueprints/charts/aruba-cloudserver-environment
tags: [release, oci, ghcr, chart, regeneration]
timestamp: 2026-09-05T00:00:00Z
---

# Release

This repository is a blueprint, not a compiled component. Two things ship: the
**Composition chart** (as an OCI artifact) and the **generated manifests** (via
the repository itself). There is no application image.

## Publishing the Composition chart

The `aruba-cloudserver-environment` chart under `compositions/` is packaged and
pushed to GHCR as an OCI artifact. The `CompositionDefinition` points at it:

```yaml
spec:
  chart:
    url: oci://ghcr.io/krateo-blueprints/charts/aruba-cloudserver-environment
    version: "0.1.0"
```

## Versioning

**The repository is versioned by git tag, and the tag is the only source of truth.**

`.github/workflows/release-tag.yaml` fires on any tag matching `[0-9]+.[0-9]+.[0-9]+`.
It discovers every `Chart.yaml`, substitutes the `CHART_VERSION` placeholder with the
tag, and publishes each chart as an OCI artifact:

```
oci://ghcr.io/krateo-blueprints/charts/<chart-name>:<tag>
```

No chart carries a hand-written version — `CHART_VERSION` is a placeholder precisely so
a version can never be committed, drift from the tag, or be bumped in one file and
forgotten in another. `scripts/validate.py` lints a copy with the placeholder
substituted, so `helm lint` still passes on an unstamped tree.

### What the number means here

This blueprint's version tracks **the evidence, not the code**. The manifests are
generated and change only when Aruba's specs do; what actually moves is how much of the
surface has been proven against the live API. So:

| Bump | When |
|------|------|
| **patch** | regenerated manifests, doc corrections, more resources proven at an existing tier |
| **minor** | a resource changes tier, a new upstream provider version is adopted, or a chain/runner capability lands |
| **major** | reserved for a breaking change to a generated CRD — which happens when Aruba changes `info.version`, since the CRD version is derived from it (`1.0.0` → `v1-0-0`) |

That last row is the one to watch: an `info.version` bump upstream renames the served
CRD version and orphans existing CRs. `.github/workflows/oas-drift.yaml` exists to catch
it before a user does.

### Pre-1.0

The provider is **pre-1.0 and will stay there until the GA core is closed**. At the time
of writing 15 of 34 resources are GA, 7 beta, 7 experimental and 5 blocked — the tier
table in [coverage](coverage.md) is generated from recorded evidence, and
[ga-readiness](ga-readiness.md) lists what remains. Publishing 1.0.0 while five
resources are known non-functional would misrepresent the thing.

Manual publish, for a one-off outside CI:

```sh
helm package compositions/aruba-cloudserver-environment --version 0.1.0
helm push aruba-cloudserver-environment-0.1.0.tgz \
  oci://ghcr.io/krateo-blueprints/charts
```

The chart version in `compositions/aruba-cloudserver-environment/Chart.yaml`, the
`spec.chart.version` in `compositions/compositiondefinition.yaml`, and the pin in
the example must move together. A chart-version bump changes the Composition's
derived apiVersion/GVK, so treat it as a coordinated change, not an in-place edit.

## Refreshing the generated manifests

Everything under `openapi/`, `configmaps/`, `restdefinitions/`, `samples/` and
`docs/coverage.md`/`docs/providers/` is generated. To refresh from updated Aruba
specs:

```sh
# 1. refresh openapi/*.json from https://arubacloud.github.io/api/openapi/ (vendored
#    UNMODIFIED -- update openapi/CHECKSUMS.txt alongside; validate.py enforces it), then:
python3 scripts/generate_restdefinitions.py  # -> restdefinitions/
python3 scripts/gen_configmaps.py            # -> configmaps/
python3 scripts/gen_samples_and_coverage.py  # -> samples/ + docs/coverage.md
python3 scripts/gen_provider_docs.py         # -> docs/providers/
python3 scripts/validate.py                  # static + helm validation (must pass)
```

The specs are consumed as published -- no OAS rewriting; RestDefinitions adapt to the
contract instead. Any generation gap is a
concrete oasgen-provider gap, analysed in
[oasgen-provider evolution](oasgen-provider-evolution.md). `validate.py` must exit
zero before a release: it checks every RestDefinition verb path+method against its
patched OAS, every `requestFieldMapping.inPath` against the path parameters, every
`oasPath` ConfigMap reference, and `helm lint` + `helm template` on the chart.

## Compatibility

These manifests require **oasgen-provider ≥ 0.22.2** (see
[Usage](usage.md#prerequisites)). It is no longer a fork: rest-dynamic-controller and
both charts live in the oasgen-provider monorepo, and the chart derives the RDC image
tag from its own `appVersion`, so the two cannot drift apart.

0.22.2 is the floor because it carries three fixes this repository found and verified
live — read-only identifiers (#75), empty-array drift (#76) and delete verification
(#77, plus the #98 regression fix that made it usable). Below that, resources are
silently unenforceable or undeletable.

**A release must state the provider version it was validated against.** The CI gate
pins it in one place (`OASGEN_VERSION` in `.github/workflows/validate.yaml`) and the
smoke job re-proves all 34 RestDefinitions against it, so the claim is executed rather
than asserted.
