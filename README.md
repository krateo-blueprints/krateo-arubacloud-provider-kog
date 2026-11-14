# Aruba Cloud Provider KOG — proxy-free RestDefinitions

## What is this

**KOG** = *Krateo Operator Generator.*

This repository contains Krateo [`oasgen-provider`](https://github.com/krateo-platformops/oasgen-provider)
`RestDefinition`s that manage **all** manageable Aruba Cloud resources as
Kubernetes Custom Resources — generated directly from the official
[Aruba Cloud OpenAPI specifications](https://api.arubacloud.com/docs/intro) with
**no wrapper/proxy web service** ("plugin").

The predecessor blueprint managed a single resource (`Subnet`) and needed a Go
proxy (`subnet-plugin`) just to reshape Aruba's `metadata` object. The
**krateo-platformops builds** of oasgen-provider and rest-dynamic-controller (both
pinned at **0.65.0**; **0.36.0** is the hard floor, and every move since 0.41.0 was
measured — including 0.53.0 and 0.54.0, both skipped. See the
pin appendices in
[oasgen-provider-evolution](docs/oasgen-provider-evolution.md))
remove that need through nested identifiers, `requestFieldMapping`,
`fieldMapping`, `secretRef`, `async` and Snowplow `*ApiRef` delegation. This repo
uses those features to cover **36 resources across 11 providers with zero
plugins**, and every load-bearing claim has been
[adversarially verified against the executor source](docs/adversarial-review.md).

## Install

```sh
# 0. The provider AND its RestDefinition CRD — two separate charts, same version.
#    Installing only one of them is the most common setup failure: the provider
#    without the CRDs fails every apply with "no matches for kind".
helm install oasgen-provider-crds oci://ghcr.io/krateo-platformops/charts/oasgen-provider-crds \
  --version 0.65.0 --namespace krateo-system --create-namespace
helm install oasgen-provider oci://ghcr.io/krateo-platformops/charts/oasgen-provider \
  --version 0.65.0 --namespace krateo-system

# 1. Auth token. Aruba's token lasts ~1h, so there are two paths:
#    (a) PREFERRED — External Secrets mints and refreshes it. This is what the
#        live-cluster verification uses (scripts/pin_verify.sh installs ESO 2.11.0).
#        Needs ESO on the cluster, plus the client credential stored once:
#          scripts/eso-bootstrap.sh
kubectl apply -f eso/   # ClusterSecretStore + ExternalSecret -> arubacloud-token
#    (b) or paste a token into a Secret by hand — a first look only, it expires:
# kubectl apply -f samples/arubacloud-token-secret.yaml

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

## Configure

### Prerequisites

A Kubernetes cluster with the **krateo-platformops** oasgen-provider and its
rest-dynamic-controller. A stock upstream oasgen-provider does **not**
have the features this repo relies on (nested identifiers, `fieldMapping`,
`async`, `*ApiRef`).

| Component | Minimum | Why |
|-----------|---------|-----|
| `ghcr.io/krateo-platformops/oasgen-provider` | **0.52.0** | **a hard floor, not a preference.** Below 0.35.0 libopenapi cannot parse these OAS documents at all, so no RestDefinition becomes Ready; `metadata` properties are dropped from generated CRDs until [#236](https://github.com/krateo-platformops/oasgen-provider/issues/236) in 0.36.0. Earlier features this repo relies on arrived sooner — `apiKey`-in-header auth and typed-map `additionalProperties` in 0.21.1, per-verb `oasPath` in 0.34.0 ([#227](https://github.com/krateo-platformops/oasgen-provider/pull/227)) — but none of them help below the parse floor. **Why 0.52.0 and not 0.53.0:** 0.52.0 adds [#424](https://github.com/krateo-platformops/oasgen-provider/pull/424) (OAS 3.0 `nullable`/`minProperties` reach the CRD) and [#421](https://github.com/krateo-platformops/oasgen-provider/pull/421) (a `fieldMapping` no longer deletes the spec property it reads from — the whole fleet uses `requestFieldMapping`) while keeping plumbing at v1.14.2. 0.53.0 jumps to plumbing v1.27.0, which stamps `default: {}` on **240** optional object paths across these 36 definitions — measured, including `Vpc.spec.properties`, whose `default` child defaults to `true` and may be held by only one vpc per region. **Also expect 16 false-positive "identifier absent from the findby response" warnings** at 0.52.0: they are an `allOf` blind spot upstream, not a real defect — do not act on them. Both are documented with the measurements in [oasgen-provider-evolution](docs/oasgen-provider-evolution.md) |
| `ghcr.io/krateo-platformops/rest-dynamic-controller` | **0.65.0** | derived from the chart's `appVersion`, so it is not separately selectable. Sends the `apiKey` header + `valuePrefix`, honours `handleParam`, forwards the CR spec on every `*ApiRef` direction, trims Secret-sourced credentials, and redacts credential **headers** in verbose dumps from 0.26.1 ([#166](https://github.com/krateo-platformops/oasgen-provider/pull/166) — see [Authentication](docs/authentication.md)) |
| chart `oci://ghcr.io/krateo-platformops/charts/oasgen-provider` | **0.65.0** | the two components are released in lockstep at identical versions |
| chart `oci://ghcr.io/krateo-platformops/charts/oasgen-provider-crds` | **0.65.0** | same version as the provider chart; it is a separate install |

This is the version CI pins (`OASGEN_VERSION` in `.github/workflows/validate.yaml`
and `PIN` in `scripts/pin_verify.sh`) and the only version any claim in these docs
has been verified at. Note the org hyphen: **`krateo-platformops`** is the live org.
`krateoplatformops` still resolves but is abandoned, with a disjoint tag range, and
`charts.krateo.io` serves an oasgen-provider far below this floor — neither is a
valid source ([oasgen#278](https://github.com/krateo-platformops/oasgen-provider/issues/278)).

oasgen-provider is now a **monorepo**: rest-dynamic-controller and both Helm charts
live in it, and the standalone `rest-dynamic-controller` repo is archived. The chart
derives `rdc.image.tag` from its own `appVersion`, so the two images can no longer
drift apart — a chart version *is* the pair it ships.

```sh
# CRDs first — they are a separate chart
helm install oasgen-provider-crds oci://ghcr.io/krateo-platformops/charts/oasgen-provider-crds \
  --version 0.65.0 --namespace krateo-system --create-namespace

helm install oasgen-provider oci://ghcr.io/krateo-platformops/charts/oasgen-provider \
  --version 0.65.0 --namespace krateo-system
```

> [!IMPORTANT]
> **Install the CRD chart too.** `oasgen-provider` alone leaves you with a running
> provider and no `RestDefinition` CRD — every `kubectl apply` then fails with
> *"no matches for kind"*. The two charts are versioned together.
>
> Version skew between oasgen and RDC used to be the sharp edge here (a chart
> shipping a newer provider against an older controller failed **silently**:
> `handleParam` ignored so HPC async never polled, delegated deletes receiving no
> spec so the CloudServer finalizer never released). Chart 0.21.x derives the RDC
> tag from `appVersion`, which removes that failure mode — see
> [troubleshooting](docs/troubleshooting.md).

### Authentication

Two objects, mirroring the upstream blueprint:

1. a Kubernetes **Secret** holding the Aruba Bearer token
   (`samples/arubacloud-token-secret.yaml`);
2. a **`<Kind>Configuration`** per resource kind, referencing that Secret and
   carrying per-verb query config (e.g. `api-version`), referenced from each CR
   via `spec.configurationRef`.

Tokens are short-lived; rotation is the operator's responsibility.

## Examples

Per-resource Configurations and CRs live under [`samples/`](samples/) (one pair per
resource, all providers), and a full end-to-end composition under
[`examples/network-environment/`](examples/network-environment/).

## Docs

Full docs live in [`docs/`](docs/index.md):

- [Getting started](docs/getting-started.md) · [Architecture](docs/architecture.md) · [Authentication](docs/authentication.md)
- [Provider reference](docs/providers/README.md) (per-provider pages) · [Coverage matrix](docs/coverage.md) · [OAS policy](docs/oas-patches.md) · [Terraform parity](docs/terraform-parity.md)
- [Adding a resource](docs/adding-a-resource.md) · [Lifecycle beyond CRUD](docs/lifecycle-beyond-crud.md) · [oasgen-provider evolution](docs/oasgen-provider-evolution.md) · [GA readiness](docs/ga-readiness.md) · [Live-cluster test](docs/live-cluster-test.md) · [Troubleshooting](docs/troubleshooting.md)

## Develop & release

Everything under `openapi/`, `configmaps/`, `restdefinitions/`, `samples/` and
`docs/coverage.md` is generated. To refresh from updated Aruba specs:

```sh
# refresh openapi/*.json from https://arubacloud.github.io/api/openapi/ (then regenerate
# openapi/CHECKSUMS.txt), and run:
python3 scripts/generate_restdefinitions.py  # -> restdefinitions/
python3 scripts/gen_configmaps.py            # -> configmaps/
python3 scripts/gen_samples_and_coverage.py  # -> samples/ + docs/coverage.md
python3 scripts/gen_provider_docs.py         # -> docs/providers/
```

The specs themselves are never rewritten — `scripts/validate.py` fails if any byte
changes. See [OAS policy](docs/oas-patches.md).

## What's here

| Path | Contents |
|------|----------|
| `openapi/` | The official Aruba OpenAPI specs, vendored **byte-for-byte unmodified** (+ `CHECKSUMS.txt`) |
| `configmaps/` | One ConfigMap per provider embedding its spec verbatim (the `oasPath` source) |
| `restdefinitions/<provider>/` | One `RestDefinition` per manageable resource — **no proxies** |
| `restactions/compute/` | Snowplow RESTActions that drive `CloudServer`'s multi-call lifecycle (delegated via `*ApiRef`) |
| `compositions/` | A Krateo `CompositionDefinition` + Helm chart provisioning a whole environment |
| `samples/<provider>/` | A `<Kind>Configuration` + a `<Kind>` CR skeleton per resource, plus the shared auth `Secret` |
| `eso/` | External Secrets `ClusterSecretStore` + `ExternalSecret` that mint and refresh the Aruba token, so nobody pastes a short-lived JWT by hand |
| `scripts/` | Reproducible generators (`generate_restdefinitions.py`, `gen_configmaps.py`, `gen_samples_and_coverage.py`, `validate.py`) |
| `docs/oas-patches.md` | The **no-modification policy** for the vendored specs, how it is enforced, and what it costs |
| `docs/oasgen-provider-evolution.md` | **Every issue that requires an oasgen-provider evolution** (the analytical deliverable) |
| `docs/lifecycle-beyond-crud.md` | **Proxy-free solution** for lifecycle beyond the 5 CRUD verbs (RESTAction delegation + Composition) |
| `docs/coverage.md` | Full resource/verb coverage matrix |

## Coverage at a glance

11 providers, 36 resources — full CRUD where the API allows it:

- **network**: Vpc, Subnet, SecurityGroup, SecurityRule, ElasticIp, VpcPeering,
  VpcPeeringRoute, VpnTunnel, VpnRoute, LoadBalancer *(read-only)*
- **compute**: CloudServer *(create/get/findby/delete — lifecycle actions need delegation)*, KeyPair
- **container**: Kaas, KaasBackup, Registry
- **database**: Dbaas, Database, DatabaseUser, Grant, DatabaseBackup
- **storage**: BlockStorage, Snapshot, Backup, Restore, Image *(blocked)*
- **security**: Kms, Key, Kmip
- **schedule**: BackupPolicy, BackupPolicyAssignment, ScheduledJob
- **baremetal**: Hpc *(no delete verb in the API)*
- **project**: Project, Folder
- **metering**: AlertRule
- **profile**: AiKey *(blocked)*

### Maturity — read this before depending on a resource

Coverage is not the same as readiness. A tier records what has been **executed**
against the live Aruba API, because every serious defect this project found was
invisible to review and surfaced only on a cluster:

| Tier | Count | Claim |
|------|-------|-------|
| **GA** | 28 | full `create → observe → drift → delete` against the live API — fit for production |
| beta | 1 | verified live short of the full lifecycle; which verb is unproven is recorded per row |
| experimental | 4 | generated and valid; not proven live — including where this account cannot reach the provider at all |
| **blocked** | 3 | known non-functional, or impossible through the API as declared: `network/LoadBalancer` ([#75](https://github.com/krateo-platformops/oasgen-provider/issues/75)), `profile/AiKey`, `storage/Image` |

**The provider as a whole is not yet GA.** Per-resource tiers and the evidence
behind each are in [`docs/coverage.md`](docs/coverage.md); what remains is tracked
in [`docs/ga-readiness.md`](docs/ga-readiness.md).

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

## Caveats & assumptions

- **Tiers record execution, not review.** 28 resources have been driven
  `create → observe → drift → delete` against the live `api.arubacloud.com`; the
  runs, their resource ids and the defects they exposed are in
  [live-cluster-test](docs/live-cluster-test.md). The remaining 8 rows are marked
  beta, experimental or blocked precisely because they have *not* earned that, and
  every serious defect this project found was invisible to static review.
- The metadata-wrapped recipe (`identifiers: [metadata.name]` +
  `additionalStatusFields: [metadata.id]` + `requestFieldMapping id →
  status.metadata.id`) is the exact pattern the fork ships as its Subnet example,
  replicated across resources.
- `findby` relies on the controller unwrapping Aruba's `{total, values[]}` list
  envelope. This is declared behaviour, not an assumption: every one of the 68 list
  envelopes in these specs carries exactly one array, which is the unambiguous case
  `ExtractItemsFromResponse` resolves without configuration, and an envelope with
  two or more arrays is an error rather than a guess (oasgen#110, shipped — so no
  `findby.itemsPath` is declared anywhere in this repo).
- The specs are consumed **unmodified** — no rewriting at all, enforced by a
  sha256 manifest. See [OAS policy](docs/oas-patches.md).
