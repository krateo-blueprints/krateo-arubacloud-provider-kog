# Aruba Cloud Provider KOG — proxy-free RestDefinitions

**KOG** = *Krateo Operator Generator.*

## What is this

A proxy-free Krateo blueprint: [`oasgen-provider`](https://github.com/krateo-blueprints/krateo-oasgen-provider)
`RestDefinition`s that manage **all** manageable Aruba Cloud resources as
Kubernetes Custom Resources — generated directly from the official
[Aruba Cloud OpenAPI specifications](https://api.arubacloud.com/docs/intro) with
**no wrapper/proxy web service** ("plugin").

The predecessor blueprint managed a single resource (`Subnet`) and needed a Go
proxy (`subnet-plugin`) just to reshape Aruba's `metadata` object. The **krateo
forks** of oasgen-provider and rest-dynamic-controller remove that need through
nested identifiers, `requestFieldMapping`, `fieldMapping`, `secretRef`, `async`
and Snowplow `*ApiRef` delegation. This repo uses those features to cover **34
resources across 10 providers with zero plugins**, and every load-bearing claim
has been [adversarially verified](docs/adversarial-review.md) against the fork's
executor source.

10 providers, 34 resources — full CRUD where the API allows it:

- **network**: Vpc, Subnet, SecurityGroup, SecurityRule, ElasticIp, VpcPeering,
  VpcPeeringRoute, VpnTunnel, VpnRoute, LoadBalancer *(read-only)*
- **compute**: CloudServer *(create/get/findby/delete — lifecycle actions delegated)*, KeyPair
- **container**: Kaas, KaasBackup, Registry
- **database**: Dbaas, Database, DatabaseUser, Grant, DatabaseBackup
- **storage**: BlockStorage, Snapshot, Backup, Restore
- **security**: Kms, Key, Kmip
- **schedule**: BackupPolicy, BackupPolicyAssignment, Job
- **baremetal**: Hpc *(no delete verb in the API)*
- **project**: Project, Folder
- **metering**: AlertRule

## Install

Prerequisites: a Kubernetes cluster with the **krateo fork** of oasgen-provider
and its rest-dynamic-controller (a stock oasgen-provider lacks the features these
manifests rely on). Minimums: oasgen-provider **0.18.0**, RDC **0.18.0**,
`krateo-oasgen-provider-chart` **0.9.19** — see
[Usage](docs/usage.md#prerequisites) for why.

```sh
# 0. the krateo oasgen-provider fork (first release pairing oasgen 0.18.0 with RDC 0.18.0)
helm install oasgen-provider oci://ghcr.io/krateo-blueprints/charts/krateo-oasgen-provider \
  --version 0.9.19 --namespace krateo-system --create-namespace

# 1. auth token (short-lived Aruba JWT)
kubectl apply -f samples/arubacloud-token-secret.yaml

# 2. OAS ConfigMaps (the oasPath sources) — must be in the oasgen-provider namespace
kubectl apply -n krateo-system -f configmaps/

# 3. the RestDefinitions (all providers, or pick a subset)
kubectl apply -R -f restdefinitions/

# 4. wait for the generated controllers to become Ready
kubectl get restdefinitions.ogen.krateo.io -A | awk 'NR==1 || /arubacloud/'
```

> [!IMPORTANT]
> **Do not install chart ≤ 0.9.18 with these manifests.** 0.9.18 shipped oasgen
> 0.18.0 against **RDC 0.16.1**; on that pairing the manifests are accepted and
> then fail **silently** (`handleParam` ignored, delegated deletes receive no
> spec). If pinned to an older chart, override `--set rdc.image.tag=0.18.0` and
> verify the running image — see [Troubleshooting](docs/troubleshooting.md).

## Configure

Two objects per resource kind, mirroring the upstream blueprint:

1. a Kubernetes **Secret** holding the Aruba Bearer token
   (`samples/arubacloud-token-secret.yaml`);
2. a **`<Kind>Configuration`** referencing that Secret and carrying per-verb query
   config (e.g. `api-version`), referenced from each CR via `spec.configurationRef`.

Tokens are short-lived; rotation is the operator's responsibility. The full config
surface — the Secret, the `<Kind>Configuration`, the RestDefinition fields, and the
Composition chart values — is in [Configuration](docs/configuration.md).

## Examples

- [`examples/cloudserver-environment/`](examples/cloudserver-environment/README.md)
  — provision a complete environment (`Vpc` + `Subnet` + `SecurityGroup` +
  `CloudServer`) from one input set, via the `aruba-cloudserver-environment`
  Composition.
- `samples/<provider>/` — a `<Kind>Configuration` + `<Kind>` CR skeleton for
  every resource, plus the shared auth Secret.

## Docs

Full documentation lives in [`docs/`](docs/index.md):

- Core: [Overview](docs/overview.md) · [Usage](docs/usage.md) ·
  [Configuration](docs/configuration.md) · [API](docs/api.md) ·
  [Examples](docs/examples.md) · [Release](docs/release.md) · [Log](docs/log.md)
- References: [Getting started](docs/getting-started.md) ·
  [Architecture](docs/architecture.md) · [Authentication](docs/authentication.md) ·
  [Provider reference](docs/providers/README.md) · [Coverage matrix](docs/coverage.md) ·
  [OAS patches](docs/oas-patches.md) · [Terraform parity](docs/terraform-parity.md)
- Design & extension: [Adding a resource](docs/adding-a-resource.md) ·
  [Async readiness](docs/async-readiness.md) ·
  [Lifecycle beyond CRUD](docs/lifecycle-beyond-crud.md) ·
  [oasgen-provider evolution](docs/oasgen-provider-evolution.md) ·
  [Adversarial review](docs/adversarial-review.md) ·
  [Troubleshooting](docs/troubleshooting.md)

## Develop & release

Everything under `openapi/`, `configmaps/`, `restdefinitions/`, `samples/` and
`docs/coverage.md`/`docs/providers/` is generated. To refresh from updated Aruba
specs:

```sh
# refresh openapi/_source/*.json from https://api.arubacloud.com/openapi/, then:
python3 scripts/patch_oas.py                 # -> openapi/  (+ logs every OAS gap)
python3 scripts/generate_restdefinitions.py  # -> restdefinitions/
python3 scripts/gen_configmaps.py            # -> configmaps/
python3 scripts/gen_samples_and_coverage.py  # -> samples/ + docs/coverage.md
python3 scripts/gen_provider_docs.py         # -> docs/providers/
python3 scripts/validate.py                  # static + helm validation (must pass)
```

`patch_oas.py` prints a count of every transformation it applies; each count is a
concrete oasgen-provider gap, analysed in
[oasgen-provider evolution](docs/oasgen-provider-evolution.md). The
`aruba-cloudserver-environment` Composition chart is packaged and pushed to GHCR
as an OCI artifact — see [Release](docs/release.md).

## Caveats & assumptions

- **Not cluster-tested.** `api.arubacloud.com` is not reachable from the build
  environment, so these manifests are validated for shape/consistency against the
  OAS and the fork's canonical Subnet example, not against a live API. Treat them
  as a reviewed starting point.
- The metadata-wrapped recipe (`identifiers: [metadata.name]` +
  `additionalStatusFields: [metadata.id]` + `requestFieldMapping id →
  status.metadata.id`) is the exact pattern the fork ships as its Subnet example,
  replicated across resources.
- The OAS patches (strip `nullable`/`readOnly`, coerce `additionalProperties`)
  change the contract to fit the tool; each is tracked in the evolution report as
  something oasgen-provider should ideally handle natively.
