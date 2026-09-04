#!/usr/bin/env python3
"""Drive a declared chain of resources through create -> observe -> drift -> delete.

Chains are DATA (tests/ga/chains/*.yaml), not code. Every resource added by hand as
another bash block was another chance to repeat a shell bug, and two of those already
cost real money: a subshell that silently discarded the teardown registry and left
four live resources running, and progress text captured into an id because it went to
stdout. Neither failure mode exists here.

Teardown runs in reverse dependency order from a finally block, so a failure anywhere
still removes what was created, and residue is then verified against the API rather
than inferred from the CRs disappearing.

Usage:
    scripts/ga_chain.py tests/ga/chains/schedule.yaml [--with-billable] [--keep]

Chain format:

    name: schedule
    vars: {PROJECT: ..., LOCATION: ITBG-Bergamo}
    residue:                       # endpoints checked for leftovers, ${VAR} expanded
      - /projects/${PROJECT}/providers/Aruba.Schedule/backupPolicies
    resources:
      - kind: BackupPolicy
        name: ga-backuppolicy
        billable: false            # true => skipped unless --with-billable
        spec: {...}                # ${VAR} and ${id:OtherKind} expanded
        drift: /projects/${PROJECT}/providers/Aruba.Schedule/backupPolicies/${id:self}

`${id:self}` is this resource's own upstream id. `${id:Kind}` is another resource's,
and `${id:<name>}` selects a specific one by CR name -- required whenever a chain
contains two resources of the same kind, since `${id:Kind}` resolves to whichever ran
last.
"""

import atexit
import base64
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

import yaml

# Unbuffered: stdout is block-buffered when redirected to a file, so a run killed
# externally leaves an EMPTY log and no way to tell what it had created. That is
# exactly how a killed run turned into resources with no record of them.
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

CONTEXT = os.environ.get("KUBE_CONTEXT", "kind-aruba-ga")
NS = os.environ.get("NS", "default")
GROUP = "arubacloud.ogen.krateo.io"
API = os.environ.get("ARUBA_API", "https://api.arubacloud.com")
TOKEN_SECRET = os.environ.get("ARUBA_TOKEN_SECRET", "arubacloud-token")
TOKEN_SECRET_NS = os.environ.get("ARUBA_TOKEN_SECRET_NS", "default")
TIMEOUT = int(os.environ.get("TIMEOUT", "420"))
DRIFT_TIMEOUT = int(os.environ.get("DRIFT_TIMEOUT", "300"))


def kubectl(*args, check=False, stdin=None):
    return subprocess.run(
        ["kubectl", "--context", CONTEXT, *args],
        capture_output=True, text=True, check=check, input=stdin,
    )


def token():
    """Read the ESO-managed Secret, which is the only copy anything keeps fresh.

    A file under /tmp is a manual-bootstrap artefact and goes stale the moment ESO
    rotates; preferring it produced 401s against an otherwise healthy cluster.
    """
    r = kubectl("get", "secret", TOKEN_SECRET, "-n", TOKEN_SECRET_NS,
                "-o", "jsonpath={.data.token}")
    if r.returncode == 0 and r.stdout.strip():
        return base64.b64decode(r.stdout).decode()
    raise SystemExit(f"no token in {TOKEN_SECRET_NS}/{TOKEN_SECRET}")


