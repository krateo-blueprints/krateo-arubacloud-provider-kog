---
type: Usage
title: Getting started
description: Install the CRD chart, token, ConfigMaps and RestDefinitions, then manage the first resource.
tags: [aruba, kog]
timestamp: 2026-10-15T00:00:00Z
---

# Getting started

This walks through installing the provider and managing your first resource (a
`Subnet`) end to end.

## Prerequisites

- A Kubernetes cluster. Which version depends entirely on the chart version, and the
  bound has moved twice:

  | chart | `kubeVersion` | effect |
  |-------|---------------|--------|
  | ≤ 0.24.0 | none | installs anywhere |
  | 0.25.0 – 0.27.1 | `>=1.33.0-0` | excludes only versions already out of support |
  | **0.28.0 +** | `>=1.36.0-0` | **excludes 1.33, 1.34 and 1.35, all currently supported — this is what we pin** |

  The 1.33 bound came from CRD **validation ratcheting**, which is what lets a CR whose
  stored status no longer matches a tightened schema be rewritten instead of rejected.
  It is GA with `LockToDefault: true` only from 1.33; on 1.30–1.32 an operator can
  disable it, and below 1.30 it does not exist — so on an older cluster a status-schema
  change can wedge existing resources. See
  [live-cluster-test](live-cluster-test.md#crd-validation-ratcheting).

  The 1.36 bound is a different and sharper requirement: version-scoped watching
  ([oasgen#178](https://github.com/krateo-platformops/oasgen-provider/pull/178)) starts
  each controller with an exact match on `krateo.io/oas-version`, and that label is
  stamped by a `MutatingAdmissionPolicy`, which 1.33–1.35 do not serve. Without it
  nothing stamps the label, the exact selector matches nothing, and the install comes up
  green while reconciling no instance at all. Upstream's own framing, which is the right
  one: a refused install is the better failure.

  > **You need Kubernetes 1.36 or newer.** We pin oasgen-provider 0.36.0, whose chart
  > declares `kubeVersion: '>=1.36.0-0'`, and Helm refuses the chart on anything older.
  > CI verifies on `kindest/node:v1.36.4`. The floor is the same at 0.34.0 and 0.37.0,
  > so it is the 1.36 bound that costs you something, not the exact pin.
  >
  > This bound is newer than most clusters, and it was taken deliberately rather than
  > inherited. Below 0.34.0 two things we depend on do not work: a per-verb `oasPath`
  > never reaches the controller ([#227](https://github.com/krateo-platformops/oasgen-provider/pull/227)),
  > which is what makes CloudServer's create reachable at all, and an appended
  > `additionalStatusFields` entry is accepted but silently never regenerates an
  > existing CRD ([#225](https://github.com/krateo-platformops/oasgen-provider/pull/225)),
  > which would leave our `status.error` mapping inert on every cluster that already
  > had these CRDs.
  >
  > If you are on 1.33–1.35 and cannot move, pin this repository at **0.2.0**, which
  > validates against oasgen-provider 0.27.1 and installs on `>=1.33`.

- The Krateo **oasgen-provider** and its rest-dynamic-controller, **≥ 0.21.1**.

  > oasgen-provider is a **monorepo**: rest-dynamic-controller and both Helm charts
  > live in it, and the standalone controller repo is archived. The chart derives
  > the RDC tag from its own `appVersion`, so the two images cannot drift apart.
  >
  > A stock upstream oasgen-provider from before this lineage does **not** have the
  > features these RestDefinitions rely on (nested identifiers, `fieldMapping`,
  > `async`, `*ApiRef`, `apiKey` auth).

  ```sh
  helm install oasgen-provider-crds oci://ghcr.io/krateo-platformops/charts/oasgen-provider-crds \
    --version 0.21.1 --namespace krateo-system --create-namespace
  helm install oasgen-provider oci://ghcr.io/krateo-platformops/charts/oasgen-provider \
    --version 0.21.1 --namespace krateo-system
  ```
- An Aruba Cloud Bearer token — see [authentication](authentication.md) and
  <https://api.arubacloud.com/docs/authentication/>.
- `kubectl`, and (optional, for the Composition) `helm`.

## 1. Install the provider CRDs and OAS ConfigMaps

```sh
# OAS ConfigMaps must live in the oasgen-provider namespace (krateo-system here)
kubectl apply -n krateo-system -f configmaps/
```

## 2. Apply the RestDefinitions

Apply everything, or a single provider/resource:

```sh
kubectl apply -R -f restdefinitions/                 # all providers
# or just one provider:
kubectl apply -f restdefinitions/network/
# or one resource:
kubectl apply -f restdefinitions/network/subnet.yaml
```

Each RestDefinition triggers oasgen-provider to generate a CRD and deploy a
controller. Wait for them to become `Ready`:

```sh
kubectl get restdefinitions.ogen.krateo.io -A | awk 'NR==1 || /arubacloud/'
kubectl wait restdefinitions.ogen.krateo.io arubacloud-network-subnet \
  --for=condition=Ready=True -n krateo-system --timeout=300s
```

## 3. Provide credentials

```sh
# a) the token Secret
kubectl apply -f samples/arubacloud-token-secret.yaml   # edit the token first

# b) the per-kind Configuration (auth + per-verb query config)
kubectl apply -f samples/network/subnet-configuration.yaml
```

See [authentication](authentication.md) for how these two objects relate.

## 4. Create a resource

Edit the sample (set `projectId`, `vpcId`, network CIDR, …) and apply it:

```sh
kubectl apply -f samples/network/subnet.yaml
kubectl get subnets.arubacloud.ogen.krateo.io -n default
kubectl describe subnet example-subnet -n default   # watch conditions
```

The `spec` mirrors the Aruba API body: `spec.metadata.name` is the human name,
`spec.properties.*` are the resource settings. On success the server id appears at
`status.metadata.id`.

## 5. Turn on verbose logging (optional)

Add the annotation to any CR to get detailed controller logs for that instance:

```yaml
metadata:
  annotations:
    krateo.io/connector-verbose: "true"
```

## 6. Only if you use `CloudServer`: delegation prerequisites

Skip this unless you manage a `CloudServer`. It is the one resource whose update and
delete are **delegated** to Snowplow RESTActions (its lifecycle is a multi-call
sequence — power state, elastic IPs, security groups, subnets, data volumes — so
there is no single endpoint to PUT). Everything else here talks to Aruba directly and
needs none of this.

Delegation needs **four** things, and the released `oasgen-provider` chart provides
none of them. They are listed in the order you hit them, because each one is invisible
until the one before it is satisfied:

| # | What | What you see without it |
|---|---|---|
| 1 | `rdc.env.URL_SNOWPLOW` | empty means **disabled**; the controller errors with `no snowplow client is configured` and the CR holds its finalizer |
| 2 | `rdc.env.URL_AUTHN` | Snowplow answers `401 {"message":"missing authorization header"}` |
| 3 | a projected `serviceAccountToken` with `audience: authn`, mounted at `/var/run/secrets/krateo.io/serviceaccount` | the token exchange fails before Snowplow is called |
| 4 | a `serviceaccount.authn.krateo.io` ServiceAccount CR matching the controller's ServiceAccount, **plus RBAC for its groups** | authn refuses the exchange with `403` |

You also need **authn itself running**, not merely `authn-crds`. Every Snowplow
`/call` route sits behind middleware that requires an authn-signed bearer token and a
`<username>-clientconfig` Secret, and authn writes that Secret itself during the
login. Snowplow starting and answering `readyz` does **not** mean it will answer
`/call` — testing that a service starts is not testing that it answers.

On (4), two details that are easy to get wrong:

- the issued **username is the ServiceAccount CR's `metadata.name`**, not the
  Kubernetes ServiceAccount's name;
- **authn authors no RBAC.** Its groups become the client certificate's `O=`, so an
  ordinary Role and RoleBinding on those groups is what lets Snowplow read the
  RESTActions and their `endpointRef` Secret. Without it the failure is an
  authorization error, not a `401`.

And the controller's ServiceAccount name must be **read, not constructed**: oasgen
names it `<plural>-<group>-v1alpha1` even where the CRD's served version is different
(`CloudServer`'s is `v1-0-0`, derived from `info.version`), so a name you assemble
yourself matches nothing and the only symptom is a `403`.

A working end-to-end wiring of all four, including the RBAC, is in
[`scripts/pin_verify.sh`](../scripts/pin_verify.sh) under `WITH_SNOWPLOW=1`.

### Where the URLs come from

**Do not copy literal URLs out of this document.** Derive them from your own
install's exposure settings: a shared-port install rewrites component ports, so an
address that is correct on one cluster is dead on another.

On a Krateo installer-built cluster, set them through
`componentValues.oasgen-provider.rdc.env` in the Installer CR — that surface exists
today and needs no chart change. The installer does not yet derive these URLs for
you; when it does, this section will name the version.

That path is a **passthrough**: whatever you set there is written into the composition
CR verbatim and wins over the chart's own default. So if a URL is not what you expect,
the chart values will not show you why — read the live CR instead:

```sh
kubectl get oasgenproviders.composition.krateo.io -o yaml
```

Tracked upstream as
[oasgen-provider#262](https://github.com/krateo-platformops/oasgen-provider/issues/262).

### A failed create cannot currently be deleted

If a delegated resource's **create** fails, the CR cannot be removed: verification of
the delete needs an id that a failed create never produced, so the finalizer is held
indefinitely and must be removed by hand. This is
[oasgen-provider#261](https://github.com/krateo-platformops/oasgen-provider/issues/261),
scheduled for 0.41.0. It is worth knowing before you create your first `CloudServer`,
because on a trial account Aruba refuses every flavor
([Arubacloud/api#29](https://github.com/Arubacloud/api/issues/29)) — so the first
create you attempt may well be one you then have to unwedge.

## Next steps

- Manage a whole environment at once with the
  [Composition](lifecycle-beyond-crud.md#2-cross-resource-a-krateo-composition).
- Manage a `CloudServer` (delegated multi-call lifecycle) — see
  [lifecycle-beyond-crud](lifecycle-beyond-crud.md).
- Browse every resource in the [provider reference](providers/README.md).
- Hitting an error? See [troubleshooting](troubleshooting.md).
