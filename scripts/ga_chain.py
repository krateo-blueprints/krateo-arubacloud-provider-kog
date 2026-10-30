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
import glob
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTEXT = os.environ.get("KUBE_CONTEXT", "kind-aruba-ga")
NS = os.environ.get("NS", "default")
GROUP = "arubacloud.ogen.krateo.io"
API = os.environ.get("ARUBA_API", "https://api.arubacloud.com")
TOKEN_SECRET = os.environ.get("ARUBA_TOKEN_SECRET", "arubacloud-token")
TOKEN_SECRET_NS = os.environ.get("ARUBA_TOKEN_SECRET_NS", "default")
TIMEOUT = int(os.environ.get("TIMEOUT", "420"))
DRIFT_TIMEOUT = int(os.environ.get("DRIFT_TIMEOUT", "300"))
# How long a single resource's delete may take to become verifiable. The default is
# the old fixed budget (12 polls, 15s apart). It is a knob because three minutes
# cannot distinguish a delete that STALLS from one that is merely slow, and that
# distinction is the entire open question on schedule/BackupPolicyAssignment: its
# delete has never once been observed with its referenced volume still alive and a
# window long enough to finish in.
DELETE_VERIFY_SECONDS = int(os.environ.get("DELETE_VERIFY_SECONDS", "180"))


def kubectl(*args, check=False, stdin=None):
    return subprocess.run(
        ["kubectl", "--context", CONTEXT, *args],
        capture_output=True, text=True, check=check, input=stdin,
    )


_TOKEN = None


def token(force=False):
    """Read the ESO-managed Secret, which is the only copy anything keeps fresh.

    A file under /tmp is a manual-bootstrap artefact and goes stale the moment ESO
    rotates; preferring it produced 401s against an otherwise healthy cluster.

    Cached, because every API call asks for it; `force=True` goes back to the Secret
    for a token ESO has since refreshed. A chain can easily outlive one token -- a
    billable run died of exactly that, every delete and the whole residue check
    answering 401, which left a 20 GB volume's fate unknown until it was swept by
    hand. Nothing here may depend on a token minted twenty minutes ago.
    """
    global _TOKEN
    if _TOKEN is not None and not force:
        return _TOKEN
    r = kubectl("get", "secret", TOKEN_SECRET, "-n", TOKEN_SECRET_NS,
                "-o", "jsonpath={.data.token}")
    if r.returncode == 0 and r.stdout.strip():
        _TOKEN = base64.b64decode(r.stdout).decode()
        return _TOKEN
    raise SystemExit(f"no token in {TOKEN_SECRET_NS}/{TOKEN_SECRET}")


