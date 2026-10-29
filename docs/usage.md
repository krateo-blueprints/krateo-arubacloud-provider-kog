---
type: Usage
title: krateo-arubacloud-provider-kog — usage
description: Install the krateo oasgen-provider fork, apply the OAS ConfigMaps and RestDefinitions, manage your first resource end to end, and provision a whole environment through the Composition.
resource: oci://ghcr.io/krateo-blueprints/charts/aruba-network-environment
tags: [usage, install, restdefinition, composition, kubectl]
timestamp: 2026-09-05T00:00:00Z
---

# Usage

## Prerequisites

A Kubernetes cluster with the **krateo-platformops** oasgen-provider and its
rest-dynamic-controller (RDC). A stock upstream oasgen-provider does **not** have
the features these RestDefinitions rely on (nested identifiers, `fieldMapping`,
`async`, `*ApiRef`).

| Component | Minimum | Why |
|-----------|---------|-----|
| `ghcr.io/krateo-platformops/oasgen-provider` | **0.36.0** | a hard floor: below 0.35.0 libopenapi cannot parse these OAS documents at all, and `metadata` properties are dropped from generated CRDs until [#236](https://github.com/krateo-platformops/oasgen-provider/issues/236) |
| `ghcr.io/krateo-platformops/rest-dynamic-controller` | **0.36.0** | derived from the chart's `appVersion`, so not separately selectable |
| chart `oci://ghcr.io/krateo-platformops/charts/oasgen-provider` | **0.36.0** | released in lockstep with the provider |
| chart `oci://ghcr.io/krateo-platformops/charts/oasgen-provider-crds` | **0.36.0** | a **separate** install; without it there is no `RestDefinition` CRD |

```sh
helm install oasgen-provider-crds oci://ghcr.io/krateo-platformops/charts/oasgen-provider-crds \
  --version 0.36.0 --namespace krateo-system --create-namespace
helm install oasgen-provider oci://ghcr.io/krateo-platformops/charts/oasgen-provider \
  --version 0.36.0 --namespace krateo-system
```

> **Install from `krateo-platformops`, not from anywhere else.** oasgen-provider is
> now a monorepo in that org; the old `krateo-blueprints/krateo-oasgen-provider`
> images and the `krateo-oasgen-provider-chart` this page used to name are a
> pre-monorepo lineage, `charts.krateo.io` serves a build far below the floor
> ([oasgen#278](https://github.com/krateo-platformops/oasgen-provider/issues/278)),
> and the unhyphenated `krateoplatformops` org still resolves but is abandoned with
> a disjoint tag range. The chart derives `rdc.image.tag` from its own `appVersion`,
> so on 0.36.0 the two images can no longer drift apart — a chart version *is* the
> pair it ships. On the pre-monorepo charts they could, and chart 0.9.18 shipped
> oasgen 0.18.0 against RDC 0.16.1, on which these manifests are accepted and then
> fail **silently** (`handleParam` ignored; delegated deletes receive no spec).

You also need an Aruba Cloud Bearer token (short-lived JWT) — see
[Configuration](configuration.md) and the Aruba
[authentication docs](https://api.arubacloud.com/docs/authentication/).

## Install the provider

```sh
# 1. Auth token (short-lived Aruba JWT)
kubectl apply -f samples/arubacloud-token-secret.yaml

# 2. OAS ConfigMaps (the oasPath sources) — must live in the oasgen-provider namespace
kubectl apply -n krateo-system -f configmaps/

# 3. The RestDefinitions (all providers, or pick a subset)
kubectl apply -R -f restdefinitions/

# 4. Wait for the generated controllers to become Ready
kubectl get restdefinitions.ogen.krateo.io -A | awk 'NR==1 || /arubacloud/'
```

Each `RestDefinition` triggers oasgen-provider to generate a CRD (group
`arubacloud.ogen.krateo.io`) and deploy a rest-dynamic-controller for it.

## Manage your first resource

Every resource is managed by two objects: a `<Kind>Configuration` (auth + per-verb
query config) and the `<Kind>` CR itself, which references it via
`spec.configurationRef`.

```sh
# a) the Configuration (references the token Secret, carries api-version etc.)
kubectl apply -f samples/network/subnet-configuration.yaml

# b) the resource CR
kubectl apply -f samples/network/subnet.yaml

# c) watch it reconcile
kubectl get subnets.arubacloud.ogen.krateo.io -A -w
```

The controller creates the resource on Aruba, writes the server-assigned id into
`status.metadata.id`, and reports `Ready`.

## Provision a whole environment (Composition)

Register the `CompositionDefinition`, then apply one instance to fan out into a
`Vpc` + `Subnet` + `SecurityGroup` + `CloudServer` and their Configurations:

```sh
# register the Composition (generates its CRD)
kubectl apply -f compositions/compositiondefinition.yaml
```

The chart values that drive the environment are documented in
[Configuration](configuration.md#composition-chart-values); a runnable walkthrough
is under [`examples/`](../examples/network-environment/README.md).

## Regenerating everything

Everything under `openapi/`, `configmaps/`, `restdefinitions/`, `samples/` and
`docs/coverage.md` is generated:

```sh
python3 scripts/generate_restdefinitions.py  # restdefinitions/
python3 scripts/gen_configmaps.py            # configmaps/
python3 scripts/gen_samples_and_coverage.py  # samples/ + docs/coverage.md
python3 scripts/gen_provider_docs.py         # docs/providers/
python3 scripts/validate.py                  # static + helm validation
```
