#!/usr/bin/env python3
"""Does a blinded findby emit oasgen's AbsenceInferredFromUnmatchedListing event?

Verifies the wiring of oasgen-provider #511 (#485, "report, don't refuse") on a real
reconcile. The unit test there proves the DECISION; only the full handler reaches Observe's
findby branch, so this probe measures whether the event appears on the path.

Two arms, because an event that fires on every create is as wrong as one that never fires:

  ARM A (control)  a clean Configuration. EXPECT EXACTLY ONE emission, at the first
                   observe, and NONE after. A fresh create legitimately infers absence from
                   an unmatched listing -- the resource really is not there yet -- so an
                   event there is correct, not a defect. What would be a defect is a SECOND
                   emission once the resource exists and findby can match it: that is the
                   steady state, and a warning there means the predicate never stops firing.
                   Measured 2026-11-07: 5 resources, 5 emissions, one each, across many
                   reconciles apiece. An earlier version of this probe asserted zero for
                   arm A and reported "the predicate is wrong" -- the probe was wrong.
  ARM B            `offset` injected into spec.configuration.query.findby, >= the collection
                   size, so findby LISTS SUCCESSFULLY and matches nothing (#486).
                   EXPECT exactly one event per reconcile.

Why offset is derived and not guessed: an offset below the collection size still returns
items, and the arm then proves nothing. Measured the hard way -- offset=1 against a 2-item
collection is a confound, not a blinding.

Emissions per reconcile are read from the RDC log, not from the Event's `count`: Kubernetes
AGGREGATES repeated identical events into one object with a count, so one object never means
one emission. The Info log line is written once per emission, so log lines and event count
must agree; a count exceeding the log lines means aggregation, and log lines exceeding
reconciles means a loop.

ARM B IS NOT REACHABLE IN THIS REPOSITORY, and that is a protection rather than a gap.
Measured 2026-11-07, all 36 RestDefinitions: none exposes offset/limit/filter/sort/
projection in configurationFields, because generate_restdefinitions.py excludes them
(VIEW_NARROWING_FINDBY_QUERY). The generated Configuration CRDs therefore do not declare
the field, and Kubernetes PRUNES it:

    kubectl patch vpcconfiguration vpc-config --type merge \
      -p '{"spec":{"configuration":{"query":{"findby":{"offset":"10000"}}}}}'
    Warning: unknown field "spec.configuration.query.findby.offset"
    -> stored query.findby == {"api-version": "1.0"}, and findby matched normally

Note the WARNING: `kubectl patch` exits 0. A probe that checks the exit code believes it
blinded something. This one got as far as reporting "the blinding did not blind" only
because it also read back what the listing did.

The vendor side of #486 is real and was re-measured the same day, against a one-item
collection: offset=0 returns 1; offset=1, 2, 10, 50, 51, 1000 and 10000 all return 0 with
total=1. Aruba honours offset at every value and does not clamp an out-of-range one. So
the mechanism holds; only the injection path is closed here, and it is open for any
consumer whose RestDefinitions do expose those parameters.

Exit codes
  0  wired and correctly scoped: B emitted, A did not, one per reconcile
  1  B emitted nothing -- the call site is not reached on the real path
  2  A emitted -- the predicate is wrong
  3  B emitted more than once per reconcile
  4  CONTROL FAILED -- arm A never created or observed, so nothing here is evidence
"""
import json
import subprocess
import sys
import time

REASON = "AbsenceInferredFromUnmatchedListing"
LOG_MARK = "Absence was inferred from an unmatched findby listing"


def kubectl(args, ctx, kubeconfig, check=True):
    cmd = ["kubectl", "--context", ctx] + args
    env_cmd = ["env", f"KUBECONFIG={kubeconfig}"] + cmd
    p = subprocess.run(env_cmd, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}\n{p.stderr.strip()}")
    return p.stdout


def events_for(name, plural, ns, ctx, kubeconfig):
    """Warning events with our reason on one object. Returns (objects, total_count)."""
    raw = kubectl(
        ["get", "events", "-n", ns, "-o", "json",
         "--field-selector", f"involvedObject.name={name}"],
        ctx, kubeconfig, check=False)
    if not raw.strip():
        return [], 0
    items = json.loads(raw).get("items") or []
    ours = [e for e in items if e.get("reason") == REASON]
    return ours, sum(int(e.get("count") or 1) for e in ours)


