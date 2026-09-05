---
type: Usage
title: krateo-arubacloud-provider-kog — usage
description: Install the krateo oasgen-provider fork, apply the OAS ConfigMaps and RestDefinitions, manage your first resource end to end, and provision a whole environment through the Composition.
resource: oci://ghcr.io/krateo-blueprints/charts/aruba-cloudserver-environment
tags: [usage, install, restdefinition, composition, kubectl]
timestamp: 2026-09-05T00:00:00Z
---

# Usage

## Prerequisites

A Kubernetes cluster with **oasgen-provider** of oasgen-provider and its
rest-dynamic-controller (RDC). A stock (non-krateo) oasgen-provider does **not**
have the features these RestDefinitions rely on (nested identifiers,
`fieldMapping`, `async`, `*ApiRef`).

| Component | Minimum | Why |
|-----------|---------|-----|
| `ghcr.io/krateo-blueprints/krateo-oasgen-provider` | **0.18.0** | typed-map `additionalProperties`; `async.poll.handleParam` + poll-path validation |
| `ghcr.io/krateo-blueprints/krateo-rest-dynamic-controller` | **0.18.0** | honours `handleParam`; forwards the CR spec on every `*ApiRef` direction |
| `krateo-oasgen-provider-chart` | **0.9.19** | first release pairing oasgen 0.18.0 **with** RDC 0.18.0 |

```sh
helm install oasgen-provider oci://ghcr.io/krateo-blueprints/charts/krateo-oasgen-provider \
  --version 0.9.19 --namespace krateo-system --create-namespace
```

> Do not install chart ≤ 0.9.18 with these manifests. It pins `rdc.image.tag` by
> hand and does not auto-track RDC releases, so 0.9.18 shipped oasgen 0.18.0
> against RDC 0.16.1. On that pairing these manifests are accepted and then fail
> **silently** (`handleParam` ignored; delegated deletes receive no spec). If you
> are pinned to an older chart, override with `--set rdc.image.tag=0.18.0` and
> verify the running image — see [Troubleshooting](troubleshooting.md).

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
is under [`examples/`](../examples/cloudserver-environment/README.md).

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
