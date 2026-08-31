#!/usr/bin/env python3
"""Detect drift between the vendored OpenAPI documents and what Aruba publishes today.

`openapi/CHECKSUMS.txt` pins what we vendored. It cannot notice that upstream has
moved on -- that is this script's job, and it is the difference between finding a
spec change ourselves and having a user find it for us.

Exit codes:
  0  every vendored document still matches upstream byte-for-byte
  1  at least one document drifted, was withdrawn, or is newly unreachable
  2  the check could not run (network failure on every document)

Nothing here writes to openapi/. Adopting a change stays a deliberate act: the
report tells you what moved, you re-vendor and re-generate, and the generated
RestDefinitions get re-validated on the way in.
"""

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

# The published docs site, which is what actually serves the documents. Note that
# https://api.arubacloud.com/openapi/<name> -- which this repo's docs used to cite --
# 301-redirects to the docs homepage and returns HTML for every name, so it silently
# "drifts" all 12 at once. Verified: this URL matches every vendored checksum today.
BASE = "https://arubacloud.github.io/api/openapi"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OASDIR = os.path.join(ROOT, "openapi")
TIMEOUT = 30


def vendored():
    """Filename -> sha256, read from the manifest rather than recomputed.

    Reading the manifest (not the files) means a tampered file that never had its
    checksum updated is caught by validate.py, and this script stays about upstream.
    """
    out = {}
    with open(os.path.join(OASDIR, "CHECKSUMS.txt")) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            digest, name = line.split(None, 1)
            out[name.strip()] = digest
    return out


def fetch(name):
    url = f"{BASE}/{name}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def summarise(body, name):
    """Best-effort 'what actually changed', so a drift report is actionable.

    A version bump and a silent in-place edit need very different responses, and a
    bare 'sha256 differs' does not distinguish them.
    """
    try:
        doc = json.loads(body)
    except Exception:
        return "upstream body is not valid JSON"
    ver = doc.get("info", {}).get("version")
    paths = len(doc.get("paths", {}) or {})
    local_path = os.path.join(OASDIR, name)
    try:
        with open(local_path, "rb") as fh:
            old = json.load(fh)
        old_ver = old.get("info", {}).get("version")
        old_paths = len(old.get("paths", {}) or {})
    except Exception:
        return f"info.version={ver}, {paths} paths"

    bits = []
    if ver != old_ver:
        bits.append(f"info.version {old_ver} -> {ver} (CRD version changes with it)")
    else:
        bits.append(f"info.version unchanged at {ver}")
    if paths != old_paths:
        bits.append(f"paths {old_paths} -> {paths}")
        added = sorted(set(doc.get("paths", {})) - set(old.get("paths", {})))
        removed = sorted(set(old.get("paths", {})) - set(doc.get("paths", {})))
        if added:
            bits.append("added: " + ", ".join(added[:5]) + (" ..." if len(added) > 5 else ""))
        if removed:
            bits.append("REMOVED: " + ", ".join(removed[:5]) + (" ..." if len(removed) > 5 else ""))
    else:
        bits.append(f"{paths} paths, same set")
    return "; ".join(bits)


def main():
    pinned = vendored()
    drifted, unreachable, ok = [], [], []

    for name in sorted(pinned):
        try:
            body = fetch(name)
        except urllib.error.HTTPError as exc:
            unreachable.append((name, f"HTTP {exc.code}"))
            continue
        except Exception as exc:  # DNS, TLS, timeout
            unreachable.append((name, type(exc).__name__))
            continue

        digest = hashlib.sha256(body).hexdigest()
        if digest == pinned[name]:
            ok.append(name)
        else:
            drifted.append((name, digest, summarise(body, name)))

    print(f"upstream: {BASE}")
    print(f"unchanged: {len(ok)}/{len(pinned)}")

    for name, digest, why in drifted:
        print(f"\nDRIFT  {name}")
        print(f"       vendored {pinned[name][:16]}...  upstream {digest[:16]}...")
        print(f"       {why}")

    for name, why in unreachable:
        print(f"\nUNREACHABLE  {name}: {why}")

    # Every document failing to download is a network problem, not upstream drift.
    # Reporting it as drift would cry wolf on every flaky run.
    if unreachable and not ok and not drifted:
        print("\nEvery document was unreachable -- treating as an infrastructure failure, "
              "not as drift.")
        return 2

    if drifted or unreachable:
        print(
            "\nNext: re-download the affected documents, refresh openapi/CHECKSUMS.txt, "
            "re-run scripts/generate_restdefinitions.py and scripts/validate.py, and "
            "review the diff -- an info.version change renames the generated CRD version, "
            "which is a breaking change for existing CRs."
        )
        return 1

    print("\nNo drift.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