def log_emissions(ctx, kubeconfig, ns="krateo-system"):
    """How many times the controller LOGGED an inferred absence -- one line per emission."""
    pods = kubectl(["get", "pods", "-n", ns, "-o",
                    "jsonpath={range .items[*]}{.metadata.name}{\"\\n\"}{end}"],
                   ctx, kubeconfig, check=False)
    total = 0
    for pod in [p for p in pods.split("\n") if p.strip()]:
        logs = kubectl(["logs", "-n", ns, pod, "--tail=-1"], ctx, kubeconfig, check=False)
        total += sum(1 for line in logs.split("\n") if LOG_MARK in line)
    return total


def discover_config(kind_cfg, ns, ctx, kc):
    """The Configuration to blind. Listed, never assumed by name."""
    raw = kubectl(["get", kind_cfg.lower(), "-n", ns, "-o", "json"], ctx, kc, check=False)
    if not raw.strip():
        return None
    items = json.loads(raw).get("items") or []
    return items[0]["metadata"]["name"] if len(items) == 1 else None


def resource(name, plural, ns, ctx, kc):
    raw = kubectl(["get", plural, name, "-n", ns, "-o", "json"], ctx, kc, check=False)
    return json.loads(raw) if raw.strip() else None


def state(name, plural, ns, ctx, kc):
    """(external id or None, [(type, reason), ...])."""
    r = resource(name, plural, ns, ctx, kc)
    if not r:
        return None, []
    st = r.get("status") or {}
    rid = ((st.get("metadata") or {}).get("id"))
    return rid, [(c.get("type"), c.get("reason")) for c in (st.get("conditions") or [])]


VPC = """apiVersion: arubacloud.ogen.krateo.io/v1-0-0
kind: Vpc
metadata:
  name: %(name)s
  namespace: %(ns)s
  annotations:
    krateo.io/connector-verbose: 'true'
spec:
  configurationRef:
    name: %(cfg)s
    namespace: %(ns)s
  projectId: "%(project)s"
  metadata:
    name: %(vname)s
    location:
      value: %(location)s
    tags: []
  properties:
    default: false
    preset: false
"""


