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
| [OAS patches](oas-patches.md) | Every transformation applied to the raw specs, and why |

## Design & extension

| Doc | What it covers |
|-----|----------------|
| [Adding a resource](adding-a-resource.md) | How the generator maps an API shape to a RestDefinition, and how to add/override one |
| [Lifecycle beyond CRUD](lifecycle-beyond-crud.md) | Proxy-free solution for multi-call/action lifecycles: RESTAction delegation + Composition |
| [oasgen-provider evolution](oasgen-provider-evolution.md) | Every issue that requires an oasgen-provider evolution (the analysis) |
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
scripts/           reproducible generators + validator
docs/              this documentation set
```

## Regenerating everything

```sh
python3 scripts/patch_oas.py                 # openapi/  (+ logs every OAS gap)
python3 scripts/generate_restdefinitions.py  # restdefinitions/
python3 scripts/gen_configmaps.py            # configmaps/
python3 scripts/gen_samples_and_coverage.py  # samples/ + docs/coverage.md
python3 scripts/gen_provider_docs.py         # docs/providers/
python3 scripts/validate.py                  # static + helm validation
```
