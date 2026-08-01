# Aruba Cloud Provider KOG — proxy-free RestDefinitions

**KOG** = *Krateo Operator Generator.*

This repository contains Krateo [`oasgen-provider`](https://github.com/braghettos/krateo-oasgen-provider)
`RestDefinition`s that manage **all** manageable Aruba Cloud resources as
Kubernetes Custom Resources — generated directly from the official
[Aruba Cloud OpenAPI specifications](https://api.arubacloud.com/docs/intro) with
**no wrapper/proxy web service** ("plugin").

The predecessor blueprint managed a single resource (`Subnet`) and needed a Go
proxy (`subnet-plugin`) just to reshape Aruba's `metadata` object. The
**braghettos forks** of oasgen-provider and rest-dynamic-controller (both at
**0.17.0**; every feature this repo uses is present since RDC 0.15.0 — see the
[verified version matrix](docs/adversarial-review.md#feature--minimum-version-matrix-verified-per-tag))
remove that need through nested identifiers, `requestFieldMapping`,
`fieldMapping`, `secretRef`, `async` and Snowplow `*ApiRef` delegation. This repo
uses those features to cover **34 resources across 10 providers with zero
plugins**, and every load-bearing claim has been
[adversarially verified against the executor source](docs/adversarial-review.md).

## Documentation

Full docs live in [`docs/`](docs/index.md):

- [Getting started](docs/getting-started.md) · [Architecture](docs/architecture.md) · [Authentication](docs/authentication.md)
- [Provider reference](docs/providers/README.md) (per-provider pages) · [Coverage matrix](docs/coverage.md) · [OAS patches](docs/oas-patches.md) · [Terraform parity](docs/terraform-parity.md)
- [Adding a resource](docs/adding-a-resource.md) · [Lifecycle beyond CRUD](docs/lifecycle-beyond-crud.md) · [oasgen-provider evolution](docs/oasgen-provider-evolution.md) · [Troubleshooting](docs/troubleshooting.md)

## What's here

| Path | Contents |
|------|----------|
| `openapi/_source/` | The official Aruba OpenAPI specs, vendored verbatim |
| `openapi/` | The same specs **patched** to be oasgen-consumable (`scripts/patch_oas.py`) |
| `configmaps/` | One ConfigMap per provider embedding its patched spec (the `oasPath` source) |
| `restdefinitions/<provider>/` | One `RestDefinition` per manageable resource — **no proxies** |
| `restactions/compute/` | Snowplow RESTActions that drive `CloudServer`'s multi-call lifecycle (delegated via `*ApiRef`) |
| `compositions/` | A Krateo `CompositionDefinition` + Helm chart provisioning a whole environment |
| `samples/<provider>/` | A `<Kind>Configuration` + a `<Kind>` CR skeleton per resource, plus the shared auth `Secret` |
| `scripts/` | Reproducible generators (`patch_oas.py`, `generate_restdefinitions.py`, `gen_configmaps.py`, `gen_samples_and_coverage.py`, `validate.py`) |
| `docs/oasgen-provider-evolution.md` | **Every issue that requires an oasgen-provider evolution** (the analytical deliverable) |
| `docs/lifecycle-beyond-crud.md` | **Proxy-free solution** for lifecycle beyond the 5 CRUD verbs (RESTAction delegation + Composition) |
| `docs/coverage.md` | Full resource/verb coverage matrix |

## Coverage at a glance

10 providers, 34 resources — full CRUD where the API allows it:

- **network**: Vpc, Subnet, SecurityGroup, SecurityRule, ElasticIp, VpcPeering,
  VpcPeeringRoute, VpnTunnel, VpnRoute, LoadBalancer *(read-only)*
- **compute**: CloudServer *(create/get/findby/delete — lifecycle actions need delegation)*, KeyPair
- **container**: Kaas, KaasBackup, Registry
- **database**: Dbaas, Database, DatabaseUser, Grant, DatabaseBackup
- **storage**: BlockStorage, Snapshot, Backup, Restore
- **security**: Kms, Key, Kmip
- **schedule**: BackupPolicy, BackupPolicyAssignment, Job
- **baremetal**: Hpc *(no delete verb in the API)*
- **project**: Project, Folder
- **metering**: AlertRule

See [`docs/coverage.md`](docs/coverage.md) for the full matrix and
[`docs/oasgen-provider-evolution.md`](docs/oasgen-provider-evolution.md) for what
the API surface asks of oasgen-provider next.

## How proxies were eliminated

Almost every Aruba resource nests its name/id in a `metadata` object
(`metadata.name` on create, `metadata.id` on read). That single fact is why the
old `subnet-plugin` existed. Each RestDefinition here replaces it declaratively:

```yaml
resource:
  kind: Subnet
  identifiers: [metadata.name]           # nested identifier — no flattening proxy
  additionalStatusFields: [metadata.id]
  excludedSpecFields: [id]
  verbsDescription:
    - {action: get,    method: GET,    path: .../subnets/{id},
       requestFieldMapping: [{inPath: id, inCustomResource: status.metadata.id}]}
    # ...create/update/delete likewise
```

The full old-plugin → native-feature mapping is in the evolution report's
appendix. The one remaining proxy-shaped need — `compute/CloudServer`'s
multi-call, action-driven lifecycle — is solved **without a proxy** by delegating
create/update/delete to Snowplow RESTActions via the fork's `*ApiRef`
(`restactions/compute/`), with a Krateo Composition (`compositions/`) for
whole-environment provisioning. See
[`docs/lifecycle-beyond-crud.md`](docs/lifecycle-beyond-crud.md).

## Prerequisites

- A Kubernetes cluster with the **braghettos** oasgen-provider installed
  (`ghcr.io/braghettos/krateo-oasgen-provider`) and its rest-dynamic-controller
  (`ghcr.io/braghettos/krateo-rest-dynamic-controller` ≥ 0.16.1). A stock
  (non-braghettos) oasgen-provider does **not** have the features this repo
  relies on (nested identifiers, `fieldMapping`, `requestTransform`, `async`).

## Install

```sh
# 1. Auth token (short-lived Aruba JWT)
kubectl apply -f samples/arubacloud-token-secret.yaml

# 2. OAS ConfigMaps (the oasPath sources) — must be in the oasgen-provider namespace
kubectl apply -n krateo-system -f configmaps/

# 3. The RestDefinitions (all providers, or pick a subset)
kubectl apply -R -f restdefinitions/

# 4. Wait for the generated controllers to become Ready
kubectl get restdefinitions.ogen.krateo.io -A | awk 'NR==1 || /arubacloud/'

# 5. Per resource you want to manage: a <Kind>Configuration, then the CR
kubectl apply -f samples/network/subnet-configuration.yaml
kubectl apply -f samples/network/subnet.yaml
```

## Authentication

Two objects, mirroring the upstream blueprint:

1. a Kubernetes **Secret** holding the Aruba Bearer token
   (`samples/arubacloud-token-secret.yaml`);
2. a **`<Kind>Configuration`** per resource kind, referencing that Secret and
   carrying per-verb query config (e.g. `api-version`), referenced from each CR
   via `spec.configurationRef`.

Tokens are short-lived; rotation is the operator's responsibility.

## Reproducing / regenerating

Everything under `openapi/`, `configmaps/`, `restdefinitions/`, `samples/` and
`docs/coverage.md` is generated. To refresh from updated Aruba specs:

```sh
# refresh openapi/_source/*.json from https://api.arubacloud.com/openapi/, then:
python3 scripts/patch_oas.py                 # -> openapi/  (+ logs every OAS gap)
python3 scripts/generate_restdefinitions.py  # -> restdefinitions/
python3 scripts/gen_configmaps.py            # -> configmaps/
python3 scripts/gen_samples_and_coverage.py  # -> samples/ + docs/coverage.md
```

`patch_oas.py` prints a count of every transformation it applies; each count is a
concrete oasgen-provider gap and is analysed in the evolution report.

## Caveats & assumptions

- **Not cluster-tested.** `api.arubacloud.com` is not reachable from the build
  environment, so these manifests are validated for shape/consistency against the
  OAS and the fork's canonical Subnet example, not against a live API. Treat them
  as a reviewed starting point.
- The metadata-wrapped recipe (`identifiers: [metadata.name]` +
  `additionalStatusFields: [metadata.id]` + `requestFieldMapping id →
  status.metadata.id`) is the exact pattern the fork ships as its Subnet example,
  replicated across resources.
- `findby` assumes the controller extracts items from Aruba's
  `{total, values[]}` list envelope (evolution report §B3).
- The OAS patches (strip `nullable`/`readOnly`, coerce `additionalProperties`)
  change the contract to fit the tool; each is tracked in the evolution report as
  something oasgen-provider should ideally handle natively.
