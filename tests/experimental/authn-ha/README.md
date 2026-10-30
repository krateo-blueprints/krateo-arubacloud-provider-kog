---
type: Runbook
title: authn 0.28.1 HA verification
description: Leader-election checks for authn 0.28.1 at replicaCount=3, scoped by the authn maintainers on authn#45, with the verdict logic tested against six broken elections.
tags: [authn, kog, experimental]
timestamp: 2026-10-29T00:00:00Z
---

# authn 0.28.1 HA verification

> [!IMPORTANT]
> **THIS HARNESS WAS NEVER RUN.** It is complete and its verdict logic is tested
> against 24 cases, but no cluster has executed it and it has produced **no result**
> about authn 0.28.1. Recorded here so nobody reads the detail below as evidence.
>
> The authn session withdrew the request on 2026-10-30: their own kind e2e for 0.28.1
> already covered one `acquired leadership`, two `standing by`, the backfill on the
> leader only, **and** the failover this harness deliberately skips. An independent
> cross-check would have been worth having, but not at the price of quiet windows on
> a host that produced two in fourteen hours while billable CloudServer work needed
> them. They also declined to run it themselves on the same contended host, on the
> grounds that a leader-election timing measurement there proves nothing — which is
> the argument this repo had been making about its own measurements.
>
> It is kept rather than deleted because building it produced findings that stand on
> their own: the prefix-overlap bug where `"identity annotation backfill"` is a prefix
> of `"…done"`, and the graceful-release false positive where counting raw
> `standing by` lines reports a normal `ReleaseOnCancel` shutdown as a defect. Both
> were corrected before anything was reported upstream. If the cross-check is ever
> wanted, it runs as written.

Requested by the `authn` session for **authn#45**: they need these checks on a
cluster that is not theirs. Scope was settled with them directly, so this runs
**checks 1–4 and nothing else**.

| # | Check | Why it is here |
|---|-------|----------------|
| 1 | exactly one pod logs `acquired leadership` | two leaders is split brain |
| 2 | the other two log `standing by` | a silent pod is not a standby |
| 3 | Lease `krateo-system/authn-cert-renewal` is held by **the pod that logged leadership** | reading the holder alone proves a Lease exists, not that the right process holds it |
| 4 | the backfill outcome, split three ways | the maintainers' highest-value check: a backfill racing three replicas is the plausible HA defect |
| 5 | `/serviceaccount/login` returns 200 from **every replica, individually** | a Service-level 200 proves one replica answered and says nothing about the other two |

The log strings are quoted verbatim from the maintainers.

### Check 4's three outcomes

The maintainers read `go/authn/main.go` (the `ownIdentity` closure) rather than
answering from memory, and `identitybackfill.Run` always produces exactly one of two
lines — **there is no legitimate silence**:

| Observed | Verdict |
|----------|---------|
| `identity annotation backfill done`, leader only | **pass**. Success even with nothing to do: an empty cluster logs `scanned=0`, not silence |
| msg `identity annotation backfill` with an `error` field | **fail**, but a *backfill* failure — a different finding from an HA defect. The error is quoted in the output |
| neither line on any pod | **fail** — but re-read once after 30s first, since it runs seconds after the leader's `Ensure` and may simply be early |

**The error line's msg is a prefix of the success line's**, so a plain
`grep -c 'identity annotation backfill'` counts `backfill done` as an error. Demonstrated:
that grep returns **2** on a log holding one success and one error.

Matched with `exact_msg`, which requires the message field to equal the string
exactly — the maintainers' suggestion, and better than `grep -v`. **The closing quote
is mandatory**: making it optional reproduced the very prefix bug it exists to avoid,
since on `message="identity annotation backfill done"` the boundary then matched the
space before `done`.

Because a matcher that finds nothing is indistinguishable from a healthy backfill, the
exact matcher is **cross-checked against the cruder substring one on every pod**, and a
disagreement is reported as "treat this pod's backfill result as UNREAD" rather than
resolved in favour of either.

### The log format is asserted, not assumed

The maintainers established from source that 0.28.1 emits **one JSON object per line
with a `message` field** — `main.go:117` builds the logger with
`zerolog.New(os.Stdout)` and nothing in the module sets `zerolog.MessageFieldName`, so
zerolog's default applies. Never `msg`, never logfmt.

`assert_log_format` checks that at runtime on each pod rather than trusting it, because
the whole value of knowing the format is that a violation becomes a *loud finding*
instead of a silent mismatch. If a later release swaps the logger, it says so in one
line; without it, every count would quietly read as "the line never appeared" — which
is the same output as a real defect.

Per the maintainers, with the field name settled, `message` matching nothing on a
0.28.1 pod is a **genuine absence**, not a field-name problem. So a cross-check
disagreement now means a line carrying that text does not have the shape the source
says it must, which is itself worth reporting to them.

### Two counting rules from the maintainers

**Count per pod and per term, not globally.** The closure runs once per leadership
term, so a pod that loses the Lease and wins it back logs `acquired leadership` and
`done` again from the same pod — correct behaviour. Counting *pods* keeps a
re-acquisition from reading as a second leader; the per-pod counts are printed so the
extra term stays visible.

**`standing by` is once per observed leader change, not per attempt.** It is
client-go's `OnNewLeader` callback, which fires only when the observed
`holderIdentity` changes, and the elector is reused across re-campaigns — so a steady
follower logs it exactly once, naming the leader. More than one is therefore a real
event, and the script classifies it from the `leader=` values rather than printing a
count a reader would have to guess at:

| `leader=` values | Meaning |
|---|---|
| distinct non-empty | real leader changes; one should match a pod's term |
| one empty (`leader=""`) | graceful release — `ReleaseOnCancel` empties `holderIdentity`; authn will suppress it |
| the same non-empty value repeated | a defect on authn's side, reported as such |

The empty value is excluded from the occurrence count, not just the distinct count.
`verdict_test.sh` caught that: counting raw `standing by` lines made a normal graceful
shutdown come out as "the same leader re-logged", which would have reported a false
defect against someone else's release.

**A restarted container's logs are gone.** Any pod with `restartCount > 0` would have
every count taken from a partial record and read as "the line never appeared". The
script reports the restart count and reads `--previous` for those pods as well.

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

Twenty-four cases. The summary counts the cases it ran rather than naming a number,
so it cannot claim coverage it does not have — it was left asserting 7 while running
11 once already.

Four groups: the election verdict (4 pass, 7 fail), `exact_msg` against the log shapes,
`leader_values` classification, and `assert_log_format` against every way the logger
could change — a logfmt swap, a `msg`-keyed rename, unstructured klog text, and an
empty log, which must stay *silent* because absence is not a format change.

The case that justifies the file is **"backfill ran on a follower"** — its counts are
*identical* to healthy (`leaders=1 standby=2 backfill=1`) and it must still fail,
because only the leader-match catches it. A count-only check passes it silently, and
that is precisely the defect check 4 exists to find.

The two cases that must **pass** are the ones the substring trap would have broken:
an empty cluster logging `done` with `scanned=0`, and a leader that re-acquired its
term and logged both lines twice from the same pod.

This exists because a screen that reports "all checks pass" without proving it can
fail a known-bad case is a function that returns true — and this one decides what
gets reported upstream about someone else's release.
