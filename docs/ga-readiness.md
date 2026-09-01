---
type: Reference
title: GA readiness
description: What GA means for this provider, what blocks it, and how each tier is earned.
tags: [aruba, kog, ga, release]
timestamp: 2026-08-31T00:00:00Z
---

# GA readiness

**Goal: make this provider generally available for public use.**

The governing principle comes from this repository's own history. Every serious
defect found so far — the `oasPath` regex, the CRD version derivation, the
credential newline, the unfixable drift loop, the RBAC `watch` verb, the
Configuration query fields, the separate CRD chart — was **invisible to static
review and surfaced only on a real cluster**. Seven for seven.

So GA here is defined by *executed* behaviour, never by reviewed behaviour. A
resource is GA when it has been driven through its lifecycle against the live
Aruba API, not when its RestDefinition looks right.

## Tiers

A resource's tier is **earned by test evidence**, not asserted.

| Tier | Bar | Support claim |
|------|-----|---------------|
| **GA** | full `create → observe → drift → delete` against the live API, in CI-reproducible form | fit for public production use |
| **Beta** | applies cleanly, reaches `Ready`, observe verified; mutation unproven | usable, sharp edges documented |
| **Experimental** | generated and statically valid only | try it, expect to find things |

### Proposed GA core (9)

Chosen because each is free or near-free, so the full lifecycle can be exercised
repeatedly in CI without a cost conversation:

`network`: **Vpc, Subnet, SecurityGroup, SecurityRule, VpcPeering, VpcPeeringRoute**
· `compute`: **KeyPair** · `project`: **Project, Folder**

`LoadBalancer` was in this list and has been **removed** — see P0-4.

### Beta

Everything requiring billable or slow provisioning: `storage` (BlockStorage,
Snapshot, Backup, Restore), `security` (Kms, Key, Kmip), `container` (Kaas,
KaasBackup, Registry), `database` (Dbaas, Database, DatabaseUser, Grant,
DatabaseBackup), `schedule`, `metering`, `baremetal/Hpc`, `network` ElasticIp and
VpnTunnel/VpnRoute.

### Experimental

`compute/CloudServer` and the `aruba-cloudserver-environment` Composition — see
blocker P0-3 — and `network/LoadBalancer`, see P0-4.

**P0-4 in detail.** The generator gives every read-only resource
`identifiers: [name]` on the assumption the name is a spec field, which is true
only when a create body defines it. Fixing it needs one of: a synthetic spec field
the user fills in to select the instance (the same capability §B4 wants for
`*SecretRef`, and not expressible from a pure OAS today); selecting by a path
parameter where the API offers one; or accepting that read-only resources are
only addressable once `status.id` is known. Until then the resource is generated
but not usable, and saying so is better than shipping it in a GA list.

## Blockers

### P0 — must clear before any GA claim

