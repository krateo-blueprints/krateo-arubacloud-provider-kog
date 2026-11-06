#!/usr/bin/env python3
"""Measure whether the findby envelope's `total` can disagree with `len(values)`.

Two different questions, and only the second is a defect:

  CONTROL  does `total` exceed len(values) when a small `limit` IS asked for?
           If not, this probe cannot detect truncation and its "no truncation"
           below would be an unproven zero rather than a measurement.
  SUBJECT  does `total` exceed len(values) when NO limit is asked for?
           That is what RDC's findby sends. If they disagree there, observe sees
           a truncated list, findby reports not-found for an object that exists,
           and an unmatched listing is treated as absence -- which drives a
           create. See docs/oasgen-provider-evolution.md B5 and
           oasgen-provider#485.

The answer measured on 2026-11-06 was: default page 50, `limit` honoured
DOWNWARD ONLY (limit=2000 still returns 50). It only shows up on a collection
with more than 50 objects; every resource collection in the test account holds
at most 6, so `--scan` exists to find one that is big enough. In that account
only `Aruba.Audit/events` qualifies, with 1175.

This is READ-ONLY: GET only, nothing created, nothing billed.

Usage:
    PROJECT=<id> scripts/paging_probe.py              # measure Aruba.Audit/events
    PROJECT=<id> scripts/paging_probe.py --scan       # find populated collections
    PROJECT=<id> scripts/paging_probe.py --path /projects/.../vpcs

The token is read from the ESO-managed Secret in the cluster, never from argv or
a file. KUBE_CONTEXT selects the cluster (default kind-aruba-110).
"""
import base64
import glob
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from required_env import required  # account-specific ids; see scripts/required_env.py

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
CONTEXT = os.environ.get("KUBE_CONTEXT", "kind-aruba-110")
NS = os.environ.get("NS", "default")
API = os.environ.get("ARUBA_API", "https://api.arubacloud.com")
PROJECT = required("PROJECT")

# The one collection in the test account large enough to answer the question.
DEFAULT_PATH = f"/projects/{PROJECT}/providers/Aruba.Audit/events"


def token():
    r = subprocess.run(["kubectl", "--context", CONTEXT, "get", "secret",
                        "arubacloud-token", "-n", NS,
                        "-o", "jsonpath={.data.token}"],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise SystemExit(
            f"no token in the ESO-managed Secret on {CONTEXT}. "
            f"Is the cluster up and is the ExternalSecret synced?"
        )
    return base64.b64decode(r.stdout).decode()


TOK = token()


def get(path, **q):
    q.setdefault("api-version", "1.0")
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    req = urllib.request.Request(f"{API}{path}?{qs}", method="GET",
                                 headers={"Authorization": f"Bearer {TOK}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:                                  # noqa: BLE001
        print(f"    {type(e).__name__}: {e}", file=sys.stderr)
        return 0, None


def collection_paths():
    """Every collection GET in the vendor documents, as a real path.

    A collection, not an item: no `{param}` after the provider segment. `limit`
    in the query parameters is what marks it as a list endpoint.
    """
    seen = []
    for f in sorted(glob.glob(os.path.join(ROOT, "openapi", "*.json"))):
        with open(f) as fh:
            doc = json.load(fh)
        for p, ops in (doc.get("paths") or {}).items():
            g = ops.get("get")
            if not g:
                continue
            if "limit" not in [x.get("name") for x in g.get("parameters", [])]:
                continue
            tail = p.split("/providers/")[-1] if "/providers/" in p else p
            if "{" in tail:
                continue
            real = p.replace("{projectId}", PROJECT)
            if real not in [r for r, _ in seen]:
                seen.append((real, tail))
    return seen


def scan():
    rows = []
    for real, tail in collection_paths():
        code, b = get(real)
        total = b.get("total") if isinstance(b, dict) else None
        vals = len(b.get("values") or []) if isinstance(b, dict) else None
        rows.append((tail, code, total, vals))
    rows.sort(key=lambda r: (-(r[2] if r[2] is not None else -1), r[0]))

    print(f"{'collection':50} {'HTTP':5} {'total':>6} {'len':>5}")
    print("-" * 70)
    for tail, code, total, vals in rows:
        print(f"{tail[:50]:50} {code:<5} {str(total):>6} {str(vals):>5}")

    big = [r for r in rows if (r[2] or 0) > 50]
    print(f"\ncollections with more than 50 objects (the only ones that can show "
          f"truncation): {len(big)}")
    for tail, _, total, _ in big:
        print(f"  {tail}  total={total}")
    if not big:
        print("  none -- this account is too small to answer the question. "
              "A 'no truncation'\n  result from --measure here would be an "
              "unproven zero, not a clean bill.")
    return 0


def measure(path):
    print(f"GET {path.replace(PROJECT, '<PROJECT_ID>')}\n")
    print(f"{'request':14} {'HTTP':5} {'total':>6} {'len':>5} {'next':>6}")
    print("-" * 42)
    rows = {}
    for label, q in [("(no params)", {}), ("limit=1", {"limit": 1}),
                     ("limit=10", {"limit": 10}), ("limit=50", {"limit": 50}),
                     ("limit=100", {"limit": 100}), ("limit=500", {"limit": 500}),
                     ("limit=2000", {"limit": 2000})]:
        code, b = get(path, **q)
        if not isinstance(b, dict):
            print(f"{label:14} {code:<5}  (no body)")
            continue
        total, n = b.get("total"), len(b.get("values") or [])
        rows[label] = (total, n)
        print(f"{label:14} {code:<5} {str(total):>6} {n:>5} "
              f"{str(bool(b.get('next'))):>6}")

    print()
    ctl = rows.get("limit=1")
    sub = rows.get("(no params)")
    if not ctl or ctl[0] is None:
        print("CONTROL did not run: the limit=1 request returned no usable body.")
        return 1
    if not (ctl[0] > ctl[1]):
        print(f"CONTROL FAILED: with limit=1, total={ctl[0]} and len={ctl[1]} do not "
              f"disagree.\nThis collection is too small to show truncation, so the "
              f"subject result below\nproves nothing. Run --scan for a bigger one.")
        return 1
    print(f"CONTROL ok: with limit=1, total={ctl[0]} > len={ctl[1]}, so this probe "
          f"CAN see truncation.")

    if sub and sub[0] is not None and sub[0] > sub[1]:
        print(f"\nTRUNCATED BY DEFAULT: an unparameterised GET on a {sub[0]}-item "
              f"collection\nreturned {sub[1]} items. A consumer that reads `values` "
              f"and stops sees a partial\nlist with nothing marking it partial.")
        caps = {lab: v for lab, v in rows.items()
                if lab.startswith("limit=") and v[1] == sub[1]
                and int(lab.split("=")[1]) > sub[1]}
        if caps:
            print(f"And the page size is a HARD CAP: {', '.join(sorted(caps))} all "
                  f"returned {sub[1]}.\nThere is no request that returns item "
                  f"{sub[1] + 1}.")
        return 1

    print("\nNo truncation without an explicit limit, and the control above proves "
          "that\nwould have been visible. At this collection's size findby reads a "
          "complete list.")
    return 0


def main(argv):
    if "--scan" in argv:
        return scan()
    path = DEFAULT_PATH
    if "--path" in argv:
        i = argv.index("--path")
        if i + 1 >= len(argv):
            raise SystemExit("--path needs a value")
        path = argv[i + 1]
    return measure(path)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
