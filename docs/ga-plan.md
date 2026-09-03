---
type: Reference
title: GA completion plan
description: How every resource reaches a GA tier, in what order, and what it costs.
tags: [aruba, kog, ga, plan]
timestamp: 2026-09-01T00:00:00Z
---

# Completing GA across all 34 resources

Companion to [ga-readiness](ga-readiness.md), which defines the tiers. This is the
route from **4 GA / 4 beta / 25 experimental / 1 blocked** to a defensible GA claim
across the whole surface.

## The bar, restated precisely

GA means `create → observe → drift → delete` executed against the live API. One
refinement the evidence forced: **8 resources have no `update` verb upstream**, so
drift correction is not merely untested but impossible.

> `CloudServer`, `Database`, `DatabaseBackup`, `DatabaseUser`, `Grant`, `Hpc`,
> `KeyPair`, `LoadBalancer`

For these, GA is `create → observe → delete` and the tier note must say so. This is
not a loosened bar — it is the correct bar for an immutable resource, and `KeyPair`
was already promoted on exactly that basis.

## What actually blocks a wholesale GA claim

Three defects apply to **every** resource, so no amount of per-resource testing
retires them. They are the real critical path.

| # | Defect | Effect on a GA claim |
|---|--------|----------------------|
| [#76](https://github.com/krateo-platformops/oasgen-provider/issues/76) | An empty array in a CR spec matches any remote array | Every resource with a list field has an unenforceable declaration. A GA promise of "the cluster converges to your spec" is false wherever a list is emptied. |
| [#75](https://github.com/krateo-platformops/oasgen-provider/issues/75) | Read-only resources materialise no identifier | `LoadBalancer` cannot work at all; blocks any future read-only resource. |
| — | Delete finalizers release on *requested*, not *completed* | A failed async delete orphans a billable resource with no CR left to retry. Unacceptable for a resource anyone pays for. |

**#76 is the one to fix first.** It is the only defect that silently weakens a
guarantee we would be actively advertising, and it is a small change — the argument
is about which semantics to adopt, not about the code.

## Cost reality

Derived from the generated CRDs, which resolve the OAS correctly (an earlier
hand-rolled scan under-reported this and is not trustworthy):

**Carry a billing field (12):** `Backup`, `BlockStorage`, `DatabaseBackup`, `Dbaas`,
`ElasticIp`, `Kaas`, `KaasBackup`, `Kms`, `Registry`, `Snapshot`, `VpcPeeringRoute`,
`VpnTunnel`

> Corrected. An earlier pass searched only for `billingPlan` and reported 7. Five
> resources declare **`billingPeriod`** without a `billingPlan` wrapper —
> `BlockStorage`, `Snapshot`, `Backup`, `KaasBackup` and, importantly, **`Kms`**.
> Since `Key` and `Kmip` both require a Kms `id`, the whole `security` provider sits
> behind a billable parent, which is the opposite of what Wave 2 assumed.

**Absence of `billingPlan` does not mean free.** `BlockStorage`, `Snapshot`,
`Backup` and `Hpc` bill by capacity or by hour without declaring a plan in the
create body. Treat the declared list as a floor, and price anything in `storage`,
`container`, `database`, `security` or `baremetal` before running it.

## Waves

Ordered so that structural work lands before it is needed, and cost rises late.

### Wave 0 — make the runner trustworthy and reusable

The current `ga-chain-test.sh` hardcodes one chain and has already shipped two bugs
that left real resources running. Before it is pointed at 25 more resources:

1. **Re-validate the teardown fix.** It has never completed a successful run. Do this
   on the existing free network chain, and require the residue check to pass.
2. **Add a drift step.** The runner proves create/observe/delete; drift is still done
   by hand, which is why three resources sit at beta rather than GA. It needs a
   per-resource "safe field to perturb" — never the identifier, never an
   account-wide flag.
3. **Make chains declarative.** Move the graph into a fixture file
   (`tests/ga/chains/*.yaml`: kind, parent bindings, drift field, billable flag) so
   each new resource is *data*, not another bespoke script.

Without step 3 the remaining 25 resources mean 25 more chances to write the same
subshell bug.

### Wave 1 — finish the free network provider (7 resources)

`Vpc`, `Subnet` already GA. Promote `SecurityGroup`, `SecurityRule`, `VpcPeering`
from beta by adding the drift step; add `VpnTunnel`/`VpnRoute` and `ElasticIp`
(billable — time-box and delete immediately).

Chains: `Vpc → {Subnet, SecurityGroup → SecurityRule, VpcPeering → VpcPeeringRoute}`
and `VpnTunnel → VpnRoute`.

### Wave 2 — free standalone resources (revised: 3, not 9)

`BackupPolicy` is **done** — full lifecycle including drift, on the declarative
runner.

What is actually free is much smaller than this wave first assumed:

- **`security` moved to a billable wave.** `Kms` carries `billingPeriod`, and `Key`
  and `Kmip` both require a Kms `id`, so all three are gated behind a paid parent.
- **`BackupPolicyAssignment` is not independently testable.** It binds a policy to a
  `resource.uri`, and the assignable resources are block storage, which bills.
- **`Job` needs a target.** The API rejects a job without exactly one `steps[]` entry,
  and a step is a `resourceUri` + `actionUri` pair pointing at something that exists.

That leaves `AlertRule` and `Job` (the latter only once a safe target exists).

### Wave 3 — storage (4)

`BlockStorage`, `Snapshot`, `Backup → Restore`. Bills by capacity. Use the smallest
allowed size, run once, delete, and verify residue against ground truth rather than
trusting the CR. This wave is where the delete-on-*requested* defect stops being
theoretical: an orphaned volume bills indefinitely.

**Do not run this wave until that defect is resolved**, or run it with a manual
post-check of the console every time.

### Wave 4 — expensive and slow (10)

`container` (`Kaas`, `KaasBackup`, `Registry`), `database` (`Dbaas`, `Database`,
`DatabaseUser`, `Grant`, `DatabaseBackup`), `baremetal` (`Hpc`).

A Kaas cluster or a Dbaas instance takes many minutes to provision and costs real
money per run. Two honest options:

- **Promote to GA once**, with a recorded run, and accept that CI cannot re-verify
  them on every change.
- **Leave them beta permanently**, documented as "lifecycle proven once, not
  continuously verified".

The second is more honest if CI cannot afford them, and a beta label that is
accurate beats a GA label that decays. `Hpc` additionally needs its `async` block
observed reaching `Succeeded`, which has never happened.

`database` also carries **P2: `DatabaseUser.password` is a plaintext spec field**.
That must be fixed before `database` is promoted, regardless of lifecycle evidence.

### Wave 5 — blocked on upstream (2)

`LoadBalancer` needs [#75](https://github.com/krateo-platformops/oasgen-provider/issues/75).
`CloudServer` needs P0-3: its RESTActions have never executed, because they require
snowplow plus `URL_SNOWPLOW`, which no chart sets. Both stay where they are until
upstream moves; neither is a testing problem.

## Continuous verification

A run that happened once decays. Two additions:

- **Nightly GA job** against a real account, running waves 1–2 (free) end to end and
  failing the build on residue. ESO already removes the credential obstacle — the
  cluster mints its own tokens, so this needs no human in the loop.
- **Tier expiry.** A GA tier whose last recorded run is older than N days is reported
  stale by `validate.py`. This makes decay visible instead of silent, and is the only
  mechanism here that defends the claim over time.

## Sequencing rationale

The order is deliberate. #76 first, because it invalidates a guarantee we would be
making. Then the runner, because 25 resources through an untrustworthy harness is how
resources get left running. Then cost, ascending. Waves 4 and 5 are the ones where
the right answer may be "document it as beta" rather than "spend more" — and deciding
that explicitly is better than an indefinite backlog.

## Definition of done

Not "34 resources GA". That is not achievable honestly while CloudServer is blocked
upstream and Kaas costs real money per verification. Done is:

1. No P0 open.
2. Every resource carries a tier backed by a linked run, or a recorded reason it
   cannot be promoted.
3. Free resources are re-verified nightly; billable ones carry a dated run.
4. The README states the provider's overall status in terms a user can act on:
   which resources are safe for production, which are sharp, which are known broken.
