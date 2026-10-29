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

`CloudServer` is deliberately **not** part of this composition, and the original
reason no longer holds. It used to be that its create exists only in
`compute-provider_v1.1.json`, unreachable until oasgen#227 — that shipped in 0.34.0,
the RestDefinition carries the per-verb `oasPath`, and create is proven live
(`CSO1A2`, `InCreation → Active` in 61s).

It stays out because of two things a Composition cannot assume:

- its update and delete are delegated to Snowplow RESTActions, which need snowplow
  **and** authn plus a projected `audience: authn` token and an authn allowlist CR —
  four prerequisites the oasgen-provider chart does not provide
  ([oasgen#262](https://github.com/krateo-platformops/oasgen-provider/issues/262)).
  Without them the resource wedges on its finalizer instead of failing cleanly.
- create is refused outright on a trial account, and eligibility is not discoverable
  beforehand: the flavor catalog exposes only `{name, cpu, ram}`, so the first signal
  is `{"fieldName":"FlavorId","errorMessage":"<id> not available for trial"}`.

See [Lifecycle beyond CRUD](../../docs/lifecycle-beyond-crud.md).

Files:

- [`token-secret.yaml`](token-secret.yaml) — the shared Aruba Bearer token Secret.
- [`compositiondefinition.yaml`](compositiondefinition.yaml) — registers the
  `aruba-network-environment` chart as a Composition.
- [`values.yaml`](values.yaml) — the environment input set (project, location,
  subnet CIDR, token reference), the same defaults the chart ships. Note there is no
  VPC CIDR and no flavor: Aruba's VPC create body is `{default, preset}`, and no
  `CloudServer` is rendered.

## Preconditions

- A cluster with the **krateo** oasgen-provider + RDC (chart ≥ **0.36.0**) — see
  [Usage](../../docs/usage.md#prerequisites).
- This repo's OAS ConfigMaps and RestDefinitions already applied (so the
  `Vpc`/`Subnet`/`SecurityGroup` CRDs exist).
- An Aruba Cloud Bearer token and a project id.

## Run it

```sh
CD=aruba-network-environment
CD_NS=krateo-system

# 1. auth token (edit token-secret.yaml first: set the JWT)
kubectl apply -f token-secret.yaml

# 2. register the Composition (generates its CRD)
kubectl apply -f compositiondefinition.yaml

# 3. read the apiVersion and kind core-provider ACTUALLY created. Do not hardcode
#    them: core-provider derives the version from the FULL chart version, patch
#    included ("v" + chartVersion with dots replaced by dashes), so chart 0.1.3 is
#    served at v0-1-3. `.status` is empty until the CompositionDefinition
#    reconciles, and an empty apiVersion fails later as `no matches for kind` --
#    which is indistinguishable at the CLI from the CRD not existing. So wait for
#    it rather than reading it once.
for i in $(seq 1 60); do
  API=$(kubectl get compositiondefinition "$CD" -n "$CD_NS" \
          -o jsonpath='{.status.apiVersion}' 2>/dev/null)
  PLURAL=$(kubectl get compositiondefinition "$CD" -n "$CD_NS" \
          -o jsonpath='{.status.resource}' 2>/dev/null)
  [ -n "$API" ] && [ -n "$PLURAL" ] && break
  sleep 5
done
[ -n "$API" ] && [ -n "$PLURAL" ] || { echo "CompositionDefinition never reported a status"; exit 1; }
KIND=$(kubectl get crd "${PLURAL}.${API%%/*}" -o jsonpath='{.spec.names.kind}')
echo "serving ${API}, kind ${KIND}, plural ${PLURAL}"

# 4. create an environment instance. The fields map 1:1 to values.yaml
#    (see the Configuration doc's value table).
cat <<EOF | kubectl apply -f -
apiVersion: ${API}
kind: ${KIND}
metadata:
  name: demo
  namespace: default
spec:
  namespace: default
  projectId: REPLACE_PROJECT_ID
  # A location, not a dataCenter. Aruba's dataCenter is a ZONE WITHIN a location
  # (ITBG-1), and the two are not interchangeable -- see docs/configuration.md.
  location: ITBG-Bergamo
  tags: []
  apiVersion: "1.0"
  name: demo
  tokenSecret:
    name: arubacloud-token
    namespace: default
    key: token
  # Filled in on the second pass -- see the known gap below.
  vpcId: REPLACE_WITH_VPC_ID
  vpc:
    default: false
    preset: false
  subnet:
    type: Advanced
    cidr: 10.0.1.0/24
    default: false
  securityGroup:
    default: false
    preset: false
EOF

# 5. watch the fan-out
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
