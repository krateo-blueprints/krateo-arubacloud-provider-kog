#!/usr/bin/env python3
"""Stand up a CloudServer so the Job chain can run, then take it all down.

A Job's only supported step actions are poweron/poweroff, so it needs a CloudServer to
target — and a CloudServer cannot be created by a RestDefinition, because its POST
lives in the 1.1 OAS document (oasgen-provider#108). That is why Job has never been
exercised: the fixture needs a server that the provider itself cannot make.

This creates the server over the API, hands its id to `tests/ga/chains/job.yaml`, runs
that chain, and removes the server afterwards whatever happened. The chain handles its
own Job teardown and residue check; this script owns only the out-of-band server.

Everything is torn down in a finally, with the ids recorded the moment each resource
exists. This repo has twice had a probe leave billable resources running, both times
because cleanup sat after an early return.

Usage:
    KUBE_CONTEXT=kind-aruba-110 python3 -u scripts/ga_job_probe.py
"""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

CONTEXT = os.environ.get("KUBE_CONTEXT", "kind-aruba-110")
NS = os.environ.get("NS", "default")
API = os.environ.get("ARUBA_API", "https://api.arubacloud.com")
PROJECT = os.environ.get("PROJECT", "69a55c8d5e6f0e14f14093ff")
LOCATION = os.environ.get("LOCATION", "ITBG-Bergamo")
DC = os.environ.get("DC", "ITBG-1")
VPC_ID = os.environ.get("VPC_ID", "69a5609fa851081197caa45f")
SUBNET_ID = os.environ.get("SUBNET_ID", "69a560ce2312085699426c28")
SG_ID = os.environ.get("SG_ID", "69a560ce2312085699426c2a")
FLAVOR = os.environ.get("FLAVOR", "CSO1A2")
IMAGE = os.environ.get("IMAGE", "LU22-001")
NAME = os.environ.get("NAME", "ga-job-target")
BASE = f"/projects/{PROJECT}/providers"

TOK = None


def kubectl(*args):
    return subprocess.run(["kubectl", "--context", CONTEXT, *args],
                          capture_output=True, text=True)


def token():
    r = kubectl("get", "secret", "arubacloud-token", "-n", NS,
                "-o", "jsonpath={.data.token}")
    if r.returncode != 0 or not r.stdout.strip():
        raise SystemExit("no token in the ESO-managed Secret")
    return base64.b64decode(r.stdout).decode()


