---
type: Example
title: cloudserver-environment — whole-environment Composition
description: A runnable walkthrough that provisions a complete Aruba Cloud environment (VPC + Subnet + SecurityGroup + CloudServer) from one input set, using the aruba-cloudserver-environment Composition on top of this repo's proxy-free RestDefinitions.
resource: oci://ghcr.io/krateo-blueprints/charts/aruba-cloudserver-environment
tags: [example, composition, cloudserver, arubacloud]
timestamp: 2026-08-11T00:00:00Z
---

# cloudserver-environment

Provisions a complete Aruba Cloud environment — `Vpc` + `Subnet` +
`SecurityGroup` + `CloudServer` and their per-kind `Configuration`s — from one
high-level input set, using the `aruba-cloudserver-environment` Krateo
Composition. It is the "composition concept" answer to lifecycle-beyond-CRUD:
many single-purpose CRs orchestrated together, with the `CloudServer`'s own
multi-call lifecycle handled by its RestDefinition's `*ApiRef` delegation (see
[Lifecycle beyond CRUD](../../docs/lifecycle-beyond-crud.md)).

Files:

- [`token-secret.yaml`](token-secret.yaml) — the shared Aruba Bearer token Secret.
- [`compositiondefinition.yaml`](compositiondefinition.yaml) — registers the
  `aruba-cloudserver-environment` chart as a Composition.
- [`values.yaml`](values.yaml) — the environment input set (project, location,
  CIDRs, flavor, token reference), the same defaults the chart ships.

## Preconditions

- A cluster with the **krateo** oasgen-provider + RDC (chart ≥ 0.9.19) — see
  [Usage](../../docs/usage.md#prerequisites).
- This repo's OAS ConfigMaps and RestDefinitions already applied (so the
  `Vpc`/`Subnet`/`SecurityGroup`/`CloudServer` CRDs exist).
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
kubectl get vpcs,subnets,securitygroups,cloudservers.arubacloud.ogen.krateo.io -n default
```

The Composition renders the four resources plus their Configurations from the
single input set. The `CloudServer`'s create/update/delete are delegated to the
Snowplow RESTActions under `restactions/compute/`; `get`/`findby` stay native.

> **Known gap — cross-resource references.** Aruba refers to a VPC/subnet/SG by a
> runtime URI that only exists after creation, which Helm cannot know at render
> time. This example provisions the networking and passes the desired
> subnet/security-group selection through `spec.properties`; wiring the concrete
> runtime URIs into the `CloudServer` is tracked in
> [Lifecycle beyond CRUD](../../docs/lifecycle-beyond-crud.md).

The exact value reference for every field is in
[Configuration](../../docs/configuration.md#composition-chart-values).
