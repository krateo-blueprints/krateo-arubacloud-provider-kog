---
type: Runbook
title: krateo-arubacloud-provider-kog — release
description: How the aruba-cloudserver-environment Composition chart is packaged and published as an OCI artifact, how the CompositionDefinition version tracks it, and how the generated manifests are refreshed from updated Aruba specs.
resource: oci://ghcr.io/krateo-blueprints/charts/aruba-cloudserver-environment
tags: [release, oci, ghcr, chart, regeneration]
timestamp: 2026-08-11T00:00:00Z
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

Manual publish (the canonical Krateo pattern is a `release-oci` CI workflow; do
that where present):

```sh
helm package compositions/aruba-cloudserver-environment
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
# 1. refresh openapi/*.json from https://api.arubacloud.com/openapi/ (vendored
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

These manifests require the **krateo fork** at the minimums in
[Usage](usage.md#prerequisites) (oasgen-provider ≥ 0.18.0, RDC ≥ 0.18.0, chart
≥ 0.9.19). A release must state the fork versions it was validated against and
must not be paired with chart ≤ 0.9.18 (silent failures — see
[Troubleshooting](troubleshooting.md)).