def api(method, path, body=None, _retried=False):
    req = urllib.request.Request(
        f"{API}{path}{'&' if '?' in path else '?'}api-version=1.0",
        method=method,
        headers={"Authorization": f"Bearer {token()}",
                 "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        if e.code == 401 and not _retried:
            # The token expired under us. ESO keeps the Secret current, so read it
            # again and replay the call once; a second 401 is a real authz failure.
            print("    401 — re-reading the ESO Secret and retrying once",
                  file=sys.stderr)
            token(force=True)
            return api(method, path, body, _retried=True)
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        # TRANSPORT failure, not an answer from Aruba. Returning 0 lets the polling
        # loops treat it as "ask again" instead of a verdict, and keeps it clearly
        # distinct from any real status code.
        #
        # This exists because a single `TimeoutError: The read operation timed out` on
        # one SSL read killed a 20-minute billable run mid-drift-poll: urlopen raises
        # that outside HTTPError, nothing caught it, and the VpcPeeringRoute verdict
        # was lost. The teardown still ran, so nothing leaked -- but the run had to be
        # paid for twice.
        print(f"    transport error on {method} {path.rsplit('/', 1)[-1]}: "
              f"{type(e).__name__} — will retry", file=sys.stderr)
        return 0, None


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
            # Print everything the CR knows, not just the condition MESSAGE. A
            # Registry run reported `TIMEOUT: ... never became Ready — ` with nothing
            # after the dash, because the only condition was Creating and its message
            # is empty: the output discarded the reason, the elapsed time and the
            # whole status block, and so could not distinguish "this resource failed"
            # from "TIMEOUT is shorter than this resource takes". It was the latter --
            # Registry fails at ~1818s against a 420s default -- and that is exactly
            # the defect this harness has now hit three times: a diagnostic that drops
            # the one fact needed to read it.
            conds = kubectl("get", f"{kind}.{GROUP}", name, "-n", NS, "-o",
                            "jsonpath={range .status.conditions[*]}"
                            "{.type}={.status}({.reason}): {.message}; {end}").stdout
            st = kubectl("get", f"{kind}.{GROUP}", name, "-n", NS, "-o",
                         "json").stdout
            extra = ""
            try:
                status = (json.loads(st) or {}).get("status") or {}
                rest = {k: v for k, v in status.items() if k != "conditions"}
                if rest:
                    extra = f"\n      status (minus conditions): {json.dumps(rest)[:400]}"
            except Exception:
                pass
            print(f"    TIMEOUT: {kind}/{name} never became Ready after {TIMEOUT}s"
                  f"\n      conditions: {conds.strip()[:400] or '(none reported)'}"
                  f"{extra}"
                  f"\n      NOTE: this is a TIMEOUT, not a terminal failure. Raise "
                  f"TIMEOUT if the resource is still provisioning — Registry reaches "
                  f"Failed at ~1818s and KaasBackup at ~1637s, both beyond the 420s "
                  f"default.", file=sys.stderr)
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


# A write is refused while the resource is still settling, with
#
#   400 {"field": "Status", "message": "invalid status"}
#
# which is indistinguishable from a rejected body if you only look at the status code.
# That is what made SecurityRule read as a resource whose drift cannot be tested for
# this entire campaign: the very same body that gets a 400 while the rule is Updating
# gets a 200 once it is Active. Confirmed by hand -- created a rule over the API,
# PUT the declared spec (200), PUT it again immediately (400 invalid status), waited
# for Active, PUT the identical body again (200).
#
# Aruba's own DELETE is refused the same way while a child is still going, so this is
# the API's general convention and not a quirk of one endpoint.
SETTLED = {"Active", "Created", "Available", "Running"}
BUSY = {"Creating", "InCreation", "Updating", "Deleting", "Pending", "Provisioning"}


def busy_refusal(resp):
    """True when a 400 means "try again later", not "send a different body".

    Both key spellings, deliberately. This API uses at least three error envelopes:
    network and storage send `errors: [{field, message}]`, the container provider
    sends `errors: [{fieldName, errorMessage}]`, and the 1.1 compute endpoint matches
    neither. Keying on `field` alone made this return False for every container
    resource, so the retry below never fired and Registry's drift was reported as
    "400 for every body shape tried" when the real answer was "ask again in a minute".
    The message casing differs too ("invalid status" vs "Invalid status"), so match
    the field name, not the prose.
    """
    if not isinstance(resp, dict):
        return False
    return any(isinstance(e, dict)
               and (e.get("field") == "Status" or e.get("fieldName") == "Status")
               for e in resp.get("errors") or [])


SETTLE_TIMEOUT = int(os.environ.get("SETTLE_TIMEOUT", "420"))


def wait_settled(url, timeout=None):
    """Poll until the resource is in a state that accepts writes.

    Unknown states are treated as settled: an unrecognised state must not silently
    cost us the drift assertion, and a refused PUT is retried anyway.
    """
    timeout = SETTLE_TIMEOUT if timeout is None else timeout
    deadline = time.time() + timeout
    started, seen = time.time(), None
    while True:
        code, body = api("GET", url)
        if code == 0:                      # transport blip: ask again
            time.sleep(10)
            continue
        if code != 200 or not isinstance(body, dict):
            return code, body
        state = (body.get("status") or {}).get("state")
        # Log every TRANSITION. Without this the probe reports only its own verdict
        # and the state that produced it is lost: KaasBackup's drift came back as "400
        # for every body shape" with no record of what the resource was doing, which
        # cannot distinguish "still settling" from "settled and immutable" from
        # "failed". Printing on change costs one line per transition and is the
        # difference between a measurement and a guess.
        if state != seen:
            print(f"    state {url.rsplit('/', 1)[-1]}: "
                  f"{seen or '(first read)'} -> {state} at t+{time.time()-started:.0f}s")
            seen = state
        if state not in BUSY:
            return code, body
        if time.time() >= deadline:
            print(f"    drift: {url.rsplit('/', 1)[-1]} still {state} after {timeout}s",
                  file=sys.stderr)
            return code, body
        time.sleep(10)


# The update method is DERIVED from the vendored OAS, not assumed to be PUT.
#
# This machinery hardcoded `api("PUT", ...)` at both drift call sites. Across the whole
# vendored set, 29 item paths declare PUT and exactly ONE declares PATCH with no PUT:
# `Aruba.Compute/cloudServers/{cloudServerId}`. So CloudServer's drift was PUTting to a
# PATCH-only endpoint, and Aruba answered
#     400 {"errors":[{"fieldName":"Flavor","errorMessage":"Please specify flavor."},
#                    {"fieldName":"Product","errorMessage":"not found"}]}
# which is CREATE-body validation -- the PUT was reaching a create-like handler. I read
# those field names as an update defect in Aruba's API and was one command away from
# filing it upstream, on three servers' worth of evidence for the wrong conclusion.
# The error named the cause and I read it as the symptom.
_OAS_CACHE = {}


def _oas_docs():
    """Every vendored OAS document, loaded once."""
    if not _OAS_CACHE:
        for f in sorted(glob.glob(os.path.join(ROOT, "openapi", "*.json"))):
            try:
                _OAS_CACHE[f] = json.load(open(f))
            except Exception:
                continue
    return _OAS_CACHE


def update_method(url):
    """PUT or PATCH for this concrete URL, per the OAS. None if it declares neither.

    Matches a concrete path against the templated ones: same segment count, and each
    OAS segment either equals the concrete segment or is a `{placeholder}`. Returns
    PUT when both exist, since that is what 29 of the 30 resources use and what every
    existing drift assertion was proven against.
    """
    concrete = [seg for seg in url.split("?")[0].split("/") if seg]
    for doc in _oas_docs().values():
        for tmpl, ops in (doc.get("paths") or {}).items():
            segs = [seg for seg in tmpl.split("/") if seg]
            if len(segs) != len(concrete):
                continue
            if not all(t.startswith("{") or t == c for t, c in zip(segs, concrete)):
                continue
            verbs = {v.lower() for v in ops}
            if "put" in verbs:
                return "PUT"
            if "patch" in verbs:
                return "PATCH"
    return None


def _drift_explicit(kind, name, url, body, assertion):
    """Inject drift with an explicit PUT body and converge on one named field.

    `assertion` is {"path": "<dotted path>", "equals": <value>} -- the value the
    controller must restore. Both are required: a probe that injects without
    asserting cannot tell correction from the API quietly ignoring the write.
    """
    if not assertion or "path" not in assertion or "equals" not in assertion:
        print(f"    drift: {kind}/{name} declares driftBody without a complete "
              f"driftAssert — skipped", file=sys.stderr)
        return None
    path, want = assertion["path"], assertion["equals"]

    def read(doc):
        cur = doc
        for part in path.split("."):
            if not isinstance(cur, dict):
                return None
            cur = cur.get(part)
        return cur

    _m = update_method(url) or "PUT"
    if _m != "PUT":
        print(f"    drift: using {_m} — the OAS declares no PUT for this path")
    code, resp = None, None
    for _ in range(3):
        code, resp = api(_m, url, body)
        if code in (200, 202, 204):
            break
        if not busy_refusal(resp):
            break
        wait_settled(url, 120)
    if code not in (200, 202, 204):
        print(f"    drift: PUT returned {code} for {kind}/{name} — cannot inject",
              file=sys.stderr)
        return None

    # Prove the write landed. Without this an API that accepts and ignores the PUT
    # looks identical to a controller that corrected it instantly, and the check
    # passes having tested nothing -- the same vacuous pass oasgen#76 produced.
    code, after = api("GET", url)
    if code == 200 and isinstance(after, dict) and read(after) == want:
        print(f"    drift: PUT was accepted but {path} is still {want!r} — the API "
              f"ignored the write, nothing to correct", file=sys.stderr)
        return None
    print(f"    drift injected via driftBody ({path}={read(after)!r}); "
          f"waiting for correction to {want!r}")

    kubectl("annotate", f"{kind}.{GROUP}", name, "-n", NS,
            f"drift-probe={int(time.time())}", "--overwrite")
    deadline, cur = time.time() + DRIFT_TIMEOUT, None
    while time.time() < deadline:
        code, doc = api("GET", url)
        if code == 200 and isinstance(doc, dict):
            cur = read(doc)
            if cur == want:
                print(f"    DRIFT CORRECTED for {kind}/{name}")
                return True
        time.sleep(20)
    print(f"    DRIFT NOT CORRECTED for {kind}/{name} ({path} still {cur!r})",
          file=sys.stderr)
    return False


def drift_check(kind, name, url, spec=None, drift_body=None, drift_assert=None):
    """Set a tag upstream and require the controller to remove it.

    A chain may instead declare `driftBody` + `driftAssert`, which replaces the tag
    trick with an explicit PUT. That is not a convenience: the tag trick assumes the
    resource HAS `metadata.tags`, and the flat project-provider resources do not.
    `FolderUpdateDto` is `{name, default}` with `additionalProperties: false`, so
    adding a `metadata` key is a 400 and the probe reported drift as untestable --
    which is how Folder sat at beta on the claim that its only convergeable field was
    the account-wide `default` switch. It is not: Folder's get/update/delete all build
    their path from `status.id`, never from `name`, so `name` is an ordinary mutable
    field that can be perturbed and converged without touching `default` at all.

    Deliberately the scenario oasgen-provider#76 got wrong: the CR declares
    `tags: []`, and before 0.22.1 an empty list matched any remote list, so this
    assertion passed vacuously while the resource stayed diverged.
    """
    # Same derivation as _drift_explicit: PUT is what 29 of 30 resources declare,
    # but CloudServer declares PATCH and no PUT, and sending PUT there reaches a
    # create-like handler that answers 400 about a missing Flavor.
    _m = update_method(url) or "PUT"
    if _m != "PUT":
        print(f"    drift: using {_m} — the OAS declares no PUT for this path")
    # Ready on the CR does not mean writable upstream: the controller reports Ready as
    # soon as its observe succeeds, which can be while the resource is still Updating.
    code, body = wait_settled(url)
    if code != 200 or not isinstance(body, dict):
        print(f"    drift: cannot read {kind}/{name} (HTTP {code}) — skipped", file=sys.stderr)
        return None

    if drift_body is not None:
        return _drift_explicit(kind, name, url, drift_body, drift_assert)

    # Build the PUT body from the DECLARED SPEC where we have one, not from the GET
    # response. A response is not necessarily a valid request: Dbaas returns
    # properties.networking as {vpc:{uri}, subnet:{uri}, securityGroup:{uri}} while the
    # update accepts {vpcUri, subnetUri, securityGroupUri}. Echoing the response back
    # therefore reads as setting Vpc to null, and Aruba answers
    #
    #   400 "The field 'Vpc' cannot be modified after the DBaaS has been created."
    #
    # for Vpc, SecurityGroup and Subnet at once -- a create-only-field error for fields
    # the caller never meant to touch. Omitting `networking` does not help (same error,
    # it reads as nulling them) and metadata alone returns 500. Sending the spec's own
    # networking block, which is already in request shape, returns 200 and the
    # controller then converges the tag. Confirmed by hand against a live Active Dbaas.
    #
    # This probe injects drift, so it must send something the API accepts; the declared
    # spec is the only thing guaranteed to be in request shape, being what create sent.
    # The response echo stays as the fallback for resources a chain declares no spec for.
    # Neither shape is universally correct, so try both. Dbaas accepts only the spec
    # shape; SecurityRule accepts only the echo (its declared spec omits fields the
    # update requires, and sending just metadata+properties is a 400). Picking one
    # would silently stop testing drift on whichever resources disagree -- which is
    # exactly how Dbaas drift sat SKIPPED for the whole campaign while reading like a
    # resource limitation rather than a probe bug.
    candidates = []
    if spec and ("metadata" in spec or "properties" in spec):
        candidates.append(("declared spec",
                           {k: json.loads(json.dumps(v)) for k, v in spec.items()
                            if k in ("metadata", "properties")}))
    echo = json.loads(json.dumps(body))
    echo.pop("status", None)
    emd = echo.setdefault("metadata", {})
    for k in ("id", "uri", "creationDate", "updateDate", "createdBy",
              "updatedBy", "project", "category", "version"):
        emd.pop(k, None)
    candidates.append(("response echo", echo))
    for _, cand in candidates:
        cand.setdefault("metadata", {})["tags"] = ["drifted-by-hand"]

    # 202 Accepted is a normal answer here: storage updates are asynchronous, and
    # treating anything but 200 as a failure silently skipped the drift check on every
    # resource whose update is queued rather than applied inline.
    shape, code, detail = None, None, None
    for label, cand in candidates:
        for _ in range(3):
            code, resp = api(_m, url, cand)
            if code in (200, 202, 204):
                break
            detail = resp
            if not busy_refusal(resp):
                break   # a real rejection: try the next shape, not the same one again
            wait_settled(url, 120)
        if code in (200, 202, 204):
            shape = label
            break
    if shape is None:
        # The field-level errors, not just the code. Aruba answers RFC7807 with
        # `errors: [{field, message}]` -- NOT fieldName/errorMessage, which is what
        # this printed before and why a real "Status: invalid status" came out as
        # "None: None" and sent me looking for a body-shape bug that was not there.
        errs = ""
        if isinstance(detail, dict) and detail.get("errors"):
            errs = " — " + "; ".join(
                f"{e.get('field') or e.get('fieldName')}: "
                f"{e.get('message') or e.get('errorMessage')}"
                for e in detail["errors"][:3] if isinstance(e, dict))
        elif isinstance(detail, dict) and detail.get("title"):
            errs = " — " + str(detail["title"])
        tried = ", ".join(l for l, _ in candidates)
        _, now = api("GET", url)
        st = (now or {}).get("status") if isinstance(now, dict) else None
        cur = st.get("state") if isinstance(st, dict) else st
        print(f"    drift: PUT returned {code} for every body shape tried ({tried})"
              f"{errs} — cannot inject into {kind}/{name} (state at refusal: {cur})",
              file=sys.stderr)
        return None
    # CONFIRM THE INJECTION LANDED BEFORE WAITING FOR IT TO BE UNDONE.
    #
    # Without this the check cannot fail. It sends a PATCH, gets a 2xx, and then polls
    # until `tags == []` -- so an API that ACCEPTS the write and silently does not
    # persist the tag makes the very first poll succeed, and the probe reports
    # DRIFT CORRECTED for a correction that never happened because a drift that never
    # happened. `_drift_explicit`'s own docstring already said this -- "a probe that
    # injects without asserting cannot tell correction from the API quietly ignoring
    # the write" -- and the tag-trick path, which is the one 27 GA resources use, had
    # no such assertion.
    #
    # Caught on CloudServer: the run reported DRIFT CORRECTED while snowplow's log
    # showed the reconciling stage making ZERO api calls. Nothing corrected anything;
    # the tag was never there to correct. That result was one commit away from being
    # recorded as GA evidence.
    #
    # A short poll, not a single read: the write may be asynchronous, which is why 202
    # is accepted above. Not finding the tag is NOT a pass and NOT a failure of the
    # controller -- it is the probe being unable to create the condition it tests, so
    # it returns None (skipped) and says which shape was accepted.
    injected = False
    for _ in range(6):
        code, now = api("GET", url)
        if code == 200 and isinstance(now, dict):
            if "drifted-by-hand" in ((now.get("metadata") or {}).get("tags") or []):
                injected = True
                break
        time.sleep(5)
    if not injected:
        print(f"    drift: {shape} was ACCEPTED ({code}) but the tag did not persist "
              f"for {kind}/{name} — the API took the write and dropped it, so there is "
              f"no drift to correct and this proves NOTHING about convergence. Not a "
              f"controller failure; the probe could not create the condition.",
              file=sys.stderr)
        return None
    print(f"    drift injected via {shape} (tags=[drifted-by-hand]) and CONFIRMED "
          f"present upstream; waiting for correction")

    kubectl("annotate", f"{kind}.{GROUP}", name, "-n", NS,
            f"drift-probe={int(time.time())}", "--overwrite")
    deadline = time.time() + DRIFT_TIMEOUT
    while time.time() < deadline:
        code, cur = api("GET", url)
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
    code, _ = api("GET", "/projects")
    if code != 200:
        raise SystemExit(f"token is not usable (HTTP {code})")
    print(f"token OK — chain '{chain['name']}'")

    ids, created, drift_results = {}, [], {}
    secrets_created = []
    self_urls = {}

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

    drift_rc = 0
    teardown_rc = 0
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
            # A field sourced through the secretRef resolver has no spec form at all,
            # so the chain must place the Secret itself. Created BEFORE the CR: the
            # controller resolves it on the create call, and a Secret arriving second
            # would make the first attempt fail for a reason that looks like the API
            # rejecting the value.
            sec = res.get("secret")
            if sec:
                sname = expand(sec["name"], variables, ids, kind)
                skey = expand(sec["key"], variables, ids, kind)
                svalue = expand(sec["value"], variables, ids, kind)
                print(f"--- creating Secret/{sname} (key {skey}) for {kind}/{name}")
                r = kubectl("create", "secret", "generic", sname, "-n", NS,
                            f"--from-literal={skey}={svalue}",
                            "--dry-run=client", "-o", "yaml")
                if r.returncode != 0:
                    raise RuntimeError(f"secret render failed: {r.stderr.strip()[:200]}")
                secrets_created.append(sname)     # registered BEFORE applying
                r = kubectl("apply", "-f", "-", stdin=r.stdout)
                if r.returncode != 0:
                    raise RuntimeError(f"secret apply failed: {r.stderr.strip()[:200]}")

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

            # Its own URL, for the per-resource delete check in teardown. A chain
            # states it as `self:`, or gets it free from `drift:`, which is the same
            # path. Stored EXPANDED, here, because `${id:self}` only means anything
            # while `kind` is in scope -- expanding it later in teardown raised
            # `${id:self} used before None was created` and aborted the teardown with
            # a live Dbaas still running. Resolve it where the context exists.
            self_ep = res.get("self") or res.get("drift")
            if self_ep:
                self_urls[name] = expand(self_ep, variables, ids, kind)

            if res.get("drift"):
                url = expand(res["drift"], variables, ids, kind)
                drift_results[f"{kind}/{name}"] = drift_check(
                    kind, name, url, spec,
                    drift_body=expand(res.get("driftBody"), variables, ids, kind)
                    if res.get("driftBody") is not None else None,
                    drift_assert=res.get("driftAssert"))

        print(f"\nRESULT: {len(created)} resources created and observed")
        for k, v in drift_results.items():
            print(f"  drift {k}: {'ok' if v else ('SKIPPED' if v is None else 'FAILED')}")
        # NOT `return`: the teardown in the finally below computes its own
        # verdict, and returning here would run it and then discard it.
        drift_rc = 0 if all(v is not False for v in drift_results.values()) else 1

    finally:
        if "--keep" in flags:
            # Deliberately not `return`: returning from a finally block swallows an
            # exception that was propagating, turning a failed run into a silent pass.
            print("\n--keep: leaving resources in place; they may bill")
            created = []
            secrets_created = []

        # Deleted after the CRs, and unconditionally: a Secret left behind holds a
        # credential, so it is removed even on the paths where the CR teardown is
        # skipped or fails.
        def _drop_secrets():
            for sname in secrets_created:
                kubectl("delete", "secret", sname, "-n", NS, "--ignore-not-found")
            if secrets_created:
                print(f"    removed {len(secrets_created)} chain Secret(s)")

        # Deletes that could not be proven. A teardown that leaves a billable
        # resource wedged MUST NOT exit 0: this chain reported exactly that tonight
        # -- CloudServer and BlockStorage both printed DELETE NOT CONFIRMED, the
        # residue check then said "clean", and the run exited 0 with a live server on
        # the account. CI would have read it as a pass.
        unconfirmed = []          # (kind, name, url, crstate)
        # Deletes where the resource ended up gone but this controller cannot be
        # credited for it. Not a pass and not the same failure as `unconfirmed`:
        # the cleanup worked, the PROOF did not.
        unattributed = []         # (kind, name, url)

        if created:
            print("\n=== teardown (reverse dependency order) ===")
            for kind, name in reversed(created):
                print(f"--- deleting {kind}/{name}")
                r = kubectl("delete", f"{kind}.{GROUP}", name, "-n", NS,
                            "--wait=true", f"--timeout={TIMEOUT}s")
                out = (r.stdout + r.stderr).strip().splitlines()
                print("   ", out[-1][:160] if out else "(no output)")
                # Did the CONTROLLER release the finalizer within the timeout? If
                # `kubectl delete --wait` timed out, it did not -- and a later 404 then
                # proves only that the resource is GONE, not that this controller
                # removed it. Tonight that distinction was the entire finding: the
                # delegated delete sent nothing across 23 attempts, I removed the server
                # by hand to stop it billing, the verify loop saw 404 on its 7th check,
                # and the run printed "delete verified" and exited 0. The harness
                # recorded the defect it had just produced as a success.
                wait_timed_out = "timed out waiting for the condition" in (
                    r.stdout + r.stderr)

                # Prove the DELETE individually, here, while this resource's parent is
                # still alive. The residue check at the end cannot do it: by then the
                # parent is gone too, and a child that vanished with its parent is
                # indistinguishable from one the controller actually deleted. That
                # distinction is the whole difference between "delete proven" and
                # "delete assumed" for every resource that hangs off another.
                #
                # Wrapped, and deliberately so: the first version of this check raised
                # on a bad path expansion and ABORTED THE REST OF THE TEARDOWN, leaving
                # a live Dbaas billing. A verification step must never be able to stop
                # the deletions it is verifying -- the whole point of this block is to
                # report, and a reporter that can halt the cleanup is worse than no
                # reporter at all.
                try:
                    url = self_urls.get(name)
                    if not url:
                        continue
                    deadline = time.time() + DELETE_VERIFY_SECONDS
                    attempt, confirmed = 0, False
                    while time.time() < deadline:
                        attempt += 1
                        code, gbody = api("GET", url)
                        if code == 404:
                            after = (f" (after {attempt} checks)" if attempt > 1 else "")
                            if wait_timed_out:
                                # Gone, but NOT attributable to this controller.
                                print(f"    delete UNATTRIBUTED: {url} -> 404{after}, "
                                      f"but `kubectl delete --wait` had already TIMED OUT "
                                      f"— the finalizer was not released in time, so this "
                                      f"404 does not prove the controller deleted it. "
                                      f"Something removed it while we waited.",
                                      file=sys.stderr)
                                unattributed.append((kind, name, url))
                            else:
                                print(f"    delete verified: {url} -> 404{after}")
                            confirmed = True
                            break
                        # A TOMBSTONE counts as gone. security/Kmip answers GET with
                        # 200 after a successful delete, carrying `status: Deleted` and
                        # a `deletionDate` a live read does not have, while vanishing
                        # from its own collection -- including with
                        # includeDeleted=true. Demanding a 404 here would call that an
                        # unconfirmed delete forever, which is the same mistake RDC
                        # made upstream (oasgen#111). The resource is absent by the
                        # API's own accounting; only the status code disagrees.
                        if code == 200 and isinstance(gbody, dict):
                            # `status` is a bare string on the security provider and
                            # an object with `.state` elsewhere, so read both shapes.
                            st = gbody.get("status")
                            state = st if isinstance(st, str) else (
                                st.get("state") if isinstance(st, dict) else None)
                            if isinstance(state, str) and state.lower() == "deleted":
                                print(f"    delete verified: {url} -> 200 tombstone "
                                      f"(status={state}"
                                      + (", deletionDate set"
                                         if gbody.get("deletionDate") else "")
                                      + ")"
                                      + (f" after {attempt} checks" if attempt > 1
                                         else ""))
                                confirmed = True
                                break
                        if code == 0:            # transport blip: ask again
                            time.sleep(15)
                            continue
                        if code != 200:
                            print(f"    delete UNVERIFIED: {url} -> HTTP {code}",
                                  file=sys.stderr)
                            confirmed = True       # answered, just not with a body
                            break
                        time.sleep(15)
                    if not confirmed:
                        mins = DELETE_VERIFY_SECONDS / 60
                        # Say what the CR is actually doing rather than assuming it
                        # went. This line used to read "the CR is gone but the
                        # resource is not" without ever checking, and on Image it was
                        # simply false: the CR was still there, Terminating, with its
                        # finalizer held -- which is the OPPOSITE failure (a wedged
                        # finalizer, not a silent orphan) and wants the opposite fix.
                        # An unverified claim in a teardown report is worse than no
                        # claim: this is the output someone reads at 2am.
                        cr = kubectl("get", f"{kind}.{GROUP}", name, "-n", NS, "-o",
                                     "jsonpath={.metadata.deletionTimestamp}|"
                                     "{.metadata.finalizers}")
                        if cr.returncode != 0:
                            crstate = "the CR is gone — the resource is ORPHANED"
                        else:
                            ts, _, fin = (cr.stdout or "").partition("|")
                            crstate = (f"the CR is STILL PRESENT, deletionTimestamp="
                                       f"{ts or '(none)'} finalizers={fin or '[]'} "
                                       f"— it is wedged, not orphaned")
                        print(f"    DELETE NOT CONFIRMED after {mins:.0f} min: {url} "
                              f"still reads 200 — {crstate}", file=sys.stderr)
                        unconfirmed.append((kind, name, url, crstate))
                except Exception as e:                      # never block the teardown
                    print(f"    delete check errored for {kind}/{name}: {e}",
                          file=sys.stderr)

            print("\n=== residue check (ground truth, async deletion allowed for) ===")
            # Match leftovers by ID, not by name. A fixture name is not unique on the
            # account: a BackupPolicyAssignment called `ga-bpa` has been wedged in
            # `Deleting` since 2026-09-04, so a name-keyed check reported `leftovers =
            # 1` against a run that created nothing at all -- blaming this run for a
            # seven-week-old corpse, and in the other direction it would have hidden a
            # real leak behind a same-named stranger. Only a resource whose id this run
            # recorded is this run's to answer for.
            #
            # Name matching survives as a fallback for resources that reached the API
            # but whose id was never captured (a create that timed out before the id
            # could be read); there, a same-named pre-existing resource is the lesser
            # risk against missing a genuine leak.
            names = {n for _, n in created}
            created_ids = {ids[n] for _, n in created if ids.get(n)}
            nameless = {n for _, n in created if not ids.get(n)}
            unverified = False
            for attempt in range(1, 7):
                left = []
                for ep in chain.get("residue", []):
                    url = expand(ep, variables, ids)
                    code, body = api("GET", url)
                    if code != 200 or not isinstance(body, dict):
                        # Do NOT pass silently. An endpoint that cannot be read proves
                        # nothing, and counting it as zero leftovers turns a dead token
                        # or a typo'd path into a clean bill of health for billable
                        # resources that are still running.
                        #
                        # One exception, and only one: a 404 on a CHILD collection
                        # whose parent this chain just deleted. A backup cannot outlive
                        # the cluster it hangs off, so that 404 is the deletion being
                        # confirmed, not a reading that failed. Anything else -- a
                        # different status, or a 404 on a path with no deleted parent in
                        # it (a typo, say) -- still refuses to call the run clean.
                        #
                        # Known gap, stated rather than hidden: a MISSPELLED path that
                        # still contains a deleted parent's id is excused by this rule.
                        # Narrowing it would mean teaching the chain which collections
                        # exist, which is the OAS's job; the residue check is a
                        # backstop, not a schema validator.
                        parent_gone = code == 404 and any(
                            rid and f"/{rid}" in url for name, rid in ids.items()
                            if name in names)
                        if parent_gone:
                            print(f"    {url}: 404 — parent deleted, nothing can "
                                  f"remain under it")
                        else:
                            print(f"    UNVERIFIED: {url} returned HTTP {code}",
                                  file=sys.stderr)
                            unverified = True
                        continue
                    vals = body.get("values") or body.get("value") or []
                    for v in vals:
                        md = v.get("metadata") or {}
                        vid = md.get("id") or v.get("id")
                        vname = md.get("name") or v.get("name")
                        if vid and vid in created_ids:
                            left.append(v)
                            print(f"    leftover: {vname} id={vid}")
                        elif vname in nameless:
                            left.append(v)
                            print(f"    leftover (id never recorded): {vname}")
                print(f"    attempt {attempt}: leftovers = {len(left)}")
                if not left:
                    print("    clean" if not unverified else
                          "    no leftovers among the endpoints that answered, but at "
                          "least one could not be read — NOT a clean bill")
                    break
                time.sleep(20)
            else:
                print("    WARNING: resources may remain — check the Aruba console", file=sys.stderr)

            # CROSS-CHECK the two instruments against each other. The per-resource
            # check above does an item GET while the parent is alive; the residue
            # check lists collections at the end. When a resource fails the first and
            # is absent from the second, the two DISAGREE -- and tonight the
            # optimistic one won silently: two resources printed DELETE NOT CONFIRMED
            # and the residue check still said "clean".
            #
            # Which reading was right is not decidable from here, so this does not
            # pick one. It reports the disagreement, because a run where two
            # instruments contradict each other has not established anything -- and
            # "clean" from the one that looked second is the least safe thing to
            # believe about a billable resource.
            if unconfirmed:
                left_ids = {((v.get("metadata") or {}).get("id")) for v in left}
                for kind, name, url, crstate in unconfirmed:
                    rid = ids.get(name)
                    if not rid or rid in left_ids:
                        continue
                    # The two instruments ran MINUTES APART -- the per-resource check
                    # during this resource's teardown, the residue listing after every
                    # other resource went. A resource deleted in between differs for
                    # them legitimately, and the first version of this check called
                    # that "one of the two is wrong". It happened on the very next run:
                    # the item GET read 200 at ~23:32 and the listing was empty at
                    # ~23:41, nine minutes later, because the delete had succeeded in
                    # between. So RE-READ THE ITEM NOW and compare like with like.
                    code_now, _ = api("GET", url)
                    if code_now == 404:
                        print(f"    resolved since teardown: {kind}/{name} (id={rid}) "
                              f"read 200 during its own delete but is 404 now — it went "
                              f"after that check. Still not attributable to the "
                              f"controller, but it is GONE.")
                        continue
                    if code_now != 200:
                        print(f"    {kind}/{name}: item GET now HTTP {code_now} — "
                              f"cannot compare instruments; treat as unread.",
                              file=sys.stderr)
                        continue
                    print(f"    INSTRUMENT DISAGREEMENT for {kind}/{name} (id={rid}): "
                          f"its item GET still reads 200 RIGHT NOW, but the residue "
                          f"listing does not report it. Both were read at the same "
                          f"time, so one of them is wrong and this run proves nothing "
                          f"about this resource. Check the console by id.",
                          file=sys.stderr)

        _drop_secrets()

        if unattributed:
            print(f"\n=== {len(unattributed)} DELETE(S) UNATTRIBUTED — the resource is "
                  f"gone but this controller is NOT proven to have removed it ===",
                  file=sys.stderr)
            for kind, name, url in unattributed:
                print(f"    {kind}/{name}: {url}", file=sys.stderr)
            print("    Nothing is billing, so this is not a cleanup failure. It is an "
                  "EVIDENCE failure: do not record these as a proven delete.",
                  file=sys.stderr)

        if unconfirmed or unattributed:
            print(f"\n=== run FAILED: {len(unconfirmed)} unconfirmed, "
                  f"{len(unattributed)} unattributed ===", file=sys.stderr)
            for kind, name, url, crstate in unconfirmed:
                print(f"    unconfirmed  {kind}/{name}: {url}", file=sys.stderr)
            for kind, name, url in unattributed:
                print(f"    unattributed {kind}/{name}: {url}", file=sys.stderr)
            # SET, never return. This block's own comment thirty lines up says that
            # returning from a `finally` swallows a propagating exception and turns a
            # failed run into a silent pass -- I wrote that warning and then added a
            # `return 1` here anyway. Python 3.14 flagged it as a SyntaxWarning during
            # a live billable run, which is the only reason I saw it.
            teardown_rc = 1

    # Outside the try/finally: an exception from the try has already propagated past
    # here with its traceback intact, which is the whole point of not returning above.
    return max(drift_rc, teardown_rc)


if __name__ == "__main__":
    sys.exit(main() or 0)