def api(method, path, tok, body=None):
    req = urllib.request.Request(
        f"{API}{path}{'&' if '?' in path else '?'}api-version=1.0",
        method=method,
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, None


def expand(obj, variables, ids, self_kind=None):
    """Substitute ${VAR} and ${id:Kind}/${id:self} throughout a nested structure."""
    if isinstance(obj, dict):
        return {k: expand(v, variables, ids, self_kind) for k, v in obj.items()}
    if isinstance(obj, list):
        return [expand(v, variables, ids, self_kind) for v in obj]
    if not isinstance(obj, str):
        return obj

    def sub(m):
        key = m.group(1)
        if key.startswith("id:"):
            want = key[3:]
            want = self_kind if want == "self" else want
            if want not in ids:
                raise KeyError(f"${{{key}}} used before {want} was created")
            return ids[want]
        if key not in variables:
            raise KeyError(f"undefined variable ${{{key}}}")
        return str(variables[key])

    return re.sub(r"\$\{([^}]+)\}", sub, obj)


def wait_ready(kind, name):
    deadline = time.time() + TIMEOUT
    while True:
        r = kubectl("get", f"{kind}.{GROUP}", name, "-n", NS, "-o",
                    'jsonpath={.status.conditions[?(@.type=="Ready")].status}')
        if r.stdout.strip() == "True":
            return True
        if time.time() >= deadline:
            msg = kubectl("get", f"{kind}.{GROUP}", name, "-n", NS, "-o",
                          "jsonpath={.status.conditions[*].message}").stdout
            print(f"    TIMEOUT: {kind}/{name} never became Ready — {msg[:300]}", file=sys.stderr)
            return False
        time.sleep(10)


def served_version(kind):
    """Ask the cluster which version the CRD actually serves.

    Not every resource is v1-0-0. The CRD version derives from the OAS info.version,
    and metering declares "1.0" rather than "1.0.0", so AlertRule is served at v1-0.
    Hardcoding a default meant a chain entry for it would be rejected outright.
    """
    r = kubectl("get", "crd", "-o",
                "jsonpath={range .items[?(@.spec.names.kind=='" + kind + "')]}"
                "{.spec.versions[0].name}{end}")
    return r.stdout.strip() or None


def upstream_id(kind, name, id_path=None):
    """Find the upstream id in status, whatever the API chose to call it.

    Not every resource reports `id`. Aruba returns `keyId` for security/Key and
    `kmipId` for Kmip, and a runner that only knew metadata.id and id declared those
    resources broken when they were in fact working perfectly -- the CR was Ready with
    status.keyId populated. Fall back to any *Id field before giving up.
    """
    candidates = [f"{{.status.{id_path}}}"] if id_path else []
    candidates += ["{.status.metadata.id}", "{.status.id}"]
    for path in candidates:
        r = kubectl("get", f"{kind}.{GROUP}", name, "-n", NS, "-o", f"jsonpath={path}")
        if r.stdout.strip():
            return r.stdout.strip()

    r = kubectl("get", f"{kind}.{GROUP}", name, "-n", NS, "-o", "jsonpath={.status}")
    try:
        st = json.loads(r.stdout or "{}")
    except Exception:
        return None
    for k, v in st.items():
        if k.lower().endswith("id") and isinstance(v, str) and v:
            return v
    return None


def drift_check(kind, name, url, tok):
    """Set a tag upstream and require the controller to remove it.

    Deliberately the scenario oasgen-provider#76 got wrong: the CR declares
    `tags: []`, and before 0.22.1 an empty list matched any remote list, so this
    assertion passed vacuously while the resource stayed diverged.
    """
    code, body = api("GET", url, tok)
    if code != 200 or not isinstance(body, dict):
        print(f"    drift: cannot read {kind}/{name} (HTTP {code}) — skipped", file=sys.stderr)
        return None

    desired = json.loads(json.dumps(body))
    desired.pop("status", None)
    md = desired.setdefault("metadata", {})
    for k in ("id", "uri", "creationDate", "updateDate", "createdBy",
              "updatedBy", "project", "category", "version"):
        md.pop(k, None)
    md["tags"] = ["drifted-by-hand"]

    code, _ = api("PUT", url, tok, desired)
    # 202 Accepted is a normal answer here: storage updates are asynchronous, and
    # treating anything but 200 as a failure silently skipped the drift check on every
    # resource whose update is queued rather than applied inline.
    if code not in (200, 202, 204):
        print(f"    drift: PUT returned {code} — cannot inject into {kind}/{name}", file=sys.stderr)
        return None
    print(f"    drift injected (tags=[drifted-by-hand]); waiting for correction")

    kubectl("annotate", f"{kind}.{GROUP}", name, "-n", NS,
            f"drift-probe={int(time.time())}", "--overwrite")
    deadline = time.time() + DRIFT_TIMEOUT
    while time.time() < deadline:
        code, cur = api("GET", url, tok)
        if code == 200 and isinstance(cur, dict):
            tags = (cur.get("metadata") or {}).get("tags")
            if tags == []:
                print(f"    DRIFT CORRECTED for {kind}/{name}")
                return True
        time.sleep(20)
    print(f"    DRIFT NOT CORRECTED for {kind}/{name} (still {tags})", file=sys.stderr)
    return False


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if not args:
        raise SystemExit(__doc__)
    chain = yaml.safe_load(open(args[0]))
    variables = dict(chain.get("vars", {}))

    # Any var may be overridden from the environment, and a var declared with an empty
    # value MUST be. That is how a secret reaches a run without being committed: a VPN
    # pre-shared key belongs in the operator's shell, not in a fixture in git.
    for k in list(variables) + [k for k in os.environ if k.startswith("GA_")]:
        env_key = k[3:] if k.startswith("GA_") else k
        if k in os.environ:
            variables[k] = os.environ[k]
        elif ("GA_" + k) in os.environ:
            variables[k] = os.environ["GA_" + k]
    missing = [k for k, v in variables.items() if v in (None, "")]
    if missing:
        raise SystemExit(
            f"chain '{chain['name']}' needs these vars supplied from the environment: "
            + ", ".join(missing) + "\n(declared empty in the fixture precisely so they "
            "are never committed — export GA_<NAME> or <NAME> before running)")
    tok = token()

    code, _ = api("GET", "/projects", tok)
    if code != 200:
        raise SystemExit(f"token is not usable (HTTP {code})")
    print(f"token OK — chain '{chain['name']}'")

    ids, created, drift_results = {}, [], {}

    # SIGTERM (a CI timeout, an impatient operator, a harness limit) does not raise in
    # Python, so `finally` never runs and the teardown is skipped -- which already left
    # a resource live once. Turn the signal into an exception so the finally block does
    # its job, and keep an atexit net for anything that still slips past.
    def _term(signum, frame):
        raise KeyboardInterrupt(f"received signal {signum}")
    for _sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        try:
            signal.signal(_sig, _term)
        except Exception:
            pass

    try:
        for res in chain["resources"]:
            kind, name = res["kind"], res["name"]
            if res.get("billable") and "--with-billable" not in flags:
                print(f"--- SKIP {kind}/{name} (billable; pass --with-billable)")
                continue

            spec = expand(res["spec"], variables, ids, kind)
            version = res.get("version") or served_version(kind) or "v1-0-0"
            manifest = {
                "apiVersion": f"{GROUP}/{version}",
                "kind": kind,
                "metadata": {"name": name, "namespace": NS,
                             "annotations": {"krateo.io/connector-verbose": "true"}},
                "spec": spec,
            }
            print(f"--- creating {kind}/{name}")
            r = kubectl("apply", "-f", "-", stdin=yaml.safe_dump(manifest))
            if r.returncode != 0:
                print(f"    apply failed: {r.stderr.strip()[:300]}", file=sys.stderr)
                raise RuntimeError("apply failed")
            created.append((kind, name))          # registered BEFORE waiting

            if not wait_ready(kind, name):
                raise RuntimeError(f"{kind}/{name} never became Ready")
            rid = upstream_id(kind, name, res.get("idPath"))
            if not rid:
                raise RuntimeError(f"{kind}/{name} reported no upstream id")
            # Keyed by BOTH kind and CR name. Kind alone collides as soon as a chain
            # holds two of the same kind -- a VPC peering needs two Vpcs, and the
            # second silently overwrote the first, so ${id:Vpc} meant "whichever ran
            # last". ${id:<name>} is unambiguous; ${id:Kind} still works for the common
            # single-instance case.
            ids[kind] = rid
            ids[name] = rid
            print(f"    Ready, id={rid}")

            if res.get("drift"):
                url = expand(res["drift"], variables, ids, kind)
                drift_results[f"{kind}/{name}"] = drift_check(kind, name, url, tok)

        print(f"\nRESULT: {len(created)} resources created and observed")
        for k, v in drift_results.items():
            print(f"  drift {k}: {'ok' if v else ('SKIPPED' if v is None else 'FAILED')}")
        return 0 if all(v is not False for v in drift_results.values()) else 1

    finally:
        if "--keep" in flags:
            # Deliberately not `return`: returning from a finally block swallows an
            # exception that was propagating, turning a failed run into a silent pass.
            print("\n--keep: leaving resources in place; they may bill")
            created = []

        if created:
            print("\n=== teardown (reverse dependency order) ===")
            for kind, name in reversed(created):
                print(f"--- deleting {kind}/{name}")
                r = kubectl("delete", f"{kind}.{GROUP}", name, "-n", NS,
                            "--wait=true", f"--timeout={TIMEOUT}s")
                out = (r.stdout + r.stderr).strip().splitlines()
                print("   ", out[-1][:160] if out else "(no output)")

            print("\n=== residue check (ground truth, async deletion allowed for) ===")
            names = {n for _, n in created}
            for attempt in range(1, 7):
                left = []
                for ep in chain.get("residue", []):
                    code, body = api("GET", expand(ep, variables, ids), tok)
                    if code != 200 or not isinstance(body, dict):
                        continue
                    vals = body.get("values") or body.get("value") or []
                    left += [v for v in vals
                             if ((v.get("metadata") or {}).get("name") or v.get("name")) in names]
                print(f"    attempt {attempt}: leftovers = {len(left)}")
                if not left:
                    print("    clean")
                    break
                time.sleep(20)
            else:
                print("    WARNING: resources may remain — check the Aruba console", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main() or 0)
