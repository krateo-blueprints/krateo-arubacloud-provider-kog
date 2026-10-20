#!/usr/bin/env python3
"""Prove CloudServer's findby and get against a server the provider cannot create.

CloudServer is the one resource whose create is real but unreachable from a
RestDefinition: the POST lives in compute-provider_v1.1.json and a RestDefinition
carries a single oasPath (oasgen-provider#108). So the two verbs it DOES declare,
findby and get, have never been exercised against an actual server -- the CR has only
ever been applied against an empty collection, which proves nothing.

This creates the server directly over the API, hands it to the controller, and checks
that the CR converges on it. That is the most CloudServer can earn here: observe, not
lifecycle.

Everything is torn down in a finally. A previous hand-rolled probe in this campaign
left a billable Dbaas running because its cleanup sat after an early return, so the
order here is: record every id the moment it exists, and delete in reverse no matter
how the body exits.

Usage:
    KUBE_CONTEXT=kind-aruba-110 python3 -u scripts/ga_cloudserver_observe.py
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
GROUP = "arubacloud.ogen.krateo.io"
API = os.environ.get("ARUBA_API", "https://api.arubacloud.com")
PROJECT = os.environ.get("PROJECT", "69a55c8d5e6f0e14f14093ff")
LOCATION = os.environ.get("LOCATION", "ITBG-Bergamo")
DC = os.environ.get("DC", "ITBG-1")
VPC_ID = os.environ.get("VPC_ID", "69a5609fa851081197caa45f")
SUBNET_ID = os.environ.get("SUBNET_ID", "69a560ce2312085699426c28")
FLAVOR = os.environ.get("FLAVOR", "CSO1A2")
IMAGE = os.environ.get("IMAGE", "LU22-001")
NAME = os.environ.get("NAME", "ga-cs-observe")
TIMEOUT = int(os.environ.get("TIMEOUT", "900"))

BASE = f"/projects/{PROJECT}/providers"


def kubectl(*args, stdin=None):
    return subprocess.run(["kubectl", "--context", CONTEXT, *args],
                          capture_output=True, text=True, input=stdin)


def token():
    r = kubectl("get", "secret", "arubacloud-token", "-n", NS,
                "-o", "jsonpath={.data.token}")
    if r.returncode != 0 or not r.stdout.strip():
        raise SystemExit("no token in the ESO-managed Secret")
    return base64.b64decode(r.stdout).decode()


TOK = None


def api(method, path, body=None, version="1.0"):
    req = urllib.request.Request(
        f"{API}{path}?api-version={version}", method=method,
        headers={"Authorization": f"Bearer {TOK}", "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw.decode(errors="replace")[:400]


def errtext(body):
    """Render an Aruba error without assuming its envelope.

    The network and storage providers answer `errors: [{field, message}]`. Reading
    those as fieldName/errorMessage printed "None: None" and cost this campaign three
    wrong explanations. Then the 1.1 compute endpoint printed "None: None" too, from
    the corrected keys -- so the envelope is not even consistent between providers.

    Therefore: never key into it blind. Pull the common fields when they are there,
    and otherwise dump what arrived. An unreadable error is worse than a verbose one.
    """
    if isinstance(body, dict):
        errs = body.get("errors")
        if isinstance(errs, dict):          # ASP.NET ValidationProblemDetails
            return "; ".join(f"{k}: {v}" for k, v in list(errs.items())[:4])
        if isinstance(errs, list) and errs:
            out = []
            for e in errs[:4]:
                if isinstance(e, dict):
                    named = e.get("field") or e.get("fieldName") or e.get("name")
                    msg = (e.get("message") or e.get("errorMessage")
                           or e.get("detail") or e.get("description"))
                    out.append(f"{named}: {msg}" if (named or msg) else json.dumps(e))
                else:
                    out.append(str(e))
            return "; ".join(out)
        for k in ("detail", "title", "message", "error_description"):
            if body.get(k):
                return f"{body[k]} | {json.dumps(body)[:300]}"
        return json.dumps(body)[:400]
    return str(body)[:400]


def wait_state(path, want=("Active", "NotUsed"), label="", timeout=600):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        code, b = api("GET", path)
        if code == 200 and isinstance(b, dict):
            last = (b.get("status") or {}).get("state")
            if last in want:
                print(f"    {label} -> {last}")
                return True, b
        time.sleep(10)
    print(f"    {label} stuck at {last} after {timeout}s", file=sys.stderr)
    return False, None


def main():
    global TOK
    TOK = token()
    code, _ = api("GET", f"/projects/{PROJECT}")
    print(f"token OK (GET /projects/{{id}} -> {code})")

    vol_id = eip_id = srv_id = None
    cr_applied = False
    verdict = {}
    try:
        md = {"name": f"{NAME}-boot", "location": {"value": LOCATION}, "tags": []}
        code, b = api("POST", f"{BASE}/Aruba.Storage/blockStorages", {
            "metadata": md,
            "properties": {"sizeGb": 20, "billingPeriod": "Hour",
                           "bootable": True, "image": IMAGE}})
        if code not in (200, 201, 202):
            raise SystemExit(f"boot volume create failed {code}: {errtext(b)}")
        vol_id = b["metadata"]["id"]
        print(f"--- boot volume {vol_id} (bootable, {IMAGE})")
        wait_state(f"{BASE}/Aruba.Storage/blockStorages/{vol_id}",
                   label="boot volume")

        code, b = api("POST", f"{BASE}/Aruba.Network/elasticIps", {
            "metadata": {"name": f"{NAME}-eip", "location": {"value": LOCATION},
                         "tags": []},
            "properties": {"billingPlan": {"billingPeriod": "Hour"}}})
        if code not in (200, 201, 202):
            raise SystemExit(f"elastic ip create failed {code}: {errtext(b)}")
        eip_id = b["metadata"]["id"]
        print(f"--- elastic ip {eip_id}")
        wait_state(f"{BASE}/Aruba.Network/elasticIps/{eip_id}", label="elastic ip")

        # The create that no RestDefinition can drive: one POST at 1.1 carrying every
        # association. No power-on, no attach, no associate -- those are day-2 calls.
        srv_body = {
            "metadata": {"name": NAME, "location": {"value": LOCATION}, "tags": []},
            "properties": {
                "dataCenter": DC,
                "flavorName": FLAVOR,
                "vpcPreset": False,
                "vpc": {"uri": f"{BASE}/Aruba.Network/vpcs/{VPC_ID}"},
                "subnets": [{"uri": f"{BASE}/Aruba.Network/vpcs/{VPC_ID}"
                                    f"/subnets/{SUBNET_ID}"}],
                "elasticIp": {"uri": f"{BASE}/Aruba.Network/elasticIps/{eip_id}"},
                "bootVolume": {"uri": f"{BASE}/Aruba.Storage/blockStorages/{vol_id}"},
            },
        }
        code, b = api("POST", f"{BASE}/Aruba.Compute/cloudServers", srv_body,
                      version="1.1")
        if code not in (200, 201, 202):
            raise SystemExit(f"cloud server create failed {code}: {errtext(b)}")
        srv_id = b["metadata"]["id"]
        print(f"--- cloud server {srv_id} created at api-version=1.1")
        ok, _ = wait_state(f"{BASE}/Aruba.Compute/cloudServers/{srv_id}",
                           want=("Active",), label="cloud server", timeout=TIMEOUT)
        verdict["server_active"] = ok

        # Now the actual assertion: the CR must find and read THIS server.
        cr = f"""apiVersion: {GROUP}/v1-0-0
