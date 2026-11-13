#!/usr/bin/env python3
"""Is any RestDefinition's CRD stale because its oasHash advanced without a render?

oasgen-provider#634 / the 0.63.1 fix: on the Create path (taken when the generated
controller's Deployment is absent) the reconciler advanced `status.oasHash` WITHOUT having
rendered. Every later Observe then compares the live document against the already-advanced
hash, finds no drift, and the CRD keeps its old schema indefinitely while the
RestDefinition reports Ready. Every tag from 0.45.0 through 0.63.0 is affected.

This is invisible to `scripts/check_oas_drift.py`, which compares our VENDORED documents
against upstream -- a different question from whether the CRD matches the document.

HOW IT DETECTS WITHOUT GUESSING. `compositeOASDigest` is sha256 over the sorted
`<oasPath>=<sha256(document bytes)>\n` lines, and its inputs are exactly the path STRING
and the document bytes -- no oasgen version, no plumbing version, no RestDefinition spec,
no cluster identity. (Corroborated independently by observation before it was read from
source: definitions sharing a document share an oasHash while their resourceHash values
all differ, which is only possible if the spec is absent from the input.)

So the stored hash can simply be RECOMPUTED from the cluster's own ConfigMaps. A
disagreement means the stored value was written by something that did not render.

THE ALGORITHM VALIDATES ITSELF BY AGREEMENT. If the reimplementation below were wrong,
NOTHING would match -- so a run where some agree and some do not is a real finding, while
a run where none agree means this script is broken rather than the cluster. That
distinction is reported explicitly, because "0 agreements" and "0 mismatches" are very
different results and a naive check would print the second for both.

THE PATH STRING IS PART OF THE HASH, not just the bytes. A document moving between
ConfigMaps, namespaces, keys, or schemes changes the hash legitimately. The recomputation
uses each definition's own declared path, so that is handled -- but it is why you cannot
compare a hash against one computed from a file on disk.

Note also that `gen_configmaps.py` writes COMPACT json (`separators=(",", ":")`), so a
ConfigMap never matches the pretty-printed vendored file byte for byte. Compare against
the ConfigMap, never against openapi/*.json.

    KUBE_CONTEXT=<ctx> python3 -I scripts/latch_check.py

Exit 0 when every definition agrees, 1 on any mismatch, 2 when nothing could be checked.
"""
import hashlib
import json
import os
import subprocess
import sys

CTX = os.environ.get("KUBE_CONTEXT")
if not CTX and "--self-test" not in sys.argv:
    sys.exit("set KUBE_CONTEXT to the cluster to inspect (or pass --self-test)")


def kubectl(*args):
    """Exit 2, never 1, when the cluster cannot be ASKED.

    Found by running this script against a cluster that had just been stopped: the
    original `sys.exit("...")` exits with status 1, which is this script's code for
    LATCH FOUND. A caller reading only the exit status would have concluded a latch
    existed when the apiserver was merely unreachable -- a failed question reported as a
    negative answer, which is the failure this script exists to avoid.
    """
    r = subprocess.run(["kubectl", "--context", CTX, *args],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  CANNOT ASK the cluster: kubectl {' '.join(args)} failed:\n"
              f"    {r.stderr.strip()[:300]}\n"
              f"  This is NOT a result. Exit 2 = not measured.", file=sys.stderr)
        sys.exit(2)
    return r.stdout


def digest_for(paths, data):
    """compositeOASDigest: sha256 over sorted '<path>=<sha256(bytes)>\n' lines."""
    h = hashlib.sha256()
    for p in sorted(paths):
        if not p.startswith("configmap://"):
            return None, f"non-configmap oasPath {p!r} -- cannot recompute"
        ns, cm, key = p[len("configmap://"):].split("/", 2)
        body = data.get((ns, cm), {}).get(key)
        if body is None:
            return None, f"no ConfigMap {ns}/{cm} key {key}"
        h.update(f"{p}={hashlib.sha256(body.encode()).hexdigest()}\n".encode())
    return h.hexdigest(), None


