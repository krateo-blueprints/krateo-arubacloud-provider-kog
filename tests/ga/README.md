---
type: Usage
title: GA lifecycle fixtures
description: Manifests and procedure for earning a resource's GA support tier.
tags: [aruba, kog, ga, testing]
timestamp: 2026-08-31T00:00:00Z
---

# GA lifecycle fixtures

A resource is promoted in [`docs/coverage.md`](../../docs/coverage.md) only when
its full lifecycle has been driven against the **live Aruba API**. These are the
manifests that do it, and [`scripts/ga-lifecycle-test.sh`](../../scripts/ga-lifecycle-test.sh)
is the procedure — kept as a script so the evidence is reproducible rather than
something someone once did by hand.

```sh
scripts/get-aruba-token.sh                          # token -> /tmp/aruba.token
kubectl apply -f samples/compute/keypair-configuration.yaml
scripts/ga-lifecycle-test.sh tests/ga/keypair.yaml
```

The script proves create → observe → delete, requires the finalizer to release,
and refuses to start if the token is not live — so a credential problem cannot be
misread as a provider defect.

## Cost

Fixtures here are restricted to resources that are **free or near-free**, because
they are meant to run repeatedly. `KeyPair` is the safest possible first case: it
is free, immediate, and has no `update` verb upstream, so its whole lifecycle is
create → observe → delete.

Anything billable stays out of this directory. If a resource's cost is uncertain,
confirm it before adding a fixture — an orphaned billable resource is the one
failure mode of this procedure that costs real money, which is why the script
verifies cleanup instead of assuming it.

## The dependent chain

`SecurityGroup`, `SecurityRule`, `VpcPeering` and `VpcPeeringRoute` cannot be tested
as standalone fixtures: each needs an id that exists only once its parent has been
created. [`scripts/ga-chain-test.sh`](../../scripts/ga-chain-test.sh) builds the whole
graph, reading each id out of `status` and substituting it into the next manifest.

```
vpc-a ──┬── securityGroup ── securityRule
        └── vpcPeering (→ vpc-b) ── vpcPeeringRoute   [--with-billable]
vpc-b ──┘
```

```sh
scripts/ga-chain-test.sh                  # free resources only
scripts/ga-chain-test.sh --with-billable  # also VpcPeeringRoute
```

Teardown runs in reverse dependency order from an `EXIT` trap, so a failure halfway
through still removes everything created — a half-built chain is the expensive
outcome. Residue is then checked against the Aruba API, with retries, because
deletion is asynchronous and an immediate check reports a false orphan.

**`VpcPeeringRoute` costs money.** Its schema carries
`properties.billingPlan.billingPeriod`, which is how the billable resources announce
themselves; `VpcPeering` has no such field and is free. That is why the route is
opt-in behind a flag rather than part of the default run.

This is also the clearest illustration of the cross-resource reference gap in
[lifecycle-beyond-crud](../../docs/lifecycle-beyond-crud.md): `remoteVpc.uri` is a
runtime URI that Helm cannot know at template-render time, which is exactly why the
Composition leaves those fields as `REPLACE_…`.

## Adding a fixture

1. Add the manifest here, using values that exist in the target account.
2. Run the script and keep the output.
3. Add a row to `TIERS` in `scripts/gen_samples_and_coverage.py` citing that run,
   then regenerate. Tiers are generated from that table, so a promotion cannot be
   made by editing prose.