kind: CloudServer
metadata:
  name: {NAME}
  namespace: {NS}
  annotations:
    krateo.io/connector-verbose: 'true'
spec:
  configurationRef:
    name: cloudserver-config
    namespace: {NS}
  projectId: {PROJECT}
  metadata:
    name: {NAME}
"""
        r = kubectl("apply", "-f", "-", stdin=cr)
        print("---", (r.stdout + r.stderr).strip().splitlines()[-1][:120])
        if r.returncode != 0:
            raise SystemExit("CR apply failed")
        cr_applied = True

        deadline = time.time() + 420
        ready = False
        while time.time() < deadline:
            r = kubectl("get", f"cloudserver.{GROUP}", NAME, "-n", NS, "-o", "json")
            if r.returncode == 0:
                st = (json.loads(r.stdout).get("status") or {})
                conds = {c["type"]: c for c in st.get("conditions", [])}
                if conds.get("Ready", {}).get("status") == "True":
                    print(f"    CR Ready — observed id="
                          f"{(st.get('metadata') or {}).get('id')}")
                    ready = True
                    verdict["observed_id"] = (st.get("metadata") or {}).get("id")
                    break
            time.sleep(15)
        verdict["cr_ready"] = ready
        if not ready:
            r = kubectl("get", f"cloudserver.{GROUP}", NAME, "-n", NS, "-o",
                        "jsonpath={.status}")
            print(f"    CR NOT Ready: {r.stdout[:400]}", file=sys.stderr)

    finally:
        print("\n=== teardown ===")
        if cr_applied:
            r = kubectl("delete", f"cloudserver.{GROUP}", NAME, "-n", NS,
                        "--wait=true", "--timeout=300s")
            print("   ", (r.stdout + r.stderr).strip().splitlines()[-1][:120])
        for label, path, rid in (
                ("cloud server", f"{BASE}/Aruba.Compute/cloudServers", srv_id),
                ("elastic ip", f"{BASE}/Aruba.Network/elasticIps", eip_id),
                ("boot volume", f"{BASE}/Aruba.Storage/blockStorages", vol_id)):
            if not rid:
                continue
            # Aruba refuses a delete while the resource or its parent is still
            # transitioning, with the same {"field":"Status"} 400 as any other write.
            for attempt in range(12):
                code, b = api("DELETE", f"{path}/{rid}")
                if code in (200, 202, 204, 404):
                    print(f"    deleted {label} {rid} ({code})")
                    break
                print(f"    delete {label} -> {code}: {errtext(b)} (retrying)")
                time.sleep(20)
            else:
                print(f"    FAILED to delete {label} {rid} — CHECK THE CONSOLE",
                      file=sys.stderr)

        print("\n=== residue (ground truth) ===")
        leftovers = 0
        for label, path in (("cloudServers", f"{BASE}/Aruba.Compute/cloudServers"),
                            ("elasticIps", f"{BASE}/Aruba.Network/elasticIps"),
                            ("blockStorages", f"{BASE}/Aruba.Storage/blockStorages")):
            for attempt in range(6):
                code, b = api("GET", path)
                if code != 200 or not isinstance(b, dict):
                    print(f"    UNVERIFIED: {label} returned {code}", file=sys.stderr)
                    break
                mine = [v for v in (b.get("values") or [])
                        if (v.get("metadata") or {}).get("name", "").startswith(NAME)]
                if not mine:
                    print(f"    {label}: clean")
                    break
                if attempt == 5:
                    leftovers += len(mine)
                    print(f"    {label}: {len(mine)} LEFT — "
                          f"{[v['metadata']['name'] for v in mine]}", file=sys.stderr)
                time.sleep(20)
        print(f"\nRESULT: {json.dumps(verdict)}  leftovers={leftovers}")


if __name__ == "__main__":
    sys.exit(main() or 0)
