#!/usr/bin/env python3
"""Is ANY cloud-server flavor accepted on this account?

CSO1I1 is refused with `FlavorId <uuid> not available for trial`. The catalog exposes
only {cpu, name, ram}, so eligibility is not discoverable before the attempt, and the
trial check runs only AFTER every reference in the request resolves -- so it cannot be
probed with a deliberately-broken request. Reaching it needs an otherwise-valid
request, which is why this costs money.

COST MODEL, stated because it is the point of the exercise:
  * a refused flavor costs NOTHING (400 before anything is provisioned);
  * the prerequisites cost a 20 GB bootable volume for the duration (billable, Hour);
  * the FIRST ACCEPTED flavor creates a real cloud server, which is deleted
    immediately and verified gone.

Everything created is torn down in a finally, in reverse dependency order, and every
delete is verified against the API rather than assumed.
"""
import base64, json, subprocess, sys, time, urllib.error, urllib.request

B = "https://api.arubacloud.com"
P = "69a55c8d5e6f0e14f14093ff"
LOC = "ITBG-Bergamo"
DC = "ITBG-1"

def token():
    r = subprocess.run(["kubectl", "--kubeconfig", "/Users/diegobraga/.kube/config",
                        "--context", "kind-aruba-110", "get", "secret", "arubacloud-token",
                        "-n", "default", "-o", "jsonpath={.data.token}"],
                       capture_output=True, text=True)
    if r.returncode or not r.stdout.strip():
        raise SystemExit("no token in the ESO-managed Secret")
    return base64.b64decode(r.stdout).decode()

TOK = token()
H = {"Authorization": "Bearer " + TOK, "Accept": "application/json",
     "Content-Type": "application/json"}

def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(B + path, data=data, headers=H, method=method)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw.decode(errors="replace")[:300]}

created = []   # (label, delete_path) in creation order; torn down reversed

