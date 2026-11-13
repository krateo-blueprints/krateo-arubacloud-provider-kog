# Does a truncated findby duplicate a resource at first contact?

oasgen-provider#486. `findby` never consults the paginator — `restclient.go:488` returns
early to `CallFindBySingle` whenever `Pagination` is nil — so the three-verdict machinery
that exists to stop a truncated scan being read as absence is **bypassed**. The maintainer
traced the consequence and cannot run it: his tests supply pagination config deliberately,
so they never exercise the no-pagination path. This runs it.

## What is actually claimed, and what was measured first

| claim | status |
|---|---|
| findby bypasses the paginator entirely | read from the code by the maintainer |
| a truncated scan reports absence, and absence drives a create | **traced, not observed** — this probe |
| the hazard is first-contact only, not every reconcile | **measured here 2026-11-07**: 600s with a blinded findby on an addressable Vpc gave no event, no error, `Ready=Available`, no findby call logged. Once `status.metadata.id` exists, observe uses `get` |
| all 36 of our kinds declare a `get` verb | measured 2026-11-12: 36/36, so none is exposed in steady state |
| Aruba's page is 50 and `limit` is honoured downward only | measured 2026-11-06, `scripts/paging_probe.py` |
| the largest managed collection here is 5 of 50 | measured 2026-11-12 — so this is theoretical in **this account** and immediate for a tenant with 60 VPCs |

## Why `limit=1` and not 51 resources

The hazard needs *the item is not on the page findby read* — a small page, not a big
collection. Verified against the client by the maintainer: `total` appears **zero** times
in the findby path, `CallFindBySingle` never reads the envelope or the request parameters,
and `findItemInList` only checks `len(items) == 0`. Nothing upstream can distinguish a
list short because we asked for 1 from one short because the vendor capped at 50.

## Why this needs its own cluster

`configurationFields` are **immutable** once a CRD is generated. A cluster that has seen
the fleet `vpc.yaml` can never accept `limit` afterwards, so the experimental definition
has to be the only Vpc definition the cluster ever sees.

## Running it

```sh
CLUSTER_PROVIDER=gke GKE_ZONE=europe-west3-b OASGEN_VERSION=0.62.0 \
  CLUSTER=aruba-b5-arm RESTDEFS_DIR=tests/experimental/findby-pagination/ \
  CHAIN=tests/ga/chains/network-adoption.yaml SKIP_CHAIN=1 KEEP=1 \
  GA_PROJECT=<project> bash scripts/pin_verify.sh

KUBE_CONTEXT=<ctx> PROJECT=<project> python3 -I tests/experimental/findby-pagination/probe.py
```

`KEEP=1` is deliberate here even though the GKE default is now to delete: the probe runs
after `pin_verify` exits, so the cluster has to outlive it. **Delete it yourself
afterwards** and verify with `describe` returning 404 — not with a piped `$?`.

## The two arms

One must fire and one must not, on the same cluster with the same Configuration:

- **OFF-PAGE** — a CR at first contact with a VPC that exists but is not on the 1-item
  page. Expect absence inferred, create attempted, and a **second** VPC with the same name.
- **ON-PAGE** — the same thing against the VPC that *is* on that page. Expect clean
  adoption, no duplicate.

An arm that only fires is not evidence. If the control also duplicates, the arms do not
discriminate and the subject result means nothing.

## What the probe refuses to do

- It **asserts the narrowed `limit` is stored**, reading it back from the Configuration.
  A `kubectl patch` of a pruned field exits 0 and stores nothing (measured 2026-11-07 with
  `findby.offset` on the fleet definition), so without this the probe would run an
  unnarrowed findby and report "no duplicate" as though that meant something.
- It **measures which VPC the 1-item page returns** rather than assuming an ordering, and
  picks the arms from that.
- It reports "the arm could not be set up" separately from "the arm did not fire". An
  experiment that failed to construct is not a negative result.

## Cleanup

Every VPC it creates is prefixed `b5probe`, including any duplicate the controller makes,
and all of them are deleted with a residue check. The pre-existing default VPC is never
touched. VPCs do not bill.

## Result, measured 2026-11-13

Released `0.62.0` images, disposable GKE, `limit=2` asserted present in the stored
Configuration, two probe VPCs, first contact:

```
Vpc CRD served version: 'v1-0-0'     VpcConfiguration: 'v1alpha1'
GET vpcs?limit=2 -> ['cloudburst-default', 'b5probe-a']
  OFF the page: b5probe-b        ON the page: b5probe-a

ARM OFF-PAGE  AbsenceInferredFromUnmatchedListing x2
              "treated as absent because its findby listed successfully and no item
               matched", and "2 times for the same spec generation"
              CannotCreateExternalResource | unexpected status: 400
              upstream objects named b5probe-b: 1   -> NO duplicate
ARM ON-PAGE   absence events: 0    upstream objects: 1   -> adopted cleanly
```

**The mechanism is confirmed; the traced consequence is not.** A truncated findby *does*
infer absence, and the inference *does* drive a create attempt — but Aruba answered **400**,
because the resource it was being asked to create already exists.

So on this vendor **#486 is a convergence failure, not a duplicate-resource bug.** The CR
can never become Ready: findby cannot see the resource, the create is refused, and the
cycle repeats every reconcile. The resource exists and is permanently unmanageable, and the
create endpoint is hit once per poll.

Whether it is worse than that is **vendor-dependent**: a vendor that permits duplicate
names, or that mints its own identifier and ignores the name, would get the duplicate the
issue describes. One vendor cannot settle that. What holds for all of them is *absence
inferred, create attempted, outcome decided by whether the vendor refuses*.

### What it cost to get one observation

Four runs; three died in this probe rather than in the subject:

| run | died of | caught by |
|---|---|---|
| 1 | string `"1"` for an integer field, then `sys.exit` leaked two VPCs | the stored-`limit` read-back |
| 2 | dirty fixture from run 1's leak | `POST` returning 400 on an existing name |
| 3 | hardcoded `v1alpha1`; the resource is served at `v1-0-0` | `kubectl apply` refusing the kind |
| 4 | — | both arms discriminated |

Every failure was loud. None produced a plausible number, which is the only reason a false
result never went upstream. The arm's *expectation* was also wrong — it asserted a
duplicate, so run 4 reported a real observation as a failure — and that is corrected: the
pass condition is now absence-inferred plus create-attempted, with the duplicate recorded
as a vendor-dependent outcome.