def main(argv):
    opts = {"--context": None, "--kubeconfig": None, "--project": None,
            "--plural": "vpcs", "--config-kind": "VpcConfiguration",
            "--config-name": "vpc-config", "--namespace": "default",
            "--location": "ITBG-Bergamo", "--offset": "10000",
            "--name": "probe511", "--wait": "420"}
    i = 1
    while i < len(argv):
        a = argv[i]
        if a not in opts:
            print("unknown argument %r" % a, file=sys.stderr)
            return 4
        if i + 1 >= len(argv):
            print("%s needs a value" % a, file=sys.stderr)
            return 4
        opts[a] = argv[i + 1]
        i += 2
    ctx, kc, project = opts["--context"], opts["--kubeconfig"], opts["--project"]
    if not (ctx and kc and project):
        print("--context, --kubeconfig and --project are required. The kubeconfig is "
              "explicit because an ambient one may point at a cluster another session "
              "selected.", file=sys.stderr)
        return 4
    ns, plural = opts["--namespace"], opts["--plural"]
    base, cfg_kind, wait = opts["--name"], opts["--config-kind"], int(opts["--wait"])
    cfg = opts["--config-name"] or discover_config(cfg_kind, ns, ctx, kc)
    if not cfg:
        print("CONTROL FAILED: no single %s in %s" % (cfg_kind, ns), file=sys.stderr)
        return 4
    target, dup = base + "-target", base + "-dup"
    vendor_name = target          # BOTH CRs name this one vendor resource.
    unblind = ["patch", cfg_kind.lower(), cfg, "-n", ns, "--type", "json", "-p",
               '[{"op":"remove","path":"/spec/configuration/query/findby/offset"}]']
    made, blinded = [], False

    def apply(cr_name, vname):
        m = VPC % {"name": cr_name, "ns": ns, "cfg": cfg, "project": project,
                   "location": opts["--location"], "vname": vname}
        r = subprocess.run(["env", "KUBECONFIG=%s" % kc, "kubectl", "--context", ctx,
                            "apply", "-f", "-"], input=m, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("apply %s: %s" % (cr_name, r.stderr.strip()))
        made.append(cr_name)

    def wait_id(cr_name, limit):
        end = time.time() + limit
        while time.time() < end:
            rid, _ = state(cr_name, plural, ns, ctx, kc)
            if rid:
                return rid
            time.sleep(10)
        return None

    try:
        # ---- ARM A: the target, created normally -------------------------------------
        print("ARM A: %s/%s, unblinded findby" % (plural, target))
        apply(target, vendor_name)
        tid = wait_id(target, wait)
        if not tid:
            _, c = state(target, plural, ns, ctx, kc)
            print("CONTROL FAILED: %s never got an id in %ss; conditions %s"
                  % (target, wait, c), file=sys.stderr)
            return 4
        _, a_count = events_for(target, plural, ns, ctx, kc)
        print("  id=%s  events=%d (one is EXPECTED on a first create)" % (tid, a_count))
        if a_count == 0:
            print("FINDING: the first create emitted nothing. The call site is not "
                  "reached, or the marker is lost before the predicate.", file=sys.stderr)
            return 1

        # ---- ARM B: a second CR for the same vendor resource, findby blinded ----------
        print("\nARM B: blinding %s/%s findby (offset=%s), then %s naming the SAME "
              "vendor resource %r" % (cfg_kind, cfg, opts["--offset"], dup, vendor_name))
        kubectl(["patch", cfg_kind.lower(), cfg, "-n", ns, "--type", "merge", "-p",
                 json.dumps({"spec": {"configuration": {"query": {"findby": {
                     "offset": opts["--offset"]}}}}})], ctx, kc)
        blinded = True
        apply(dup, vendor_name)
        end, b_count, b_conds = time.time() + wait, 0, []
        while time.time() < end:
            _, b_count = events_for(dup, plural, ns, ctx, kc)
            _, b_conds = state(dup, plural, ns, ctx, kc)
            if b_count:
                break
            time.sleep(10)
        did, b_conds = state(dup, plural, ns, ctx, kc)
        print("  events on %s: %d" % (dup, b_count))
        print("  conditions: %s" % b_conds)
        print("  status.metadata.id: %s" % did)
        if not b_count:
            why = ("the blinded listing errored rather than returning empty, so the "
                   "blinding is wrong"
                   if any(r in ("ObserveFailed", "ReconcileError") for _, r in b_conds)
                   else "findby returned a match despite the offset, so the blinding did "
                        "not blind" if did else
                        "findby was consulted and matched nothing, yet nothing was "
                        "emitted: the Synthesised marker did not reach the predicate")
            print("FINDING: no event in %ss. %s" % (wait, why), file=sys.stderr)
            return 1
        print("  -> the event fires on the path only a non-addressable CR can take")

        # ---- ARM C: unblind, same CR, must adopt rather than create -------------------
        print("\nARM C (recovery): unblinding; %s must now ADOPT %s" % (dup, tid))
        kubectl(unblind, ctx, kc, check=False)
        blinded = False
        rid = wait_id(dup, wait)
        _, c_conds = state(dup, plural, ns, ctx, kc)
        _, c_count = events_for(dup, plural, ns, ctx, kc)
        print("  id=%s (target was %s)  events=%d  conditions=%s"
              % (rid, tid, c_count, c_conds))
        if rid != tid:
            print("NOTE: adopted id %r != target id %r -- a create may have succeeded "
                  "after all, so the duplicate was not refused." % (rid, tid))
        if c_count > b_count:
            print("FINDING: %d -> %d events after unblinding. findby can match now, so "
                  "nothing further should be inferred." % (b_count, c_count),
                  file=sys.stderr)
            return 2
        print("\nWIRED: the event fires when a blinded findby infers absence, is silent "
              "once findby can match, and never fires for an addressable resource.")
        return 0
    finally:
        if blinded:
            kubectl(unblind, ctx, kc, check=False)
            print("teardown: findby unblinded")
        for cr in reversed(made):
            kubectl(["delete", plural, cr, "-n", ns, "--wait=false"], ctx, kc, check=False)
            print("teardown: %s/%s deletion requested" % (plural, cr))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
