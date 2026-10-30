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

# 3. read the apiVersion, kind and plural core-provider ACTUALLY created. Do not
#    hardcode and do not derive them.
#
#    The VERSION comes from the full chart version, patch included ("v" + the
#    version with dots replaced by dashes), at three code sites in core-provider --
#    so chart 0.3.8 is served at v0-3-8. This chart carries `version: CHART_VERSION`,
#    substituted at release, so no correct literal exists at any commit.
#
#    The PLURAL is not derivable at all: core-provider resolves it through
#    plumbing/kubeutil/plurals, not a string rule. core-provider measured
#    `strings.ToLower(kind)+"s"` silently dropping 32 of 83 rows in a fleet-wide
#    join. Only `kind` follows a rule, and it is served too, so read all three.
#
#    Wait on the CONDITION, not on a non-empty string. Since core-provider 2.13.39
#    an unresolvable chart sets Ready=False with reason Unavailable and the registry
#    error in the message -- and a bad CHART_VERSION substitution is exactly an
#    unresolvable chart. Polling for a non-empty apiVersion would spin to the
#    timeout and tell you nothing; this fails fast and says why.
kubectl wait --for=condition=Ready "compositiondefinition/${CD}" -n "$CD_NS" --timeout=5m

API=$(kubectl get compositiondefinition "$CD" -n "$CD_NS" -o jsonpath='{.status.apiVersion}')
KIND=$(kubectl get compositiondefinition "$CD" -n "$CD_NS" -o jsonpath='{.status.kind}')
PLURAL=$(kubectl get compositiondefinition "$CD" -n "$CD_NS" -o jsonpath='{.status.resource}')
# Ready is core-provider's overall reconcile condition; it is not DEFINED as "status
# is populated", so the three fields are still checked rather than assumed. They
# agreed in both directions on all 69 definitions of core-provider's fleet, but that
# is an observation, not a documented invariant.
[ -n "$API" ] && [ -n "$KIND" ] && [ -n "$PLURAL" ] \
  || { echo "Ready=True but status is incomplete: apiVersion='${API}' kind='${KIND}' resource='${PLURAL}'"; exit 1; }
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
