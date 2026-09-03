---
type: Reference
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

The consequences are worth stating plainly:

- **Creating an HPC is irreversible through the API.** `kubectl delete` removes the CR;
  the bare-metal machine keeps running and keeps billing.
- The GA bar — `create → observe → delete` for a resource with no update verb — is
  therefore **unreachable**, not merely untested.
- Its payload is also underivable: `node.sku`, `node.os`, `network.bandwidth` and
  `firewall.sku` are bare strings with no enum, and **no SKU, OS or bandwidth value
  appears anywhere** in the 13 vendored documents, the docs, the samples or the
  compositions.

This is a decision, not a task: exercising HPC means provisioning bare metal that
cannot be deprovisioned through the API this provider uses. Recorded here rather than
attempted.

## Fixed as a direct result

- **apiVersion is now derived from the cluster**, not assumed. `metering/AlertRule` is
  served at **`v1-0`**, because `metering.json` declares `info.version: "1.0"` rather
  than `"1.0.0"` — every other resource is `v1-0-0`. A chain entry for it would have
  been rejected outright.
- The three payloads that survived — `project/Folder`, `security/Key`,
  `database/DatabaseUser` — carry their verifier's caveats in `payloads.yaml`, notably
  that `Key` and `DatabaseUser` have no safe field to perturb, so their GA bar is
  `create → observe → delete`.
