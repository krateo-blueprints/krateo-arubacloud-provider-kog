---
type: Runbook
title: authn 0.28.1 HA verification
description: Leader-election checks for authn 0.28.1 at replicaCount=3, scoped by the authn maintainers on authn#45, with the verdict logic tested against six broken elections.
tags: [authn, kog, experimental]
timestamp: 2026-10-29T00:00:00Z
---

# authn 0.28.1 HA verification

Requested by the `authn` session for **authn#45**: they need these checks on a
cluster that is not theirs. Scope was settled with them directly, so this runs
**checks 1–4 and nothing else**.

| # | Check | Why it is here |
|---|-------|----------------|
| 1 | exactly one pod logs `acquired leadership` | two leaders is split brain |
| 2 | the other two log `standing by` | a silent pod is not a standby |
| 3 | Lease `krateo-system/authn-cert-renewal` is held by **the pod that logged leadership** | reading the holder alone proves a Lease exists, not that the right process holds it |
| 4 | `identity annotation backfill done` on the **leader only** | the maintainers' highest-value check: a backfill racing three replicas is the plausible HA defect |
| 5 | `/serviceaccount/login` returns 200 from **every replica, individually** | a Service-level 200 proves one replica answered and says nothing about the other two |

The three log strings are quoted verbatim from the maintainers.

**Failover is deliberately not tested.** Deleting the leader and watching the Lease
move is already covered by their own kind e2e, which found the Lease moved with
exactly one re-issue and no duplicate CSR. Repeating it would roughly double the run
for no new information; they asked for the steady state instead.

## Running it

```sh
# The rig brings up authn + snowplow with no oasgen. AUTHN_EXTRA_SET is an additive
# passthrough, so the snowplow#911 fixture this rig was written for is unaffected.
CLUSTER=authn-ha AUTHN_VERSION=0.28.1 AUTHN_EXTRA_SET=replicaCount=3 \
  bash tests/experimental/snowplow-911/snowplow_rig.sh

CLUSTER=authn-ha bash tests/experimental/authn-ha/verify_ha.sh
```

Either 0.28.1 or 0.28.2 measures the same thing — 0.28.2 only changes the body of
`/serviceaccount/login` 403s and does not touch the leader-election code. Report
which one ran.

## Two things that cost a run before

**Accept any digest from the image index.** containerd reports the **index** digest
in `imageID` even on arm64, so asserting the per-arch manifest digest aborts the run
on a correct image. The 0.28.1 index also lists two `unknown/unknown` attestation
manifests, which are not runnable. `OK_DIGESTS` holds the whole acceptable set.

**Run only on a quiet host.** Every check here is a race. On a starved node a renewal
deadline slips whether or not the release has a defect, and the report would be a
false leader-election regression against someone else's code. A peer session nearly
shipped loosened probe timings to four charts off exactly that mistake: 11 MariaDB
restarts under load, 0 restarts in a quiet window, 2 more when load returned. Gate on
host **load average**, not container CPU — `docker stats` cannot see a host-side build
at all.

## The verdict logic is itself tested

`verdict_test.sh` replays the counting and matching against synthetic logs, no
cluster needed:

```sh
bash tests/experimental/authn-ha/verdict_test.sh
```

One healthy election must pass; six broken ones must fail. The case that justifies
the file is **"backfill ran on a follower"** — its counts are *identical* to healthy
(`leaders=1 standby=2 backfill=1`) and it must still fail, because only the
leader-match catches it. A count-only check passes it silently, and that is precisely
the defect check 4 exists to find.

This exists because a screen that reports "all checks pass" without proving it can
fail a known-bad case is a function that returns true — and this one decides what
gets reported upstream about someone else's release.