def api(method, path, body=None, version="1.0", _retried=False):
    global TOK
    req = urllib.request.Request(
        f"{API}{path}?api-version={version}", method=method,
        headers={"Authorization": f"Bearer {TOK}", "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        # A probe that holds one token for its whole run tears down with an expired
        # one: 401 on every DELETE, and a billable server left running. Re-read the
        # ESO-managed Secret and replay the call once.
        if e.code == 401 and not _retried:
            print("    401 — re-reading the ESO Secret and retrying once",
                  file=sys.stderr)
            TOK = token()
            return api(method, path, body, version, _retried=True)
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw.decode(errors="replace")[:400]


def err(b):
    """Render an error without assuming the envelope — this API uses three."""
    if isinstance(b, dict):
        es = b.get("errors")
        if isinstance(es, list) and es:
            return "; ".join(
                f"{e.get('field') or e.get('fieldName')}: "
                f"{e.get('message') or e.get('errorMessage')}"
                for e in es[:4] if isinstance(e, dict))
        if isinstance(es, dict):
            return "; ".join(f"{k}: {v}" for k, v in list(es.items())[:4])
        for k in ("detail", "title", "message"):
            if b.get(k):
                return str(b[k])
        return json.dumps(b)[:300]
    return str(b)[:300]


def wait_state(path, want, label, timeout=900):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        code, b = api("GET", path)
        if code == 200 and isinstance(b, dict):
            last = (b.get("status") or {}).get("state")
            if last in want:
                print(f"    {label} -> {last}", flush=True)
                return True
        time.sleep(15)
    print(f"    {label} stuck at {last} after {timeout}s", file=sys.stderr)
    return False


def main():
    global TOK
    TOK = token()
    print(f"token OK (GET /projects/{{id}} -> {api('GET', f'/projects/{PROJECT}')[0]})")

    vol = eip = srv = None
    try:
        code, b = api("POST", f"{BASE}/Aruba.Storage/blockStorages", {
            "metadata": {"name": f"{NAME}-boot", "location": {"value": LOCATION},
                         "tags": []},
            "properties": {"sizeGb": 20, "billingPeriod": "Hour",
                           "bootable": True, "image": IMAGE}})
        if code not in (200, 201, 202):
            raise SystemExit(f"boot volume failed {code}: {err(b)}")
        vol = b["metadata"]["id"]
        print(f"--- boot volume {vol}")
        wait_state(f"{BASE}/Aruba.Storage/blockStorages/{vol}",
                   ("Active", "NotUsed"), "boot volume")

        code, b = api("POST", f"{BASE}/Aruba.Network/elasticIps", {
            "metadata": {"name": f"{NAME}-eip", "location": {"value": LOCATION},
                         "tags": []},
            "properties": {"billingPlan": {"billingPeriod": "Hour"}}})
        if code not in (200, 201, 202):
            raise SystemExit(f"elastic ip failed {code}: {err(b)}")
        eip = b["metadata"]["id"]
        print(f"--- elastic ip {eip}")
        wait_state(f"{BASE}/Aruba.Network/elasticIps/{eip}",
                   ("Active", "NotUsed"), "elastic ip")

        # securityGroups is REQUIRED here though the schema marks nothing required.
        code, b = api("POST", f"{BASE}/Aruba.Compute/cloudServers", {
            "metadata": {"name": NAME, "location": {"value": LOCATION}, "tags": []},
            "properties": {
                "dataCenter": DC, "flavorName": FLAVOR, "vpcPreset": False,
                "vpc": {"uri": f"{BASE}/Aruba.Network/vpcs/{VPC_ID}"},
                "subnets": [{"uri": f"{BASE}/Aruba.Network/vpcs/{VPC_ID}"
                                    f"/subnets/{SUBNET_ID}"}],
                "securityGroups": [{"uri": f"{BASE}/Aruba.Network/vpcs/{VPC_ID}"
                                           f"/securityGroups/{SG_ID}"}],
                "elasticIp": {"uri": f"{BASE}/Aruba.Network/elasticIps/{eip}"},
                "bootVolume": {"uri": f"{BASE}/Aruba.Storage/blockStorages/{vol}"},
            }}, version="1.1")
        if code not in (200, 201, 202):
            raise SystemExit(f"cloud server failed {code}: {err(b)}")
        srv = b["metadata"]["id"]
        print(f"--- cloud server {srv} (api-version=1.1)")
        if not wait_state(f"{BASE}/Aruba.Compute/cloudServers/{srv}",
                          ("Active",), "cloud server"):
            raise SystemExit("the server never reached Active; not running the chain")

        print(f"\n=== running the job chain against CS_ID={srv} ===", flush=True)
        env = dict(os.environ, CS_ID=srv, KUBE_CONTEXT=CONTEXT)
        proc = subprocess.run(
            [sys.executable, "-u", "scripts/ga_chain.py", "tests/ga/chains/job.yaml"],
            env=env, text=True)
        print(f"\n=== chain exited {proc.returncode} ===", flush=True)
    finally:
        print("\n=== removing the out-of-band server ===", flush=True)
        for label, path, rid in (
                ("cloud server", f"{BASE}/Aruba.Compute/cloudServers", srv),
                ("elastic ip", f"{BASE}/Aruba.Network/elasticIps", eip),
                ("boot volume", f"{BASE}/Aruba.Storage/blockStorages", vol)):
            if not rid:
                continue
            for _ in range(30):
                code, b = api("DELETE", f"{path}/{rid}")
                if code in (200, 202, 204, 404):
                    print(f"    deleted {label} {rid} ({code})", flush=True)
                    break
                print(f"    delete {label} -> {code}: {err(b)} (retrying)", flush=True)
                time.sleep(20)
            else:
                print(f"    FAILED to delete {label} {rid} — CHECK THE CONSOLE",
                      file=sys.stderr)
        print("\n=== residue ===", flush=True)
        for label, path in (("cloudServers", f"{BASE}/Aruba.Compute/cloudServers"),
                            ("elasticIps", f"{BASE}/Aruba.Network/elasticIps"),
                            ("blockStorages", f"{BASE}/Aruba.Storage/blockStorages"),
                            ("jobs", f"{BASE}/Aruba.Schedule/jobs")):
            for attempt in range(8):
                code, b = api("GET", path)
                if code != 200 or not isinstance(b, dict):
                    print(f"    UNVERIFIED {label}: HTTP {code}", file=sys.stderr)
                    break
                mine = [v for v in (b.get("values") or [])
                        if (v.get("metadata") or {}).get("name", "").startswith("ga-")]
                if not mine:
                    print(f"    {label}: clean", flush=True)
                    break
                if attempt == 7:
                    print(f"    {label}: LEFT {[v['metadata']['name'] for v in mine]}",
                          file=sys.stderr)
                time.sleep(20)


if __name__ == "__main__":
    sys.exit(main() or 0)
