#!/usr/bin/env python3
"""Does a TRUNCATED findby make the controller create a duplicate at first contact?

oasgen-provider#486. The maintainer traced it and cannot run it: his tests supply
pagination config deliberately, so they never exercise the no-pagination path that
`restclient.go:488` takes whenever `Pagination` is nil. This runs it.

TWO ARMS ON ONE CLUSTER, one of which must fire and one of which must not:

  OFF-PAGE   a CR at first contact with a VPC that EXISTS upstream but is not on the
             1-item page findby reads. Expect: absence inferred, create attempted, a
             SECOND VPC with the same name -- i.e. a duplicate.
  ON-PAGE    a CR at first contact with the VPC that IS on that page. Expect: adopted,
             no duplicate, no absence event. Same kind, same Configuration, same cluster.

An arm that only fires is not evidence; a pair where one fires and one does not is.

PREREQUISITES: a cluster built by scripts/pin_verify.sh with
RESTDEFS_DIR=tests/experimental/findby-pagination/ and SKIP_CHAIN=1, so the ONLY Vpc
definition the cluster has ever seen is the experimental one. configurationFields are
immutable once a CRD is generated, so a cluster that saw the fleet definition first can
never accept `limit` -- which is the whole reason this needs its own cluster.

NOTHING BILLS. VPCs are free. Every VPC this creates is named with PREFIX and deleted at
the end, including any duplicate the controller makes; the existing default VPC is never
touched.
"""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request

API = "https://api.arubacloud.com"
PREFIX = "b5probe"
PROJECT = os.environ["PROJECT"]
CTX = os.environ["KUBE_CONTEXT"]
NS = "default"
GROUP = "arubacloud.ogen.krateo.io"
LOCATION = os.environ.get("LOCATION", "ITBG-Bergamo")
VPCS = f"/projects/{PROJECT}/providers/Aruba.Network/vpcs"

failures = []


def kubectl(*args, stdin=None):
    return subprocess.run(["kubectl", "--context", CTX, *args],
                          capture_output=True, text=True, input=stdin)


def token():
    r = kubectl("get", "secret", "arubacloud-token", "-n", NS,
                "-o", "jsonpath={.data.token}")
    if r.returncode != 0 or not r.stdout.strip():
        sys.exit(f"cannot read arubacloud-token in {CTX}/{NS}: {r.stderr.strip()[:200]}")
    return base64.b64decode(r.stdout).decode().strip()


TOK = token()


