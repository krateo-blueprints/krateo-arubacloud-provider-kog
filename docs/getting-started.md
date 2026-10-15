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
  | ≤ 0.24.0 | none | installs anywhere; **this is what we pin** |
  | 0.25.0 – 0.27.1 | `>=1.33.0-0` | excludes only versions already out of support |
  | **0.28.0 +** | `>=1.36.0-0` | **excludes 1.33, 1.34 and 1.35, all currently supported** |

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

  > So **1.36+ for anything current**. We pin 0.22.2, which has no floor and is why this
  > repo still verifies on 1.33 in CI — see the note on `node_image` in
  > `.github/workflows/validate.yaml`.

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

## Next steps

- Manage a whole environment at once with the
  [Composition](lifecycle-beyond-crud.md#2-cross-resource-a-krateo-composition).
- Manage a `CloudServer` (delegated multi-call lifecycle) — see
  [lifecycle-beyond-crud](lifecycle-beyond-crud.md).
- Browse every resource in the [provider reference](providers/README.md).
- Hitting an error? See [troubleshooting](troubleshooting.md).
