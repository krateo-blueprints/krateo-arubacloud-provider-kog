---
type: Example
title: network-environment — whole-environment Composition
description: A runnable walkthrough that provisions an Aruba Cloud network environment (VPC + Subnet + SecurityGroup) from one input set, using the aruba-network-environment Composition on top of this repo's proxy-free RestDefinitions.
resource: oci://ghcr.io/krateo-blueprints/charts/aruba-network-environment
tags: [example, composition, cloudserver, arubacloud]
timestamp: 2026-08-11T00:00:00Z
---

# network-environment

Provisions a complete Aruba Cloud environment — `Vpc` + `Subnet` +
`SecurityGroup` and their per-kind `Configuration`s — from one
high-level input set, using the `aruba-network-environment` Krateo
Composition. It is the "composition concept" answer to lifecycle-beyond-CRUD:
many single-purpose CRs orchestrated together.

`CloudServer` is deliberately **not** part of this composition: its create
exists only in `compute-provider_v1.1.json`, which the controller cannot reach
until oasgen#227 ships, so its CRD spec is currently just
`{configurationRef, metadata.name, projectId}` — enough to create an object,
not enough to make a server.
[Lifecycle beyond CRUD](../../docs/lifecycle-beyond-crud.md)).

Files:

- [`token-secret.yaml`](token-secret.yaml) — the shared Aruba Bearer token Secret.
- [`compositiondefinition.yaml`](compositiondefinition.yaml) — registers the
  `aruba-network-environment` chart as a Composition.
- [`values.yaml`](values.yaml) — the environment input set (project, location,
  CIDRs, flavor, token reference), the same defaults the chart ships.

## Preconditions

- A cluster with the **krateo** oasgen-provider + RDC (chart ≥ 0.9.19) — see
  [Usage](../../docs/usage.md#prerequisites).
- This repo's OAS ConfigMaps and RestDefinitions already applied (so the
  `Vpc`/`Subnet`/`SecurityGroup` CRDs exist).
- An Aruba Cloud Bearer token and a project id.

## Run it

```sh
# 1. auth token (edit token-secret.yaml first: set the JWT)
kubectl apply -f token-secret.yaml

# 2. register the Composition (generates its CRD)
kubectl apply -f compositiondefinition.yaml

# 3. create an environment instance. Set projectId + a token reference; the
#    fields map 1:1 to values.yaml (see the Configuration doc's value table).
cat <<'EOF' | kubectl apply -f -
apiVersion: composition.krateo.io/v1alpha1
kind: ArubaCloudserverEnvironment
metadata:
  name: demo
  namespace: default
spec:
  projectId: REPLACE_PROJECT_ID
  location: ITBG-Bergamo
  name: demo
  tokenSecret:
    name: arubacloud-token
    namespace: default
    key: token
  vpc:
    cidr: 10.0.0.0/16
  subnet:
    cidr: 10.0.1.0/24
    type: Advanced
  cloudServer:
    flavorName: A1
    bootVolume:
      sizeGb: 40
EOF

# 4. watch the fan-out
kubectl get vpcs,subnets,securitygroups.arubacloud.ogen.krateo.io -n default
```

The Composition renders the three resources plus their Configurations from the
single input set.

> **Known gap — cross-resource references.** `Subnet` and `SecurityGroup` take
> `vpcId` as a PATH parameter, and that id only exists once the `Vpc` has been
> created — which Helm cannot know at render time. So `vpcId` is a placeholder you
> fill in: apply the chart, read the `Vpc`'s `status.metadata.id`, set `vpcId`, and
> re-apply. Expressing that automatically needs a composition-level reference the
> chart cannot carry; tracked in
> [Lifecycle beyond CRUD](../../docs/lifecycle-beyond-crud.md).

The exact value reference for every field is in
[Configuration](../../docs/configuration.md#composition-chart-values).
