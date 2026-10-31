#!/usr/bin/env python3
"""Assert each generated <Kind>Configuration CRD exposes EXACTLY the auth method its
OAS document declares, with exactly that method's field set.

From the openstack-nova-operator-kog session, and the load-bearing idea is theirs:
the comparison must be EQUALITY, not membership.

    got == ["apiKey"]        <- a completeness assertion
    "apiKey" in got          <- passes on a CRD that ALSO carries bearer

Membership cannot see the interesting failure, which is a document generating both --
and nor can a rejection test. This repo already has rejection evidence: a published
0.2.0 chart sent `authentication.bearer` to three resources whose document declares
apiKey and the API server refused it. That proves the wrong thing is refused; this
proves there is nothing wrong to reach for. They are complementary and neither
subsumes the other.

The expectation is DERIVED from the same OAS the generator reads, never hardcoded, so
it cannot drift from the documents: 8 of the 13 declare `apiKey` with `in: header`
and 5 declare `http/bearer`, and if that split changes this check changes with it.

Reads only. Needs no vendor API and no credentials.
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from providers import CONFIGMAP_PROVIDERS  # noqa: E402

GROUP = "arubacloud.ogen.krateo.io"
# What each scheme type generates in the Configuration CRD. Measured off the samples
# that are server-dry-run against these CRDs on every PR, so it is not a guess.
FIELDS = {
    "apiKey": ["header", "tokenRef", "valuePrefix"],
    "bearer": ["tokenRef"],
}

# Negative-test hook, so verifying the FAILURE path never means editing this file.
# Three times tonight a planted edit plus its cleanup went into one shell invocation,
# the slow part timed out, and the planted state was left behind -- once in a script a
# live run was reading. An env override makes the wrong-expectation test a flag rather
# than a mutation:
#     ASSERT_AUTH_EXPECT_OVERRIDE='{"bearer":["tokenRef","bogus"]}'
# It is read only when set, so it cannot affect CI, and a bad value fails loudly
# rather than silently disabling the check.
_ov = os.environ.get("ASSERT_AUTH_EXPECT_OVERRIDE")
if _ov:
    try:
        FIELDS = {**FIELDS, **json.loads(_ov)}
    except Exception as e:
        raise SystemExit(f"ASSERT_AUTH_EXPECT_OVERRIDE is not valid JSON: {e}")
    print(f"  NOTE: expectation overridden for testing -> {FIELDS}")


def declared_method(doc):
    """The single auth method this document declares, per its securitySchemes."""
    schemes = (doc.get("components") or {}).get("securitySchemes") or {}
    methods = set()
    for sc in schemes.values():
        if sc.get("type") == "apiKey" and sc.get("in") == "header":
            methods.add("apiKey")
        elif sc.get("type") == "http" and sc.get("scheme") == "bearer":
            methods.add("bearer")
    if len(methods) != 1:
        return None, f"document declares {sorted(methods) or 'no'} usable scheme(s)"
    return methods.pop(), None


def kubectl(*args):
    return subprocess.run(["kubectl", *args], capture_output=True, text=True)


def main():
    ctx = os.environ.get("KUBE_CONTEXT")
    pre = ["--context", ctx] if ctx else []
    fail = checked = 0

    # Resource kind -> its provider, from the RestDefinitions themselves rather than a
    # filename convention: a RestDefinition's kind is what the CRD is named after.
    import glob
    import yaml
    by_provider = {}
    for f in sorted(glob.glob(os.path.join(ROOT, "restdefinitions", "**", "*.yaml"),
                              recursive=True)):
        d = yaml.safe_load(open(f)) or {}
        res = (d.get("spec") or {}).get("resource") or {}
        kind = res.get("kind")
        prov = os.path.basename(os.path.dirname(f))
        if kind:
            by_provider.setdefault(prov, []).append(kind)

    # ONE wait for the whole set, not one per CRD.
    #
    # Gate on Established rather than on the RestDefinition's Ready: nova measured
    # Ready going true BEFORE the CRDs exist, twice -- Ready at t+148s with CRDs
    # arriving t+148-149s, and Ready at t+177s with CRDs at t+177-179s
    # (oasgen-provider#314). So some wait is needed. But they arrive within ~1-2s of
    # each other, so waiting per CRD waits for the same event 36 times: at 120s
    # each that is up to 72 minutes when something is genuinely absent, which is
    # how a useful check becomes one nobody leaves enabled.
    #
    # MEASURED 2026-10-31, because the 72 minutes above is a COMPUTED WORST CASE
    # and was being read as an observation -- including by me, when I quoted it to
    # the nova session as a reason to batch. In CI this batch wait plus all 36
    # assertions takes 8 SECONDS. But 8s is not a measurement of establishment
    # latency: the preceding "every RestDefinition reaches Ready" step takes 2m46s
    # and establishment happens during it, so this wait finds nothing left to wait
    # for. The defensible claim is that the batch form costs ~0 when establishment
    # has already happened and bounds the worst case when it has not. Anyone
    # quoting "8s instead of 72 minutes" would be comparing a measurement against
    # a hypothetical.
    all_crds = [f"{k.lower()}configurations.{GROUP}"
                for ks in by_provider.values() for k in ks]
    w = kubectl(*pre, "wait", "--for=condition=Established",
                *[f"crd/{c}" for c in all_crds], "--timeout=180s")
    if w.returncode != 0:
        # Not fatal on its own: a CRD that is absent is reported per-resource below,
        # which says WHICH one rather than that the batch failed.
        print(f"  note: not all CRDs Established within 180s "
              f"({(w.stderr or '').strip()[:100]})")

    for prov, kinds in sorted(by_provider.items()):
        fn = CONFIGMAP_PROVIDERS.get(prov)
        if not fn:
            print(f"  SKIP {prov}: no document in CONFIGMAP_PROVIDERS")
            continue
        doc = json.load(open(os.path.join(ROOT, "openapi", fn)))
        want, why = declared_method(doc)
        if not want:
            print(f"  SKIP {prov} ({fn}): {why}")
            continue
        for kind in sorted(kinds):
            plural = f"{kind.lower()}configurations"
            crd = f"{plural}.{GROUP}"
            r = kubectl(*pre, "get", "crd", crd, "-o", "json")
            if r.returncode != 0:
                # Absent is NOT the same as wrong, and saying so matters: a CRD that
                # has not been Established yet reads identically to one that is broken.
                print(f"  MISS {crd}: not present ({(r.stderr or '').strip()[:70]})")
                fail += 1
                continue
            try:
                v = json.loads(r.stdout)["spec"]["versions"][0]
                auth = (v["schema"]["openAPIV3Schema"]["properties"]["spec"]
                        ["properties"]["authentication"]["properties"])
            except (KeyError, IndexError, json.JSONDecodeError) as e:
                print(f"  FAIL {crd}: no authentication schema to read ({e})")
                fail += 1
                continue
            checked += 1
            got = sorted(auth)
            if got != [want]:
                print(f"  FAIL {crd}: expected EXACTLY ['{want}'] (its document "
                      f"declares {want}), got {got}")
                fail += 1
                continue
            gotf = sorted((auth[want] or {}).get("properties") or {})
            if gotf != FIELDS[want]:
                print(f"  FAIL {crd}: {want} fields {gotf}, expected {FIELDS[want]}")
                fail += 1
                continue
            print(f"  OK   {crd:52s} methods={got} fields={gotf}")

    print(f"\n{checked} Configuration CRD(s) asserted, {fail} failure(s)")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
