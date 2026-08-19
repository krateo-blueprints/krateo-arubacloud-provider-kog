---
type: Component
title: krateo-arubacloud-provider-kog
description: Proxy-free Aruba Cloud RestDefinitions for Krateo — doc index.
tags: [aruba, kog]
timestamp: 2026-08-19T00:00:00Z
---

# Aruba Cloud Provider KOG — documentation

Proxy-free Krateo `oasgen-provider` RestDefinitions for **all** manageable Aruba
Cloud resources (34 resources across 10 providers), generated directly from the
official OpenAPI specifications.

## Start here

| Doc | What it covers |
|-----|----------------|
| [Getting started](getting-started.md) | Prerequisites, install, and managing your first resource end to end |
| [Architecture](architecture.md) | How RestDefinition → CRD + controller works; the metadata pattern; reconcile flow (diagrams) |
| [Authentication](authentication.md) | The token Secret, `<Kind>Configuration` resources, per-verb query config, rotation |

## Reference

| Doc | What it covers |
|-----|----------------|
| [Provider reference](providers/README.md) | Per-provider pages: every resource, its verbs, endpoints, config and samples |
| [Coverage matrix](coverage.md) | The full resource/verb table + what is intentionally not generated |
| [OAS policy](oas-patches.md) | Why the specs are never modified, how that is enforced, and what it costs |
| [Terraform parity](terraform-parity.md) | Resource cross-map vs the official Aruba Cloud Terraform provider |

## Design & extension

| Doc | What it covers |
|-----|----------------|
| [Adding a resource](adding-a-resource.md) | How the generator maps an API shape to a RestDefinition, and how to add/override one |
| [Async readiness](async-readiness.md) | Controller-native provisioning waits via the `async` block (wired on `Hpc`) |
| [Lifecycle beyond CRUD](lifecycle-beyond-crud.md) | Proxy-free solution for multi-call/action lifecycles: RESTAction delegation + Composition |
| [oasgen-provider evolution](oasgen-provider-evolution.md) | Every issue that requires an oasgen-provider evolution (the analysis) |
| [Live-cluster test](live-cluster-test.md) | What a real cluster proved — and the four defects static validation could not catch |
| [Adversarial review](adversarial-review.md) | Every load-bearing claim attacked against the fork's executor source — confirmed breaks (fixed) and acquittals |
| [Troubleshooting](troubleshooting.md) | Common failures and how to diagnose them |

## Repository layout

```
openapi/           Aruba specs, vendored byte-for-byte unmodified (+ CHECKSUMS.txt)
configmaps/        per-provider ConfigMaps embedding those specs (oasPath source)
restdefinitions/   one RestDefinition per resource (no proxies)
restactions/       Snowplow RESTActions for CloudServer's delegated lifecycle
compositions/      Krateo CompositionDefinition + Helm chart (whole-environment provisioning)
samples/           <Kind>Configuration + CR skeleton per resource + auth Secret
scripts/           reproducible generators + validator
docs/              this documentation set
```

## Regenerating everything

```sh
python3 scripts/generate_restdefinitions.py  # restdefinitions/
python3 scripts/gen_configmaps.py            # configmaps/
python3 scripts/gen_samples_and_coverage.py  # samples/ + docs/coverage.md
python3 scripts/gen_provider_docs.py         # docs/providers/
python3 scripts/validate.py                  # static + helm validation
```
