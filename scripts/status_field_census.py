"""Do real vendor responses carry values for our declared additionalStatusFields?

`verify_status_fields_exist_in_response_schema` in scripts/validate.py proves the PATH
exists in the response SCHEMA -- 88 declarations across 33 kinds, all present. This asks
the different question of whether anything actually travels down it, by reading live
resources.

THE SPLIT THAT MAKES THE RESULT READABLE. The dominant declaration across 33 kinds is the
triple {metadata.id, status.error, status.failureReason}. The vendor sends the last two
ONLY when a resource has FAILED, so their absence on a healthy resource is CORRECT
behaviour. Counting them together with metadata.id would report "2 of 3 fields never
populated" and read as a defect. They are therefore reported as UNTESTABLE-without-a-
failure rather than as absent.

WHAT IT CAN AND CANNOT COVER. Only kinds with a parameterless findby and at least one
live resource can be read. Measured 2026-11-12: 23 kinds have a reachable findby, 7 of
those had a resource to read, and all 7 carried their identifier field. The other 16 would
need resources created -- most of them billable -- and the error-path fields would need a
resource deliberately broken. Both are stated rather than silently omitted.

READ-ONLY: GET only, nothing created, nothing billed.

    KUBE_CONTEXT=<ctx> PROJECT=<id> python3 -I scripts/status_field_census.py
"""
import base64, glob, json, os, subprocess, sys, urllib.request, yaml

API = "https://api.arubacloud.com"
PROJECT = os.environ["PROJECT"]
CTX = os.environ["KUBE_CONTEXT"]
ERROR_ONLY = {"status.error", "status.failureReason", "error", "failureReason"}

def token():
    out = subprocess.run(["kubectl", "--context", CTX, "get", "secret", "arubacloud-token",
                          "-n", "default", "-o", "jsonpath={.data.token}"],
                         capture_output=True, text=True, check=True).stdout
    return base64.b64decode(out).decode().strip()

TOK = token()

def get(path):
    req = urllib.request.Request(
        f"{API}{path}{'&' if '?' in path else '?'}api-version=1.0",
        headers={"Authorization": f"Bearer {TOK}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"null")
    except Exception as e:
        return getattr(e, "code", 0), None

def dig(o, dotted):
    cur = o
    for p in dotted.split("."):
        if not isinstance(cur, dict):
            return None, False
        if p not in cur:
            return None, False
        cur = cur[p]
    return cur, True

# kind -> (findby path, declared fields)
kinds = {}
for f in sorted(glob.glob("restdefinitions/*/*.yaml")):
    d = yaml.safe_load(open(f)) or {}
    if d.get("kind") != "RestDefinition":
        continue
    res = (d.get("spec") or {}).get("resource") or {}
    kind = res.get("kind")
    fields = set()
    def walk(n):
        if isinstance(n, dict):
            if isinstance(n.get("additionalStatusFields"), list):
                fields.update(n["additionalStatusFields"])
            for v in n.values(): walk(v)
        elif isinstance(n, list):
            for v in n: walk(v)
    walk(d)
    path = None
    for v in (res.get("verbsDescription") or []):
        if str(v.get("action", "")).lower() == "findby":
            path = v.get("path")
    if kind and fields and path and "{" not in path.replace("{projectId}", ""):
        kinds[kind] = (path.replace("{projectId}", PROJECT), sorted(fields))

print(f"  kinds with a parameterless findby and declared fields: {len(kinds)}\n")
hdr = f"  {'kind':<20} {'items':>5}  {'id-ish present':<16} {'error-path fields':<22}"
print(hdr); print("  " + "-" * (len(hdr) - 2))
pop = ident_ok = ident_missing = 0
for kind, (path, fields) in sorted(kinds.items()):
    code, body = get(path)
    items = body.get("values") if isinstance(body, dict) else None
    if code != 200 or not isinstance(items, list):
        print(f"  {kind:<20} {'-':>5}  HTTP {code}")
        continue
    if not items:
        print(f"  {kind:<20} {0:>5}  (no resource to read)")
        continue
    pop += 1
    item = items[0]
    ident = [f for f in fields if f not in ERROR_ONLY]
    got = [f for f in ident if dig(item, f)[1] and dig(item, f)[0] not in (None, "")]
    err = [f for f in fields if f in ERROR_ONLY]
    ok = len(got) == len(ident) and ident
    ident_ok += int(bool(ok)); ident_missing += int(not ok)
    print(f"  {kind:<20} {len(items):>5}  {('%d/%d %s' % (len(got), len(ident), 'OK' if ok else 'MISSING')):<16} "
          f"{(', '.join(err) + ' (untestable: needs a FAILED resource)') if err else '(none declared)'}")

print(f"\n  populated kinds read: {pop}")
print(f"  identifier fields all present: {ident_ok}    with a gap: {ident_missing}")
