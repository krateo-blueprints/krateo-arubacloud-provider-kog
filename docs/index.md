---
type: Component
title: krateo-arubacloud-provider-kog — index
description: The map of the Aruba Cloud Provider KOG doc bundle — proxy-free oasgen-provider RestDefinitions for 34 Aruba Cloud resources across 10 providers, plus a whole-environment Composition.
resource: oci://ghcr.io/krateo-blueprints/charts/aruba-cloudserver-environment
tags: [krateo, arubacloud, oasgen-provider, restdefinition, kog]
timestamp: 2026-08-11T00:00:00Z
---

# Aruba Cloud Provider KOG

**KOG** = *Krateo Operator Generator.* This repository is a proxy-free Krateo
blueprint: a set of [`oasgen-provider`](https://github.com/krateo-blueprints/krateo-oasgen-provider)
`RestDefinition`s that manage **all** manageable Aruba Cloud resources (34
resources across 10 providers) as Kubernetes Custom Resources, generated directly
from the official Aruba Cloud OpenAPI specifications with **no wrapper/proxy web
service**. A single Krateo `CompositionDefinition` + Helm chart
(`aruba-cloudserver-environment`) provisions a whole environment from one input set.

## OKF core docs

| Doc | What it covers |
|-----|----------------|
| [Overview](overview.md) | The architecture — RestDefinition → CRD + controller, the metadata pattern, proxy elimination, `*ApiRef` delegation, the Composition |
| [Usage](usage.md) | Install the fork, apply the ConfigMaps + RestDefinitions, manage your first resource, provision an environment |
| [Configuration](configuration.md) | The token Secret, `<Kind>Configuration` resources, per-verb query config, the Composition chart values |
| [API](api.md) | The CRDs this repo relies on and produces — `RestDefinition`, `CompositionDefinition`, the generated per-resource CRDs |
| [Examples](examples.md) | Index of the runnable examples under `examples/` |
| [Release](release.md) | How the Composition chart is packaged and published as an OCI artifact |
| [Log](log.md) | Curated history of notable changes and decisions |

## Deep-dive references

| Doc | What it covers |
|-----|----------------|
| [Architecture](architecture.md) | RestDefinition → CRD + controller, the metadata pattern, reconcile flow |
| [Authentication](authentication.md) | The token Secret, `<Kind>Configuration`, per-verb query config, rotation |
| [Getting started](getting-started.md) | Install and manage your first resource end to end |
| [Provider reference](providers/README.md) | Per-provider pages: every resource, its verbs, endpoints, config and samples |
| [Coverage matrix](coverage.md) | The full resource/verb table + what is intentionally not generated |
| [OAS patches](oas-patches.md) | Every transformation applied to the raw specs, and why |
| [Terraform parity](terraform-parity.md) | Resource cross-map vs the official Aruba Cloud Terraform provider |
| [Adding a resource](adding-a-resource.md) | How the generator maps an API shape to a RestDefinition |
| [Async readiness](async-readiness.md) | Controller-native provisioning waits via the `async` block |
| [Lifecycle beyond CRUD](lifecycle-beyond-crud.md) | Proxy-free solution for multi-call/action lifecycles |
| [oasgen-provider evolution](oasgen-provider-evolution.md) | Every issue that requires an oasgen-provider evolution |
| [Adversarial review](adversarial-review.md) | Every load-bearing claim attacked against the fork's executor source |
| [Troubleshooting](troubleshooting.md) | Common failures and how to diagnose them |

## Repository layout

```
openapi/_source/   raw Aruba specs (vendored verbatim)
openapi/           patched specs (scripts/patch_oas.py)
configmaps/        per-provider ConfigMaps embedding the patched specs (oasPath source)
restdefinitions/   one RestDefinition per resource (no proxies)
restactions/       Snowplow RESTActions for CloudServer's delegated lifecycle
compositions/      Krateo CompositionDefinition + Helm chart (whole-environment provisioning)
samples/           <Kind>Configuration + CR skeleton per resource + auth Secret
examples/          runnable, self-contained walkthroughs
scripts/           reproducible generators + validator
docs/              this documentation set
```