def wait_state(path, want, label, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        c, b = call("GET", path)
        st = ((b.get("status") or {}) if isinstance(b, dict) else {}).get("state")
        if st in want:
            print(f"    {label}: {st} at t+{int(time.time()-t0)}s")
            return st
        time.sleep(10)
    print(f"    {label}: TIMEOUT, last state {st!r}")
    return None

def main():
    print("=== flavors, deduped and ordered cheapest-first ===")
    c, b = call("GET", "/providers/Aruba.Compute/catalog/cloudServerFlavors?api-version=1.0")
    rows = b if isinstance(b, list) else b.get("values", [])
    seen, flavors = set(), []
    for f in sorted(rows, key=lambda x: (x.get("cpu") or 0, x.get("ram") or 0)):
        n = f.get("name")
        if n and n not in seen:
            seen.add(n)
            flavors.append((n, f.get("cpu"), f.get("ram")))
    print(f"    {len(rows)} entries -> {len(flavors)} unique names")

    print("=== prerequisites (vpc/subnet/sg free; the volume is billable) ===")
    c, b = call("POST", f"/projects/{P}/providers/Aruba.Network/vpcs?api-version=1.0",
                {"metadata": {"name": "fp-vpc", "location": {"value": LOC}, "tags": []},
                 "properties": {"default": False, "preset": False}})
    if c not in (200, 201, 202):
        print(f"    vpc failed {c}: {json.dumps(b)[:200]}"); return 1
    vpc = b.get("id") or (b.get("metadata") or {}).get("id")
    created.append(("vpc", f"/projects/{P}/providers/Aruba.Network/vpcs/{vpc}"))
    print(f"    vpc {vpc}")
    wait_state(f"/projects/{P}/providers/Aruba.Network/vpcs/{vpc}?api-version=1.0",
               {"Active"}, "vpc")

    c, b = call("POST", f"/projects/{P}/providers/Aruba.Network/vpcs/{vpc}/subnets?api-version=1.0",
                {"metadata": {"name": "fp-subnet", "location": {"value": LOC}, "tags": []},
                 "properties": {"type": "Advanced", "default": False,
                                "network": {"address": "10.77.1.0/24"}}})
    if c not in (200, 201, 202):
        print(f"    subnet failed {c}: {json.dumps(b)[:200]}"); return 1
    sub = b.get("id") or (b.get("metadata") or {}).get("id")
    created.append(("subnet", f"/projects/{P}/providers/Aruba.Network/vpcs/{vpc}/subnets/{sub}"))
    print(f"    subnet {sub}")
    wait_state(f"/projects/{P}/providers/Aruba.Network/vpcs/{vpc}/subnets/{sub}?api-version=1.0",
               {"Active"}, "subnet")

    c, b = call("POST", f"/projects/{P}/providers/Aruba.Network/vpcs/{vpc}/securityGroups?api-version=1.0",
                {"metadata": {"name": "fp-sg", "location": {"value": LOC}, "tags": []},
                 "properties": {"default": False, "preset": False}})
    if c not in (200, 201, 202):
        print(f"    sg failed {c}: {json.dumps(b)[:200]}"); return 1
    sg = b.get("id") or (b.get("metadata") or {}).get("id")
    created.append(("sg", f"/projects/{P}/providers/Aruba.Network/vpcs/{vpc}/securityGroups/{sg}"))
    print(f"    sg {sg}")

    c, b = call("POST", f"/projects/{P}/providers/Aruba.Storage/blockStorages?api-version=1.0",
                {"metadata": {"name": "fp-boot", "location": {"value": LOC}, "tags": []},
                 "properties": {"sizeGb": 20, "billingPeriod": "Hour",
                                "bootable": True, "image": "LU22-001"}})
    if c not in (200, 201, 202):
        print(f"    volume failed {c}: {json.dumps(b)[:200]}"); return 1
    vol = b.get("id") or (b.get("metadata") or {}).get("id")
    created.append(("volume", f"/projects/{P}/providers/Aruba.Storage/blockStorages/{vol}"))
    print(f"    volume {vol}  <-- BILLABLE")
    wait_state(f"/projects/{P}/providers/Aruba.Storage/blockStorages/{vol}?api-version=1.0",
               {"NotUsed", "Active"}, "volume")

    print(f"=== attempting {len(flavors)} flavors; a refusal costs nothing ===")
    accepted = None
    for name, cpu, ram in flavors:
        body = {"metadata": {"name": "fp-server", "location": {"value": LOC}, "tags": []},
                "properties": {"dataCenter": DC, "flavorName": name,
                               "vpc": {"uri": f"/projects/{P}/providers/Aruba.Network/vpcs/{vpc}"},
                               "bootVolume": {"uri": f"/projects/{P}/providers/Aruba.Storage/blockStorages/{vol}"},
                               "subnets": [{"uri": f"/projects/{P}/providers/Aruba.Network/vpcs/{vpc}/subnets/{sub}"}],
                               "securityGroups": [{"uri": f"/projects/{P}/providers/Aruba.Network/vpcs/{vpc}/securityGroups/{sg}"}]}}
        c, b = call("POST", f"/projects/{P}/providers/Aruba.Compute/cloudServers?api-version=1.1", body)
        if c in (200, 201, 202):
            sid = b.get("id") or (b.get("metadata") or {}).get("id")
            created.append(("server", f"/projects/{P}/providers/Aruba.Compute/cloudServers/{sid}"))
            print(f"    {name:12s} cpu={cpu} ram={ram} -> *** ACCEPTED {c}, id={sid} ***")
            accepted = (name, cpu, ram, sid)
            break
        errs = b.get("errors") if isinstance(b, dict) else None
        msg = "; ".join(f"{e.get('fieldName')}={e.get('errorMessage')}" for e in errs) if errs else json.dumps(b)[:120]
        print(f"    {name:12s} cpu={cpu} ram={ram} -> {c}  {msg}")
    print("=== verdict ===")
    print(f"    {'ACCEPTED: ' + repr(accepted) if accepted else 'NO FLAVOR ACCEPTED on this account'}")
    return 0

rc = 1
try:
    rc = main()
finally:
    print("=== teardown, reverse order, every delete verified ===")
    for label, path in reversed(created):
        for attempt in range(1, 13):
            call("DELETE", path + "?api-version=1.0")
            time.sleep(10)
            c, _ = call("GET", path + "?api-version=1.0")
            if c == 404:
                print(f"    {label}: 404 after {attempt} delete(s)"); break
            print(f"    {label}: still present (GET {c}), retrying")
        else:
            print(f"    *** {label} STILL PRESENT: {path}")
sys.exit(rc)