def self_test():
    """Prove the comparison DISCRIMINATES, with no cluster and no writes.

    Testing this by patching a real RestDefinition's status on a live cluster was the
    obvious move and the wrong one -- it writes to a shared resource to learn something
    about arithmetic. Synthetic inputs settle it and can be re-run by anyone.
    """
    P = "configmap://krateo-system/x-openapi/x.json"
    data = {("krateo-system", "x-openapi"): {"x.json": '{"a":1}'}}
    want, err = digest_for([P], data)
    assert err is None and want, f"digest_for failed on a valid input: {err}"

    ok = True
    if want == "deadbeef" * 8:
        print("  SELF-TEST FAIL: a wrong stored value compared equal"); ok = False
    same, _ = digest_for([P], data)
    if same != want:
        print("  SELF-TEST FAIL: the same input gave two different digests"); ok = False
    other, _ = digest_for([P], {("krateo-system", "x-openapi"): {"x.json": '{"a":2}'}})
    if other == want:
        print("  SELF-TEST FAIL: different bytes gave the same digest"); ok = False
    moved, _ = digest_for(["configmap://other-ns/x-openapi/x.json"],
                          {("other-ns", "x-openapi"): {"x.json": '{"a":1}'}})
    if moved == want:
        print("  SELF-TEST FAIL: the PATH is not part of the digest, but it must be"); ok = False
    missing, err2 = digest_for([P], {})
    if missing is not None or not err2:
        print("  SELF-TEST FAIL: an unreadable document did not report as unreadable"); ok = False

    print("  self-test: identical input -> identical digest; different bytes -> different;")
    print("             different PATH -> different; unreadable document -> reported")
    print(f"  self-test: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 2


def main():
    rds = json.loads(kubectl("get", "restdefinition", "-A", "-o", "json"))["items"]
    cms = json.loads(kubectl("get", "configmap", "-A", "-o", "json"))["items"]
    data = {(c["metadata"]["namespace"], c["metadata"]["name"]): (c.get("data") or {})
            for c in cms}

    agree, mismatches, skipped = 0, [], []
    for r in rds:
        name = r["metadata"]["name"]
        path = (r.get("spec") or {}).get("oasPath")
        stored = (r.get("status") or {}).get("oasHash")
        if not path or not stored:
            skipped.append((name, "no oasPath or no stored oasHash yet"))
            continue
        want, why = digest_for([path], data)
        if want is None:
            skipped.append((name, why))
        elif want == stored:
            agree += 1
        else:
            mismatches.append((name, path, stored, want))

    print(f"  definitions          : {len(rds)}")
    print(f"  stored hash AGREES   : {agree}")
    print(f"  MISMATCH (latched)   : {len(mismatches)}")
    print(f"  not checkable        : {len(skipped)}")
    for n, why in skipped:
        print(f"      skip {n}: {why}")
    for n, p, s, w in mismatches:
        print(f"      LATCHED {n}\n        oasPath    {p}\n        stored     {s}\n"
              f"        recomputed {w}")

    if agree == 0 and not mismatches:
        print("\n  NOTHING WAS CHECKED. This is not 'no latch' -- it is no measurement.")
        return 2
    if agree == 0 and mismatches:
        print("\n  EVERY definition mismatched and NONE agreed. Suspect this script rather\n"
              "  than the cluster: with a correct algorithm some would agree. Do not report\n"
              "  a fleet-wide latch from this.")
        return 2
    if mismatches:
        print("\n  Remediation (0.63.1 notes), in this order -- the order matters:\n"
              "    1. confirm the generated controller's Deployment is Ready\n"
              "    2. patch status.oasHash to empty on the affected RestDefinition\n"
              "    3. the next Observe detects drift and regenerates\n"
              "  Patching before the Deployment is Ready re-forms the latch on the next\n"
              "  Create pass. Note also that an upgrade crossing a version boundary clears\n"
              "  a latch by itself, because generatorChanged returns true unconditionally\n"
              "  when generatorBuild is empty and is OR'd into the drift inputs.")
        return 1
    print("\n  No latch: every stored hash equals a fresh computation over the cluster's\n"
          "  own ConfigMaps. The agreements also validate the algorithm -- if the\n"
          "  reimplementation were wrong, none of them would have matched.")
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    sys.exit(main())
