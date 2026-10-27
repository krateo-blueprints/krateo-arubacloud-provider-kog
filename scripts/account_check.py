#!/usr/bin/env python3
"""Is anything this repo can create still alive on the account?

Every chain already checks its own residue, and that has never been the weak point.
The weak point was the spot-check: for weeks, "account verified clean" in this
repository's notes meant a hand-written list of collections that happened to omit
`Aruba.Schedule/backupPolicies` — where a BackupPolicy had been stuck in `Deleting`
since 2026-09-04. Nothing was billing, which was luck rather than diligence.

So the list is not hand-written any more. It is derived from the RestDefinitions, so a
collection cannot be forgotten unless the resource does not exist here at all.

Only project-level collections can be swept: a child collection needs its parent's id
in the path, and if the parent is gone the child cannot outlive it. Those are listed as
skipped rather than silently dropped.

    KUBE_CONTEXT=kind-aruba-110 python3 scripts/account_check.py [--all]

Without --all, only resources whose name looks like test fixture output (ga-*, tm-*)
are reported as leftovers; anything else is listed as pre-existing. Exit status is 1 if
a leftover or an unreadable collection is found, so CI or a wrapper can gate on it.
"""
import base64
import glob
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

import yaml

CONTEXT = os.environ.get("KUBE_CONTEXT", "kind-aruba-110")
NS = os.environ.get("NS", "default")
API = os.environ.get("ARUBA_API", "https://api.arubacloud.com")
PROJECT = os.environ.get("PROJECT", "69a55c8d5e6f0e14f14093ff")
FIXTURE = re.compile(r"^(ga|tm)-")


def token():
    r = subprocess.run(["kubectl", "--context", CONTEXT, "get", "secret",
                        "arubacloud-token", "-n", NS, "-o",
                        "jsonpath={.data.token}"], capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise SystemExit("no token in the ESO-managed Secret")
    return base64.b64decode(r.stdout).decode()


def api(path, tok):
    req = urllib.request.Request(
        f"{API}{path}?api-version=1.0",
        headers={"Authorization": f"Bearer {tok}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"  transport error on {path}: {type(e).__name__}", file=sys.stderr)
        return 0, None


def collections():
    """Project-level findby paths, read from the RestDefinitions."""
    sweepable, skipped = {}, {}
    for f in sorted(glob.glob("restdefinitions/*/*.yaml")):
        res = yaml.safe_load(open(f))["spec"]["resource"]
        for v in res.get("verbsDescription", []):
            if v.get("action") != "findby":
                continue
            path = v["path"]
            params = set(re.findall(r"\{(\w+)\}", path))
            if params - {"projectId"}:
                skipped[res["kind"]] = path      # needs a parent id
            else:
                sweepable[res["kind"]] = path.replace("{projectId}", PROJECT)
    return sweepable, skipped


def main():
    show_all = "--all" in sys.argv
    tok = token()
    sweepable, skipped = collections()
    print(f"sweeping {len(sweepable)} project-level collections "
          f"({len(skipped)} are child collections, listed at the end)\n")

    leftovers, unreadable, preexisting = [], [], []
    for kind, path in sorted(sweepable.items(), key=lambda kv: kv[1]):
        code, body = api(path, tok)
        if code != 200 or not isinstance(body, dict):
            unreadable.append((kind, path, code))
            print(f"  {kind:24} UNREADABLE (HTTP {code})")
            continue
        items = body.get("values") or body.get("value") or []
        mine = [v for v in items
                if FIXTURE.match(((v.get("metadata") or {}).get("name")
                                  or v.get("name") or ""))]
        others = [v for v in items if v not in mine]
        for v in mine:
            md = v.get("metadata") or v
            leftovers.append((kind, md.get("name"), md.get("id"),
                              (v.get("status") or {}).get("state")))
        for v in others:
            md = v.get("metadata") or v
            preexisting.append((kind, md.get("name"),
                                (v.get("status") or {}).get("state")))
        flag = "  <-- LEFTOVER" if mine else ""
        if items or show_all:
            print(f"  {kind:24} {len(items)} item(s){flag}")

    print()
    if leftovers:
        print("LEFTOVERS (fixture-named resources still present):")
        for kind, name, rid, state in leftovers:
            print(f"  {kind}/{name}  id={rid}  state={state}")
    else:
        print("no fixture-named resources remain in any sweepable collection")

    if preexisting:
        print("\npre-existing (not created by these tests):")
        for kind, name, state in preexisting:
            print(f"  {kind}/{name}  state={state}")

    if unreadable:
        print("\nUNREADABLE — these prove nothing either way:")
        for kind, path, code in unreadable:
            print(f"  {kind}  HTTP {code}  {path}")

    if skipped:
        print(f"\nchild collections, not sweepable without a live parent "
              f"({len(skipped)}): {', '.join(sorted(skipped))}")

    return 1 if (leftovers or unreadable) else 0


if __name__ == "__main__":
    sys.exit(main())
