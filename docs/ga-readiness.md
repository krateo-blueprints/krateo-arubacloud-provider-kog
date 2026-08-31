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

### Proposed GA core (10)

Chosen because each is free or near-free, so the full lifecycle can be exercised
repeatedly in CI without a cost conversation:

`network`: **Vpc, Subnet, SecurityGroup, SecurityRule, VpcPeering, VpcPeeringRoute**
· `compute`: **KeyPair** · `project`: **Project, Folder** · `network`:
**LoadBalancer** *(read-only by construction — no create verb exists, so observe
is its whole lifecycle)*

### Beta

Everything requiring billable or slow provisioning: `storage` (BlockStorage,
Snapshot, Backup, Restore), `security` (Kms, Key, Kmip), `container` (Kaas,
KaasBackup, Registry), `database` (Dbaas, Database, DatabaseUser, Grant,
DatabaseBackup), `schedule`, `metering`, `baremetal/Hpc`, `network` ElasticIp and
VpnTunnel/VpnRoute.

### Experimental

`compute/CloudServer` and the `aruba-cloudserver-environment` Composition — see
blocker P0-3.

## Blockers

### P0 — must clear before any GA claim

| # | Blocker | Scope | Owner |
|---|---|---|---|
| P0-1 | **Token expires hourly.** The ESO rotation path is documented but **untested**; a stock install stops working after ~60 minutes. Rotation-without-restart *is* verified, so the premise holds — the manifests do not. | all tiers | this repo |
| P0-2 | **GA-core lifecycle evidence.** 9 of the 10 core resources have never been created. Only Subnet has a completed cycle. | GA core | this repo |
| P0-3 | **CloudServer is unproven.** Its RESTActions have never executed (they need snowplow plus `URL_SNOWPLOW`, which no chart sets), and the Composition still carries `REPLACE_…` cross-resource placeholders. | experimental | this repo + upstream |

### P1 — before the GA label is durable

| # | Blocker | Note |
|---|---|---|
| P1-1 | **No CI gate.** `validate.py` and a kind smoke test must run on every PR, or the seven-defect pattern simply repeats. | this repo |
| P1-2 | **No upstream-spec drift detection.** Aruba's OAS will change; nothing notices today. The checksum manifest pins what we vendored, not what upstream now publishes. | this repo |
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
3. CI runs `validate.py` plus a kind smoke test (all 34 apply, all reach `Ready`)
   on every PR.
4. Tier labels appear per resource in `docs/coverage.md` and the README, each
   backed by linked evidence.
5. Upstream-spec drift is detected automatically, not discovered by a user.
6. No P0 open.

## Sequence

**Phase 1 — prove the core.** P0-2 and P0-1: full lifecycle on the 10 GA-core
resources; make ESO rotation real and survive an expiry. Cheap resources only, per
the agreed test budget.

**Phase 2 — make it repeatable.** P1-1 and P1-2: CI gate and upstream drift
detection. Without these, phase 1's evidence decays silently.

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
