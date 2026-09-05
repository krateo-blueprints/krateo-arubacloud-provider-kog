---
type: Decision
title: Payload derivation for the remaining resources
description: What a 32-agent adversarial derivation found before any billable run was attempted.
tags: [aruba, kog, ga, testing]
timestamp: 2026-09-03T00:00:00Z
---

# Deriving the remaining payloads

Bringing the last 23 resources to GA was bottlenecked on one thing: discovering each
resource's minimal valid create payload. Doing that by trial and error costs a failed
**billable** run per attempt, and the API returns constraints the OAS never declares.

So the derivation was fanned out — one agent per provider reading the OAS, the
generated CRDs and Aruba's published metadata — and every result was then
adversarially verified by a second agent instructed to **refute** it.

**19 of 22 payloads were refuted.** That is the headline: had they been run as
derived, nineteen billable attempts would have failed, several leaving resources
behind.

Full output: [`tests/ga/derived/payloads.yaml`](../tests/ga/derived/payloads.yaml).

## Why they were refuted

| Reason | Count | Examples |
|--------|-------|----------|
| **Invented literal values** | 13 | `Kaas.nodePools[].instance`, `KaasBackup.type`, `VpnRoute.cloudSubnet`, `Job.actionUri` |
| **Wrong apiVersion assumed** | 12 | everything assumed `v1-0-0` |
| **Configuration CR missing** | 9 | `Dbaas`, `Registry`, `ElasticIp`, `Grant`, `Job` |
| **Unresolvable placeholder** | 6 | `DatabaseBackup.database.name` bound to a resource with no id |
| **No delete verb at all** | 1 | `baremetal/Hpc` |

The dominant failure is inventing enum values, which is the same root cause behind
every undeclared-constraint finding in [live-cluster-test](live-cluster-test.md):
Aruba's OAS routinely declares a bare `string` where the API enforces a fixed set, and
points at a documentation page instead.

## `baremetal/Hpc` cannot reach GA as the bar is written

`openapi/baremetal-provider.json` contains **zero delete operations**. The only `hpcs`
operations are `POST /hpcs`, three `GET`s, and two `PUT`s (`name`, `automaticrenew`).

Verified against Aruba's own published documentation, not just the vendored spec: the
[`Arubacloud/api`](https://github.com/Arubacloud/api) docs tree contains exactly seven
baremetal operations — `create-hpc`, `list-hpc`, `get-hpc`, `get-hpc-services`,
`check-hpc-creation-status`, `rename-hpc`, `set-hpc-automatic-renew`. There is no
delete, cancel, terminate or decommission operation anywhere.

**Correction to an earlier claim.** I first wrote that creating an HPC is
"irreversible". That is too strong. Deprovisioning exists — it is just not a delete:

```jsonc
// PUT /projects/{projectId}/providers/Aruba.Baremetal/hpcs/{id}/automaticrenew
{ "paymentMethodId": string|null,
  "months": int|null,
  "activate": boolean,                 // false = stop renewing
  "actionOnFolder": "RemoveFromFolder" | "DisableSafeFolder" }
```

Setting `activate: false` stops renewal, so the machine **lapses at the end of its
paid term** rather than being removed on demand. That is a subscription lifecycle, and
`months` + `paymentMethodId` in that body confirm it. The create body carries no term
field at all, so the term is implicit.

What this means concretely:

- The spend is **bounded** (one term), not unbounded — but it is **not recoverable
  within a test run**, and a chain's teardown cannot return the account to its prior
  state.
- The GA bar `create → observe → delete` is **unreachable as written**, because no
  delete exists. It could honestly be redefined for this resource as
  `create → observe → disable-renew`, which is what the API actually offers — but that
  still leaves real bare metal running until the term expires.
- The payload is **underivable from published sources**: `node.sku`, `node.os`,
  `network.bandwidth` and `firewall.sku` are bare strings with no enum, and Aruba's
  metadata page — which does publish CloudServer flavors, KaaS sizes and DBaaS engines
  — **lists nothing for baremetal at all**. Confirmed by fetching it.

So HPC is blocked on two independent things: values that cannot be sourced, and a
lifecycle that cannot be closed. Recorded rather than attempted.

## Fixed as a direct result

- **apiVersion is now derived from the cluster**, not assumed. `metering/AlertRule` is
  served at **`v1-0`**, because `metering.json` declares `info.version: "1.0"` rather
  than `"1.0.0"` — every other resource is `v1-0-0`. A chain entry for it would have
  been rejected outright.
- The three payloads that survived — `project/Folder`, `security/Key`,
  `database/DatabaseUser` — carry their verifier's caveats in `payloads.yaml`, notably
  that `Key` and `DatabaseUser` have no safe field to perturb, so their GA bar is
  `create → observe → delete`.