| # | Blocker | Scope | Owner |
|---|---|---|---|
| P0-4 | **Read-only resources have no usable identifier.** `LoadBalancer` declares `identifiers: [name]`, but a read-only resource has no create body, so no identifier field is ever materialised in its CRD spec — which holds only `configurationRef` and `projectId`. There is no way to say *which* load balancer is meant: `findby` can never match, and `get` binds `{id}` from a `status.id` nothing populates. Unusable by construction. Filed as [oasgen-provider#75](https://github.com/krateo-platformops/oasgen-provider/issues/75). | `network/LoadBalancer` | this repo + upstream |
| P0-1 | **Token expires hourly.** The ESO rotation path is documented but **untested**; a stock install stops working after ~60 minutes. Rotation-without-restart *is* verified, so the premise holds — the manifests do not. | all tiers | this repo |
| P0-2 | **GA-core lifecycle evidence.** 3 of 9 done — Subnet, KeyPair and Vpc have completed full lifecycles against the live API, Vpc including out-of-band drift correction. Remaining: SecurityGroup, SecurityRule, VpcPeering, VpcPeeringRoute, Project, Folder. | GA core | this repo |
| P0-5 | **Empty arrays in a CR spec are unenforceable.** RDC's `compareSlices` iterates only the CR's slice, so an empty one matches any remote value and the controller reports `Ready` while diverged. Emptying any list is invisible to drift detection; positional comparison makes reordering invisible too. Found live: `tags: []` never corrected `["drifted-by-hand"]`. | all tiers | upstream |
| P0-3 | **CloudServer is unproven.** Its RESTActions have never executed (they need snowplow plus `URL_SNOWPLOW`, which no chart sets), and the Composition still carries `REPLACE_…` cross-resource placeholders. | experimental | this repo + upstream |

### P1 — before the GA label is durable

| # | Blocker | Note |
|---|---|---|
| ~~P1-1~~ | ~~No CI gate.~~ **Done** — `.github/workflows/validate.yaml` gates every PR on `validate.py`, generator determinism, and a kind smoke test (34/34 Ready, all 69 samples server-dry-run against their generated CRDs, provider error log clean). **Proven green in CI** — run [33418610761](https://github.com/krateo-blueprints/krateo-arubacloud-provider-kog/actions/runs/33418610761): a clean kind cluster reached 34/34 Ready, all 69 samples were accepted, and the provider logged nothing. | this repo |
| ~~P1-2~~ | ~~No upstream-spec drift detection.~~ **Done** — `.github/workflows/oas-drift.yaml` re-downloads all twelve specs weekly and files one issue on any change. **Proven green in CI**, reporting 12/12 unchanged. Building it corrected the documented source URL, which pointed at a redirect serving HTML for every filename — following the documented refresh procedure would have overwritten the specs with an error page. | this repo |
| P1-3 | **`findby` item extraction is a heuristic** — RDC returns the *first array-valued key* of the response object. Correct for Aruba today (one array), nondeterministic the day a response carries two. | upstream (§B3) |
| P1-4 | **`oasPath` regex** rejects hyphenated ConfigMap keys — [oasgen-provider#74](https://github.com/krateo-platformops/oasgen-provider/pull/74) open. | upstream |

### P2 — beta-tier quality, not GA blockers

- **`DatabaseUser.password` is a plaintext spec field** (§B4). Unacceptable for a
  GA resource; acceptable for a *documented* beta one. It gates promoting the
  `database` provider, not the GA core.
- Fidelity gaps §A1, §A3–A6 (`nullable`, `readOnly`, `number`, `format`,
  constraints) — cosmetic today, real if response-body validation is ever enabled.
- Action endpoints §C1 (power on/off, attach/detach, rename) remain
  RESTAction-only.

## Exit criteria

GA is declared when **all** hold:

1. Every GA-core resource has a recorded, reproducible full lifecycle against the
   live API, with the account left as found.
2. A token-rotation path is **executed** end to end, not sketched — an install
   survives past token expiry unattended.
3. ~~CI runs `validate.py` plus a kind smoke test (all 34 apply, all reach `Ready`)
   on every PR.~~ **Met.**
4. ~~Tier labels appear per resource in `docs/coverage.md` and the README, each
   backed by linked evidence.~~ **Met** — generated from an explicit evidence table in
   `scripts/gen_samples_and_coverage.py`, so a promotion requires citing what was run.
   Standing today: 1 GA, 1 beta, 31 experimental, 1 blocked.
5. ~~Upstream-spec drift is detected automatically, not discovered by a user.~~ **Met.**
6. No P0 open.

## Sequence

**Phase 1 — prove the core.** P0-2 and P0-1: full lifecycle on the 10 GA-core
resources; make ESO rotation real and survive an expiry. Cheap resources only, per
the agreed test budget.

**Phase 2 — make it repeatable.** ~~P1-1 and P1-2: CI gate and upstream drift
detection.~~ **Done**, and deliberately done before phase 1 completes: without the
gate, phase 1's evidence decays silently as the repo changes underneath it.

**Phase 3 — label and ship.** Tier tables, per-resource evidence links, install
docs stated against the tier a user is choosing.

**Phase 4 — earn promotions.** Move resources from beta to GA as evidence
accumulates; fix P2 items to unblock `database`; resolve CloudServer.

## Explicitly out of scope for GA

Stated so absence reads as a decision rather than an oversight:

- **Full CloudServer day-2 lifecycle** (power, associations, volumes) stays
  experimental until its RESTActions are executed against the live API.
- **Cross-resource reference wiring** in the Composition — needs Krateo's
  reference resolution; today the operator supplies ids.
- **HPC async completion** — bare metal is too costly to exercise routinely;
  the async block is wired and admission-validated, but never observed reaching
  `Succeeded`.