def api(method, path, body=None):
    req = urllib.request.Request(
        f"{API}{path}{'&' if '?' in path else '?'}api-version=1.0", method=method,
        headers={"Authorization": f"Bearer {TOK}", "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, None
    except Exception as e:
        print(f"    transport error on {method} {path}: {type(e).__name__}")
        return 0, None


def vpc_body(name):
    return {"metadata": {"name": name, "location": {"value": LOCATION}, "tags": []},
            "properties": {"default": False, "preset": False}}


def list_vpcs(query=""):
    code, body = api("GET", VPCS + (f"?{query}" if query else ""))
    items = (body or {}).get("values") if isinstance(body, dict) else None
    return code, (items if isinstance(items, list) else [])


def names_of(items):
    return [((i.get("metadata") or {}).get("name")) for i in items]


def by_name(items, name):
    return [i for i in items if ((i.get("metadata") or {}).get("name")) == name]


def cr_manifest(cr_name, vpc_name):
    return json.dumps({
        "apiVersion": f"{GROUP}/v1alpha1", "kind": "Vpc",
        "metadata": {"name": cr_name, "namespace": NS,
                     "annotations": {"krateo.io/connector-verbose": "true"}},
        "spec": {"configurationRef": {"name": "vpc-config", "namespace": NS},
                 "projectId": PROJECT,
                 "metadata": {"name": vpc_name, "location": {"value": LOCATION},
                              "tags": []},
                 "properties": {"default": False, "preset": False}},
    })


def events_for(cr_name):
    r = kubectl("get", "events", "-n", NS,
                "--field-selector", f"involvedObject.name={cr_name}",
                "-o", "jsonpath={range .items[*]}{.reason}|{.message}{'\\n'}{end}")
    return [l for l in r.stdout.splitlines() if l.strip()]


def assert_limit_is_stored():
    """A Configuration patch exits 0 while storing NOTHING if the CRD prunes the field.

    Measured 2026-11-07 on the fleet definition: `kubectl patch ... findby.offset` printed
    a warning, exited 0, and stored nothing. So this reads the value back and refuses to
    continue if it is absent -- otherwise the whole experiment would run with an
    unnarrowed findby and report "no duplicate" as if that meant something.
    """
    r = kubectl("patch", "vpcconfiguration", "vpc-config", "-n", NS, "--type", "merge",
                "-p", json.dumps({"spec": {"configuration": {"query": {"findby": {"limit": "1"}}}}}))
    print(f"    patch rc={r.returncode} {r.stderr.strip()[:120]}")
    got = kubectl("get", "vpcconfiguration", "vpc-config", "-n", NS,
                  "-o", "jsonpath={.spec.configuration.query.findby.limit}").stdout.strip()
    if got != "1":
        sys.exit("ABORT: findby.limit did not store (read back %r). The CRD pruned it, so "
                 "this cluster's Vpc CRD was generated WITHOUT the experimental "
                 "definition. A result from here would be meaningless." % got)
    print(f"    ASSERTED findby.limit == {got!r} in the stored Configuration")


def run_arm(label, cr_name, vpc_name, expect_duplicate, settle=240):
    print(f"\n--- ARM {label}: CR {cr_name} -> existing vpc {vpc_name}"
          f" (expect duplicate: {expect_duplicate})")
    r = kubectl("apply", "-f", "-", stdin=cr_manifest(cr_name, vpc_name))
    if r.returncode != 0:
        failures.append(f"{label}: CR apply failed: {r.stderr.strip()[:200]}")
        return
    deadline = time.time() + settle
    dupes, evs = 1, []
    while time.time() < deadline:
        time.sleep(15)
        _, items = list_vpcs()
        dupes = len(by_name(items, vpc_name))
        evs = events_for(cr_name)
        if dupes > 1:
            break
    absence = [e for e in evs if "AbsenceInferredFromUnmatchedListing" in e]
    ids = [((i.get("metadata") or {}).get("id")) for i in by_name(list_vpcs()[1], vpc_name)]
    print(f"    upstream objects named {vpc_name}: {dupes}  ids={ids}")
    print(f"    absence events on the CR: {len(absence)}")
    for e in evs[:4]:
        print(f"      event: {e[:150]}")
    if expect_duplicate and dupes <= 1:
        failures.append(f"{label}: expected a duplicate, upstream still has {dupes}. "
                        f"#486's traced consequence did NOT reproduce.")
    if (not expect_duplicate) and dupes > 1:
        failures.append(f"{label}: CONTROL BROKEN -- a duplicate appeared for a vpc that "
                        f"IS on the page, so the arms do not discriminate.")
    print(f"    {label}: {'as predicted' if bool(dupes > 1) == expect_duplicate else 'NOT as predicted'}")


def cleanup():
    print("\n=== cleanup: deleting every probe CR and every probe VPC ===")
    for cr in (f"{PREFIX}-offpage", f"{PREFIX}-onpage"):
        kubectl("delete", f"vpc.{GROUP}", cr, "-n", NS, "--ignore-not-found",
                "--timeout=120s")
    time.sleep(20)
    _, items = list_vpcs()
    left = [i for i in items if str(names_of([i])[0] or "").startswith(PREFIX)]
    for i in left:
        md = i.get("metadata") or {}
        code, _ = api("DELETE", f"{VPCS}/{md.get('id')}")
        print(f"    DELETE {md.get('name')} ({md.get('id')}) -> {code}")
    for _ in range(12):
        time.sleep(15)
        _, items = list_vpcs()
        residue = [n for n in names_of(items) if str(n or "").startswith(PREFIX)]
        if not residue:
            print("    residue: 0 probe VPCs remain")
            return
    failures.append(f"CLEANUP: probe VPCs remain upstream: {residue}")


def main():
    print("=== preflight: what exists now ===")
    code, items = list_vpcs()
    print(f"    GET vpcs -> {code}, {len(items)} vpc(s): {names_of(items)}")
    if code != 200:
        sys.exit("cannot list vpcs; aborting before creating anything")

    print("\n=== creating two probe VPCs at the vendor (free, deleted at the end) ===")
    for n in (f"{PREFIX}-a", f"{PREFIX}-b"):
        c, _ = api("POST", VPCS, vpc_body(n))
        print(f"    POST {n} -> {c}")
        if c not in (200, 201, 202):
            cleanup()
            sys.exit(f"could not create {n}; nothing further attempted")
    time.sleep(20)

    print("\n=== which VPC does a 1-item page actually return? (measured, not assumed) ===")
    c, page = list_vpcs("limit=1")
    page_names = names_of(page)
    print(f"    GET vpcs?limit=1 -> {c}, {len(page)} item(s): {page_names}")
    if len(page) != 1:
        cleanup()
        sys.exit(f"limit=1 returned {len(page)} items, so this vendor does not honour it "
                 f"downward here and the experiment cannot be set up")
    _, allv = list_vpcs()
    offpage = [n for n in names_of(allv)
               if str(n or "").startswith(PREFIX) and n not in page_names]
    onpage = [n for n in page_names if str(n or "").startswith(PREFIX)]
    print(f"    probe VPCs OFF the page: {offpage}")
    print(f"    probe VPCs ON  the page: {onpage}")

    print("\n=== narrowing the findby through the Configuration ===")
    assert_limit_is_stored()

    if offpage:
        run_arm("OFF-PAGE", f"{PREFIX}-offpage", offpage[0], expect_duplicate=True)
    else:
        failures.append("no probe VPC is off the 1-item page; the subject arm could not "
                        "be set up and its absence is not a negative result")
    if onpage:
        run_arm("ON-PAGE", f"{PREFIX}-onpage", onpage[0], expect_duplicate=False)
    else:
        failures.append("no probe VPC is on the 1-item page; the CONTROL arm could not be "
                        "set up, so a duplicate in the other arm proves less")

    cleanup()
    print("\n" + "=" * 70)
    if failures:
        print(f"{len(failures)} PROBLEM(S):")
        for f in failures:
            print("  -", f)
        return 1
    print("BOTH ARMS AS PREDICTED: a truncated findby duplicated an existing resource at "
          "first contact; the same CR against an on-page resource adopted it cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
