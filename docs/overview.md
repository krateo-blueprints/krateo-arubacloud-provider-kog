---
type: Architecture
title: krateo-arubacloud-provider-kog — overview
description: How the blueprint works — RestDefinition to generated CRD + controller, the nested-metadata identifier pattern that eliminates proxies, *ApiRef delegation for multi-call lifecycles, and the whole-environment Composition.
resource: oci://ghcr.io/krateo-blueprints/charts/aruba-cloudserver-environment
tags: [architecture, oasgen-provider, restdefinition, metadata-pattern, apiref]
timestamp: 2026-09-05T00:00:00Z
---

# Overview

This repository turns the official Aruba Cloud REST API into Kubernetes Custom
Resources without writing any Go proxy. It ships declarative
[`oasgen-provider`](https://github.com/krateo-blueprints/krateo-oasgen-provider)
`RestDefinition`s (one per resource) plus a whole-environment Krateo Composition.
It is a **blueprint**: everything here is data (RestDefinitions, ConfigMaps,
RESTActions, a Helm chart) consumed by the Krateo platform — no bespoke image.

## The generation pipeline

The API surface flows through four generated layers, all reproducible from the
scripts under `scripts/`:

```
Aruba OpenAPI spec (vendored unmodified) --> openapi/<provider>.json
openapi/<provider>.json --gen_configmaps.py--> configmaps/<provider>-openapi.yaml   (a ConfigMap)
                    --generate_restdefinitions.py--> restdefinitions/<provider>/<kind>.yaml
                    --gen_samples_and_coverage.py--> samples/ + docs/coverage.md
```

Each `RestDefinition` (`ogen.krateo.io/v1alpha1`) names a patched spec by
`oasPath: configmap://<namespace>/<configmap-name>/<file.json>` and describes the
resource: its `kind`, its `identifiers`, and one `verbsDescription` entry per
CRUD verb bound to an OpenAPI path + method. When applied, **oasgen-provider
generates a CRD** for that kind (group `arubacloud.ogen.krateo.io`) and deploys a
**rest-dynamic-controller (RDC)** instance that reconciles instances of it against
the live Aruba API.

## How proxies were eliminated

Almost every Aruba resource nests its name/id in a `metadata` object
(`metadata.name` on create, `metadata.id` on read). That single fact is why the
predecessor blueprint needed a Go `subnet-plugin` to flatten the shape. Each
`RestDefinition` here replaces that plugin declaratively, using features that
exist only in **oasgen-provider** of oasgen-provider and RDC:

- `identifiers: [metadata.name]` — a **nested identifier**, no flattening proxy.
- `additionalStatusFields: [metadata.id]` — surfaces the server-assigned id into
  `status.metadata.id`.
- `requestFieldMapping` — feeds `status.metadata.id` back into the `{id}` path
  parameter of `get`/`update`/`delete`.
- `excludedSpecFields` — drops fields the API returns but must not appear in spec.

The canonical example is `restdefinitions/network/subnet.yaml`, replicated across
all resources. See [Adversarial review](adversarial-review.md) for the
verification that each feature actually behaves as claimed against oasgen-provider's
executor source, and [oasgen-provider evolution](oasgen-provider-evolution.md) for
the residual gaps.

## Lifecycle beyond the five CRUD verbs

A few resources have a lifecycle that is not a single create/update/delete call.
The prime case is `compute/CloudServer`, whose provisioning, power state,
associations and volume attachments are a multi-call, action-driven sequence.

Instead of reviving a proxy, `CloudServer`'s `RestDefinition` **delegates**
create/update/delete to Snowplow `RESTAction`s via oasgen-provider's `createApiRef` /
`updateApiRef` / `deleteApiRef`, while `get`/`findby` stay native. The RESTActions
live under `restactions/compute/` and are written to be **idempotent** (a
`dependsOn.iterator` guard runs a step zero times when the resource already
exists), because RDC re-invokes them every reconcile until the native observe
reports convergence. See [Lifecycle beyond CRUD](lifecycle-beyond-crud.md).

## Async readiness

Some resources (e.g. `baremetal/Hpc`) provision asynchronously. Their
`RestDefinition` uses oasgen-provider's `async` block so the controller itself polls a
status endpoint and only reports `Ready` when provisioning completes, rather than
flapping. See [Async readiness](async-readiness.md).

## Whole-environment provisioning: the Composition

The `compositions/` directory pairs a Krateo `CompositionDefinition`
(`core.krateo.io/v1alpha1`) with a Helm chart, `aruba-cloudserver-environment`.
Registering the CompositionDefinition generates a CRD; applying one instance of it
fans a single high-level input set (project, location, CIDRs, flavor, token
reference) out into a coordinated set of CRs — `Vpc` + `Subnet` + `SecurityGroup`
+ `CloudServer` and their per-kind `Configuration`s. This is the
"composition concept" answer to lifecycle-beyond-CRUD: many single-purpose CRs
orchestrated together rather than one controller doing many things.

## Grounding and caveats

- **Not cluster-tested.** `api.arubacloud.com` is not reachable from the build
  environment, so manifests are validated for shape/consistency against the OAS
  and oasgen-provider's canonical Subnet example (`scripts/validate.py`), not against a
  live API. Treat them as a reviewed starting point.
- The OAS patches (strip `nullable`/`readOnly`, coerce `additionalProperties`)
  change the contract to fit the tool; each is tracked in the evolution report as
  something oasgen-provider should ideally handle natively.
