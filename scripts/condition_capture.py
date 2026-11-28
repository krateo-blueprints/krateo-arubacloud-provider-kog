#!/usr/bin/env python3
"""Record parent/child condition behaviour during a chain run, so a FLAP can be told
apart from a SAMPLING GAP.

The question this answers: a child went Ready=True -> False/Creating -> True and the
parent Composition never showed it. Did the flap fail to propagate, or did the parent
simply not run while it lasted? The parent only re-evaluates its children once per pass
(P, roughly the resync), so a flap shorter than P can be invisible without anything being
wrong. Those two outcomes must never share a label:

    D <  P and no parent pass fell inside the flap  ->  NOT SAMPLED
    D >= P (or a pass did fall inside) and no trace ->  NOT PROPAGATED

Modes
  live      python3 scripts/condition_capture.py --context CTX [--duration 600 --interval 5]
  analyse   python3 scripts/condition_capture.py --fixture FILE     (offline, no cluster)

Live mode uses the KUBECONFIG in the environment and never touches another one. It only
READS the cluster (get, logs). Pass --capture-out FILE to keep the raw record, which
`--fixture` can re-analyse later.

What is recorded, per sample
  parent  Ready status/reason/message/lastTransitionTime, status.unevaluatedChildren,
          status.managedChildrenCount, metadata.generation vs status.observedGeneration,
          metadata.resourceVersion, status.writtenBy
  child   Ready status/reason/message, Synced status/reason, lastTransitionTime of each,
          metadata.resourceVersion
Once per run: the parent's Events; the composition-dynamic-controller (cdc) log lines that
mention the composition (their timestamps ARE the parent's pass times); and a provenance
block (RDC + cdc image tags and the unstructured-runtime each embeds, core-provider
version, the resync period actually configured).

CHILDREN are read from the parent's `status.managed` (apiVersion/resource/name/namespace),
the list core-provider writes. A parent without it has no discoverable children, which is
reported as an error, not as "no flaps".

VENDOR GET COUNTS are deliberately NOT produced. See VENDOR_GETS below.

Exit codes
  0  ran, found input, report produced
  2  could not ask the cluster / bad arguments
  3  NOTHING FOUND (no compositions, no children, no cdc log lines, too few samples).
     The message names what was absent. This script never prints a clean empty report.
"""
import argparse
import datetime as dt
import json
import os
import re
import statistics
import subprocess
import sys
import time
import urllib.request

# Declared expectations. Every count is compared to one of these, never to itself.
MIN_COMPOSITIONS = 1
MIN_CHILDREN = 1
MIN_SAMPLES = 2          # one sample cannot show a transition
MIN_CDC_LINES = 1
MIN_PASSES_FOR_P = 2     # a pass interval needs two passes

# cdc may log several lines for one pass; lines closer than this are one pass.
DEFAULT_PASS_GAP_S = 5.0

VENDOR_GETS = """\
Vendor GET counts per created resource: NOT REPORTED. No reliable source exists.
  * rest-dynamic-controller logs no per-request line. The only per-request output is
    httputil.DumpRequestOut/DumpResponse (restclient.go debuggingRoundTripper), gated by
    the per-CR `krateo.io/connector-verbose` annotation, written to stdout with no
    timestamp and no CR name; list/findby GETs carry no resource id. Counting it would
    need that annotation set on every CR (mutating the run) and attributing by URL
    substring, which undercounts findby. (Below 0.26.1 that dump prints the bearer.)
  * REST_CONTROLLER_DEBUG / OASGEN_PROVIDER_DEBUG switch slog debug lines, not the dump.
  * The metrics server (REST_CONTROLLER_METRICS_SERVER_PORT) exposes client-go's
    rest_client_requests_total, which counts Kubernetes-API calls; the vendor client is a
    separate http.Client and is not in it.
  A number from any of these would be one this script cannot defend."""


class NothingFound(Exception):
    """Raised, never swallowed: the input was empty where it must not be."""


# ---------------------------------------------------------------- time helpers
def parse_ts(s):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    s = s.strip()
    m = re.match(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?(Z|[+-]\d\d:\d\d)?$", s)
    if not m:
        raise ValueError(f"unparseable timestamp {s!r}")
    base = dt.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")
    tz = m.group(3)
    if tz in (None, "Z"):
        off = 0
    else:
        sign = 1 if tz[0] == "+" else -1
        off = sign * (int(tz[1:3]) * 3600 + int(tz[4:6]) * 60)
    frac = float(m.group(2)) if m.group(2) else 0.0
    return base.replace(tzinfo=dt.timezone.utc).timestamp() - off + frac


# ------------------------------------------------------------ object summarisers
def cond(obj, typ):
    for c in (obj.get("status") or {}).get("conditions") or []:
        if c.get("type") == typ:
            return {"status": c.get("status"), "reason": c.get("reason"),
                    "message": c.get("message"),
                    "lastTransitionTime": c.get("lastTransitionTime")}
    return None


def summarise_parent(obj):
    st = obj.get("status") or {}
    md = obj.get("metadata") or {}
    return {
        "name": md.get("name"), "namespace": md.get("namespace"), "uid": md.get("uid"),
        "ready": cond(obj, "Ready"),
        "unevaluatedChildren": st.get("unevaluatedChildren"),
        "managedChildrenCount": st.get("managedChildrenCount"),
        "generation": md.get("generation"),
        "observedGeneration": st.get("observedGeneration"),
        "resourceVersion": md.get("resourceVersion"),
        "writtenBy": st.get("writtenBy"),
    }


def summarise_child(obj):
    md = obj.get("metadata") or {}
    return {
        "kind": obj.get("kind"), "name": md.get("name"),
        "ready": cond(obj, "Ready"), "synced": cond(obj, "Synced"),
        "resourceVersion": md.get("resourceVersion"),
    }


# ------------------------------------------------------------------ kubectl I/O
class Kube:
    def __init__(self, context):
        self.base = ["kubectl"] + (["--context", context] if context else [])

    def run(self, *args, check=True):
        p = subprocess.run(self.base + list(args), capture_output=True, text=True)
        if check and p.returncode != 0:
            raise RuntimeError(f"kubectl {' '.join(args)} -> exit {p.returncode}: "
                               f"{p.stderr.strip()}")
        return p

    def json(self, *args):
        return json.loads(self.run(*args, "-o", "json").stdout)


def discover_compositions(kube):
    """Every instance of every CRD in group composition.krateo.io."""
    crds = kube.json("get", "crd")["items"]
    plurals = sorted(c["spec"]["names"]["plural"] + "." + c["spec"]["group"]
                     for c in crds if c["spec"]["group"] == "composition.krateo.io")
    if not plurals:
        raise NothingFound("no CustomResourceDefinition in group composition.krateo.io "
                           "exists on this cluster, so there can be no Composition")
    found = []
    for res in plurals:
        for it in kube.json("get", res, "-A")["items"]:
            found.append((res, it["metadata"].get("namespace", ""), it["metadata"]["name"]))
    if len(found) < MIN_COMPOSITIONS:
        raise NothingFound(
            f"no compositions found: {len(plurals)} composition CRD(s) exist "
            f"({', '.join(plurals)}) but they hold 0 instances (expected >= "
            f"{MIN_COMPOSITIONS})")
    return found


def api_group(api_version):
    """Mirror of k8s.io schema.ParseGroupVersion, which core-provider's childEvalOf uses:
    0 slashes -> core group; 1 slash -> split there (so "/v1" and "v1/" PARSE, and are
    fetched); 2+ slashes -> error, and the rollup counts that entry healthy WITHOUT a GET.
    Returns None only in the error case. The empty string is caught earlier, by the
    empty-field check, and never reaches here."""
    if api_version.count("/") >= 2:
        return None
    return api_version.split("/", 1)[0] if "/" in api_version else ""


def managed_state(parent_obj):
    """status.managed ABSENT (inventory never recorded) vs present-but-EMPTY (an inventory
    was recorded and holds nothing) vs present. They are different facts: core-provider
    reports Ready=False/InventoryNotRecorded for the first, Ready=True/Available for the
    second."""
    m = (parent_obj.get("status") or {}).get("managed")
    if m is None:
        return "absent", []
    return ("empty" if len(m) == 0 else "present"), m


def managed_refs(entries):
    """(resource, namespace, name) per status.managed entry, using the entry's own
    `resource` (the plural, not the kind) and `namespace` as written. An entry missing
    apiVersion/resource/name, or with an unparsable apiVersion, is healthy WITHOUT a GET
    in core-provider's rollup, so it is returned as skipped, never fetched."""
    refs, skipped = [], []
    for m in entries:
        grp = api_group(m.get("apiVersion"))
        if not (m.get("apiVersion") and m.get("resource") and m.get("name")) or grp is None:
            skipped.append(f"{m.get('resource')}/{m.get('namespace','')}/{m.get('name')}")
            continue
        res = m["resource"] + ("." + grp if grp else "")
        refs.append((res, m.get("namespace", ""), m["name"]))
    return refs, skipped


def sample_once(kube, comps):
    now = time.time()
    out = []
    for res, ns, name in comps:
        nsarg = ["-n", ns] if ns else []
        pobj = kube.json("get", res, name, *nsarg)
        state, entries = managed_state(pobj)
        refs, skipped = managed_refs(entries)
        children = {}
        for cres, cns, cname in refs:
            cnsarg = ["-n", cns] if cns else []
            p = kube.run("get", cres, cname, *cnsarg, "-o", "json", check=False)
            key = f"{cres}/{cns}/{cname}"
            if p.returncode == 0:
                children[key] = summarise_child(json.loads(p.stdout))
            elif "notfound" in p.stderr.lower().replace(" ", ""):
                # NotFound counts as CONVERGING in the parent's rollup: keep it, flagged,
                # as a non-True child rather than dropping it.
                children[key] = {"absent": True, "name": cname,
                                 "ready": {"status": "Absent", "reason": "NotFound"},
                                 "synced": {"status": "Absent", "reason": "NotFound"}}
            else:
                children[key] = {"error": p.stderr.strip()}
        out.append({"composition": f"{res}/{ns}/{name}", "parent": summarise_parent(pobj),
                    "managed_state": state, "managed_entries": len(entries),
                    "managed_skipped_no_get": skipped,
                    "managed_recorded_but_absent": sorted(
                        k for k, v in children.items() if v.get("absent")),
                    "children": children})
    return {"t": now, "compositions": out}


def tag_of(image):
    base = image.rsplit("/", 1)[-1]
    return base.split(":", 1)[1].split("@")[0] if ":" in base else None


def go_mod_runtime_version(repo, tag):
    """unstructured-runtime version from the go.mod at the image's git tag. The image does
    not carry it in a readable place, and the binary is distroless."""
    for ref in (tag, "v" + tag if not tag.startswith("v") else tag[1:]):
        url = f"https://raw.githubusercontent.com/krateoplatformops/{repo}/{ref}/go.mod"
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                m = re.search(r"unstructured-runtime\s+(v\S+)", r.read().decode())
                if m:
                    return m.group(1)
        except Exception:
            continue
    return None


def pods_by_image(kube, needle):
    pods = kube.json("get", "pods", "-A")["items"]
    return [p for p in pods
            if any(needle in c.get("image", "")
                   for c in p["spec"].get("containers", []))]


def configured_resync(pod, env_name):
    """Resync as the pod is ACTUALLY configured: a -resync-interval arg wins over the
    env var, which wins over the code default. Reports where the value came from."""
    for c in pod["spec"]["containers"]:
        for a in (c.get("args") or []) + (c.get("command") or []):
            m = re.match(r"^-{1,2}resync-interval[= ](.+)$", a)
            if m:
                return {"value": m.group(1), "source": f"arg {a}"}
        args = (c.get("args") or [])
        for i, a in enumerate(args):
            if re.match(r"^-{1,2}resync-interval$", a) and i + 1 < len(args):
                return {"value": args[i + 1], "source": f"arg {a} {args[i+1]}"}
        for e in c.get("env") or []:
            if e.get("name") == env_name:
                if "value" in e:
                    return {"value": e["value"], "source": f"env {env_name}"}
                return {"value": None, "source": f"env {env_name} via valueFrom "
                                                  "(not resolved)"}
    return {"value": "3m", "source": "code default (no arg or env set)"}


def build_provenance(kube, comp_summary_first):
    prov = {}
    for key, needle, repo, env_name in (
            ("rdc", "rest-dynamic-controller", "rest-dynamic-controller",
             "REST_CONTROLLER_RESYNC_INTERVAL"),
            ("cdc", "composition-dynamic-controller", "composition-dynamic-controller",
             "COMPOSITION_CONTROLLER_RESYNC_INTERVAL")):
        pods = pods_by_image(kube, needle)
        if not pods:
            prov[key] = {"error": f"no pod runs an image matching {needle!r}"}
            continue
        images = sorted({c["image"] for p in pods for c in p["spec"]["containers"]
                         if needle in c["image"]})
        tags = sorted({tag_of(i) or "?" for i in images})
        rt = {t: go_mod_runtime_version(repo, t) for t in tags if t != "?"}
        prov[key] = {"images": images, "tags": tags,
                     "unstructured_runtime": {t: (v or "UNRESOLVED (go.mod not fetched)")
                                              for t, v in rt.items()},
                     "resync": configured_resync(pods[0], env_name),
                     "pods": len(pods)}
    wb = (comp_summary_first or {}).get("writtenBy") or {}
    if wb.get("version"):
        prov["core_provider"] = {"version": wb["version"],
                                 "source": "status.writtenBy.version"}
    else:
        prov["core_provider"] = {
            "version": None,
            "source": "status.writtenBy is ABSENT on the composition (it exists only "
                      "from core-provider 2.13.135); read the version from the "
                      "core-provider chart/image on the cluster instead"}
    return prov


def capture_cdc_lines(kube, comp_names, since):
    pods = pods_by_image(kube, "composition-dynamic-controller")
    if not pods:
        raise NothingFound("no composition-dynamic-controller pod found on the cluster; "
                           "there are no cdc log lines to read parent pass times from")
    lines = []
    for p in pods:
        ns, nm = p["metadata"]["namespace"], p["metadata"]["name"]
        r = kube.run("logs", "-n", ns, nm, "--timestamps", f"--since={since}s",
                     check=False)
        for ln in r.stdout.splitlines():
            if any(n in ln for n in comp_names):
                lines.append(ln)
    return lines


def capture_events(kube, comps):
    ev = []
    for res, ns, name in comps:
        nsarg = ["-n", ns] if ns else ["-A"]
        d = kube.json("get", "events", *nsarg, "--field-selector",
                      f"involvedObject.name={name}")
        ev += [{"composition": f"{res}/{ns}/{name}", "type": e.get("type"),
                "reason": e.get("reason"), "message": e.get("message"),
                "lastTimestamp": e.get("lastTimestamp") or e.get("eventTime")}
               for e in d["items"]]
    return ev


def live_capture(a):
    kube = Kube(a.context)
    comps = discover_compositions(kube)
    started = time.time()
    samples = []
    end = started + a.duration
    while True:
        samples.append(sample_once(kube, comps))
        if time.time() + a.interval > end:
            break
        time.sleep(a.interval)
    first = samples[0]["compositions"][0]["parent"] if samples[0]["compositions"] else {}
    cdc = capture_cdc_lines(kube, [c[2] for c in comps],
                            int(time.time() - started) + 60)
    return {"provenance": build_provenance(kube, first), "samples": samples,
            "events": capture_events(kube, comps), "cdc_log_lines": cdc}


# ---------------------------------------------------------------------- analysis
def cdc_times(lines):
    ts = []
    for ln in lines:
        m = re.search(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)", ln)
        if m:
            ts.append(parse_ts(m.group(0)))
    return sorted(ts)


def passes_from(times, gap):
    passes = []
    for t in times:
        if not passes or t - passes[-1] >= gap:
            passes.append(t)
    return passes


def series(samples, comp, child):
    out = []
    for s in samples:
        for c in s["compositions"]:
            if c["composition"] == comp and child in c["children"] \
                    and "error" not in c["children"][child]:
                out.append((s["t"], c["children"][child], c["parent"]))
    return out


def find_flaps(rows, which):
    """Windows during which condition `which` was not True, bracketed by True on both
    sides. start/end come from lastTransitionTime where present (the controller's own
    record, finer than the sample interval) else from the sample times.

    A flap that began AND ended between two samples leaves status True on both, but moves
    lastTransitionTime. It is reported with `bound` set: its start is unknown, so D is an
    UPPER bound (end minus the earlier sample), never a measurement."""
    flaps, cur, seen_true, prev = [], None, False, None
    for t, ch, _ in rows:
        c = ch.get(which) or {}
        if c.get("status") == "True":
            ltt = c.get("lastTransitionTime")
            if cur is not None:
                # A child that reappears (NotFound -> back) keeps its OLD
                # lastTransitionTime, which predates the window: use it only if it
                # does not, else the sample time.
                lt = parse_ts(ltt) if ltt else None
                cur["end"] = lt if lt is not None and lt >= cur["start"] else t
                flaps.append(cur)
                cur = None
            elif prev is not None and ltt and ltt != prev[1] and prev[0] is not None:
                flaps.append({"which": which, "start": prev[0], "end": parse_ts(ltt),
                              "bound": True, "status": "hidden", "reasons": {"(hidden)"},
                              "messages": set()})
            seen_true, prev = True, (t, ltt)
        elif seen_true:
            if cur is None:
                cur = {"which": which, "start": t, "bound": False,
                       "status": c.get("status"), "reasons": set(), "messages": set()}
            cur["reasons"].add(c.get("reason"))
            if c.get("message"):
                cur["messages"].add(c["message"])
            if c.get("lastTransitionTime"):
                lt = parse_ts(c["lastTransitionTime"])
                if lt <= cur["start"]:
                    cur["start"] = lt
            prev = None
    return flaps


_MSG_PREFIX = re.compile(r"^(?:\d+ of \d+ managed children are not ready|"
                         r"managed children not healthy): ")


def parse_names(msg):
    """The child names a parent Ready message lists, or None when the message is not one
    of the two known formats (never guess). Formats, from core-provider:
        "N of M managed children are not ready: a, b, c (+N more)"
        "managed children not healthy: a, b, c (+N more)"
    An empty/absent message is the healthy state and lists nothing ([])."""
    if not msg:
        return []
    m = _MSG_PREFIX.match(msg)
    if not m:
        return None
    rest = re.sub(r"\s*\(\+\d+ more\)\s*$", "", msg[m.end():])
    return [t.strip() for t in rest.split(", ") if t.strip()]


def _count(msg):
    m = re.match(r"\s*(\d+) of (\d+) ", msg or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def _fmt(r):
    return f"{r.get('status')}/{r.get('reason')}/{r.get('message')!r}"


def _pass_without_write(rows, in_win, margin):
    """True/False/None. Did the parent's resourceVersion stay put across at least one
    pass inside the window? Compares the last sample at or before the pass with the first
    sample more than `margin` seconds after it. None when no pass has both samples."""
    tested = False
    for p in in_win:
        pre = [r for r in rows if r[0] <= p]
        post = [r for r in rows if r[0] > p + margin]
        if not pre or not post:
            continue
        tested = True
        if pre[-1][2].get("resourceVersion") == post[0][2].get("resourceVersion"):
            return True
    return False if tested else None


def parent_evidence(rows, flap, P, concurrent):
    """What did the PARENT show for this flap? Returns (evidence, parent_was_down, context).

    lastTransitionTime moves ONLY when a condition's status changes (condition.Upsert
    keeps the old time when the status is unchanged), so a parent already Ready!=True
    rewrites reason/message and leaves the timestamp alone. Priority:
      1. ATTRIBUTABLE message diff: this child's NAME appears in / disappears from the
         parent's Ready message between consecutive samples. The message lists at most 3
         children ("a, b, c (+N more)"), so a flapping child is often not named.
      2. A count-only change ("4 of 36" -> "5 of 36") is "some child", not this one: it
         is recorded as UNATTRIBUTED COUNT CHANGE and counts as evidence only when this
         was the sole Ready flap in the window (`concurrent` == 1), which is stated.
      3. A STATUS transition (lastTransitionTime in window, or parent not-True when it was
         True at start).
    resourceVersion is NOT evidence of propagation (it moves on any write); it is only the
    no-write test that NOT PROPAGATED requires, applied in classify().
    """
    start, end, name = flap["start"], flap["end"], flap["child"]
    hi = end + (P or 0)
    before = [r for r in rows if r[0] <= start]
    base = (before[-1] if before else rows[0])[2].get("ready") or {}
    was_down = base.get("status") != "True"
    ctx = []
    for i in range(1, len(rows)):
        t, _, cur = rows[i]
        if not (start <= t <= hi):
            continue
        pr, cr = rows[i - 1][2].get("ready") or {}, cur.get("ready") or {}
        if pr.get("message") == cr.get("message") and pr.get("reason") == cr.get("reason"):
            continue
        shown = f"before {_fmt(pr)} -> after {_fmt(cr)}"
        pn, cn = parse_names(pr.get("message")), parse_names(cr.get("message"))
        if pn is None or cn is None:
            ctx.append(f"message not in a known format, attribution unavailable: {shown}")
            continue
        if (name in pn) != (name in cn):
            return (f"attributable (name {'gained' if name in cn else 'lost'}"
                    f" at +{t-start:.0f}s): {shown}", was_down, ctx)
        pc, cc = _count(pr.get("message")), _count(cr.get("message"))
        if pc and cc and pc != cc:
            if concurrent == 1:
                return (f"count change attributable only because this was the SOLE Ready "
                        f"flap in the window (+{t-start:.0f}s): {shown}", was_down, ctx)
            ctx.append(f"UNATTRIBUTED COUNT CHANGE ({concurrent} Ready flaps overlapped "
                       f"the window): {shown}")
        else:
            ctx.append(f"parent message/reason changed but does not name {name!r}: {shown}")
    if not was_down:
        for t, _, par in rows:
            r = par.get("ready") or {}
            if start <= t <= hi and r.get("status") != "True":
                return (f"status transition: parent Ready={_fmt(r)} sampled at "
                        f"+{t-start:.0f}s", was_down, ctx)
    for t, _, par in rows:
        ltt = (par.get("ready") or {}).get("lastTransitionTime")
        if ltt and start <= parse_ts(ltt) <= hi:
            return (f"status transition: parent Ready lastTransitionTime {ltt} falls in "
                    "the window", was_down, ctx)
    return None, was_down, ctx


def classify(flap, rows, passes, P, concurrent=1, margin=DEFAULT_PASS_GAP_S):
    D = flap["end"] - flap["start"]
    bound = flap.get("bound", False)
    in_win = [p for p in passes if flap["start"] <= p <= flap["end"]]
    if P is None:
        rel = "D vs P: UNDEFINED (fewer than 2 parent passes observed)"
    else:
        rel = (("D < P" if D < P else "D >= P") +
               f"  (D={'<=' if bound else ''}{D:.1f}s, P={P:.1f}s)")
        if bound and D >= P:
            rel += "  [D is only an upper bound: start fell between samples]"
    ready_down = any((ch.get("ready") or {}).get("status") != "True"
                     for t, ch, _ in rows if flap["start"] <= t <= flap["end"])
    if flap["which"] == "synced" and not ready_down:
        verdict = ("INVISIBLE TO PARENT: the child's Ready stayed True; only Synced "
                   "flapped, and the parent decides on Ready")
        return {"D": D, "relation": rel, "passes_in_window": len(in_win),
                "verdict": verdict}
    ev, was_down, ctx = parent_evidence(rows, flap, P, concurrent)
    if ev:
        verdict = f"PROPAGATED ({ev})"
    elif P is None:
        verdict = "INDETERMINATE (no pass interval to compare D against)"
    elif not in_win and D < P:
        verdict = ("NOT SAMPLED: D < P and no parent pass fell inside the flap; the "
                   "parent never had the chance to observe it. This is NOT a "
                   "propagation failure")
    elif bound and D >= P:
        verdict = ("INDETERMINATE: the flap began between samples, so D is only an "
                   "upper bound that is not below P; shorten --interval")
    elif not in_win:
        verdict = ("INDETERMINATE: D >= P yet the pass log shows no pass inside the "
                   "window; the cdc log has a gap, so neither 'not sampled' nor "
                   "'not propagated' can be claimed")
    elif was_down:
        verdict = ("NOT ASSESSABLE: the parent was already Ready != True at flap start "
                   "(no status transition available to make) and no change attributable "
                   "to this child appeared. Neither propagation nor its failure")
    else:
        nowrite = _pass_without_write(rows, in_win, margin)
        if nowrite is True:
            verdict = (f"NOT PROPAGATED: {len(in_win)} parent pass(es) fell inside the "
                       "flap, the parent's resourceVersion did not move across at least "
                       "one of them (no write attempted), and Ready showed nothing")
        elif nowrite is False:
            verdict = ("NOT ASSESSABLE: the parent WAS written across every pass in the "
                       "window (resourceVersion moved) but nothing attributable to this "
                       "child changed; something was written and it cannot be said to "
                       "be about this child")
        else:
            verdict = ("NOT ASSESSABLE: no sample brackets any pass inside the window, so "
                       "'no write across a pass' cannot be tested; shorten --interval")
    # raw strings beside every verdict, so a reader can judge the diff themselves
    before = [r for r in rows if r[0] <= flap["start"]]
    pre = (before[-1] if before else rows[0])[2].get("ready") or {}
    post = [r for r in rows if r[0] <= flap["end"] + (P or 0)][-1][2].get("ready") or {}
    verdict += f"  [parent Ready at flap start: {_fmt(pre)}; at window end: {_fmt(post)}]"
    if ctx:
        verdict += "  [context: " + "; ".join(ctx) + "]"
    return {"D": D, "relation": rel, "passes_in_window": len(in_win), "verdict": verdict}


def mismatches(samples, comp):
    """status.managed is what the parent RECORDED when it last wrote the release, not
    what exists now. State every disagreement with live existence."""
    out, absent, skipped, counts = [], {}, set(), set()
    n = 0
    for s in samples:
        for c in s["compositions"]:
            if c["composition"] != comp:
                continue
            n += 1
            for k, v in c["children"].items():
                if v.get("absent"):
                    absent[k] = absent.get(k, 0) + 1
            skipped.update(c.get("managed_skipped_no_get") or [])
            mc, me = c["parent"].get("managedChildrenCount"), c.get("managed_entries")
            if mc is not None and me is not None and mc != me:
                counts.add((mc, me))
    for k, v in sorted(absent.items()):
        out.append(f"recorded in status.managed but NotFound live in {v} of {n} "
                   f"sample(s): {k} (deleted out of band, or not yet created)")
    for k in sorted(skipped):
        out.append(f"entry {k} is malformed; the parent counts it healthy without a GET, "
                   "so it was not fetched")
    for mc, me in sorted(counts):
        out.append(f"status.managedChildrenCount={mc} but status.managed holds {me} "
                   "entries")
    return out


def analyse(rec, pass_gap=DEFAULT_PASS_GAP_S):
    a_gap = pass_gap
    samples = rec.get("samples") or []
    if not samples:
        raise NothingFound("the record holds 0 samples")
    if len(samples) < MIN_SAMPLES:
        raise NothingFound(f"the record holds {len(samples)} sample(s); at least "
                           f"{MIN_SAMPLES} are needed to see any transition")
    comps = sorted({c["composition"] for s in samples for c in s["compositions"]})
    if len(comps) < MIN_COMPOSITIONS:
        raise NothingFound(f"no compositions in any sample (found {len(comps)}, "
                           f"expected >= {MIN_COMPOSITIONS})")
    lines = rec.get("cdc_log_lines") or []
    times = cdc_times(lines)
    if len(times) < MIN_CDC_LINES:
        raise NothingFound(f"no cdc log lines mentioning the composition(s) with a "
                           f"parseable timestamp ({len(lines)} line(s) given, "
                           f"{len(times)} timestamped; expected >= {MIN_CDC_LINES}); "
                           "parent pass times cannot be derived")
    passes = passes_from(times, pass_gap)
    P = None
    if len(passes) >= MIN_PASSES_FOR_P:
        P = statistics.median(b - a for a, b in zip(passes, passes[1:]))
    report = {"passes_observed": len(passes), "P": P, "compositions": {}, "children": 0}
    for comp in comps:
        kids = sorted({k for s in samples for c in s["compositions"]
                       if c["composition"] == comp for k in c["children"]})
        if len(kids) < MIN_CHILDREN:
            states = {c.get("managed_state", "unknown") for s in samples
                      for c in s["compositions"] if c["composition"] == comp}
            what = []
            if "absent" in states:
                what.append("status.managed is ABSENT: the inventory was never recorded "
                            "(core-provider reports Ready=False/InventoryNotRecorded)")
            if "empty" in states:
                what.append("status.managed is present but EMPTY: the parent recorded an "
                            "inventory with nothing in it (Ready=True/Available)")
            if not what:
                what.append("status.managed state was not recorded in this record")
            raise NothingFound(f"composition {comp} has 0 children in every sample "
                               f"(expected >= {MIN_CHILDREN}); " + "; ".join(what))
        rvs = [c["parent"].get("resourceVersion") for s in samples
               for c in s["compositions"] if c["composition"] == comp]
        entry = {"parent_writes_seen": len(set(rvs)) - 1, "children": {},
                 "mismatches": mismatches(samples, comp)}
        per = {}
        for k in kids:
            rows = series(samples, comp, k)
            per[k] = (rows, find_flaps(rows, "ready") + find_flaps(rows, "synced"))
        ready_flaps = [f for _, fl in per.values() for f in fl if f["which"] == "ready"]
        for k, (rows, flaps) in per.items():
            out = []
            for f in flaps:
                f = dict(f, child=(rows[-1][1].get("name") or k.rsplit("/", 1)[-1]))
                hi = f["end"] + (P or 0)
                conc = sum(1 for g in ready_flaps
                           if g["start"] <= hi and g["end"] >= f["start"])
                out.append(dict(f, concurrent_ready_flaps=conc,
                                **classify(f, rows, passes, P, conc, a_gap)))
            entry["children"][k] = {
                "samples": len(rows),
                "child_writes_seen": len({r[1].get("resourceVersion") for r in rows}) - 1,
                "flaps": out}
            report["children"] += 1
        report["compositions"][comp] = entry
    if report["children"] < MIN_CHILDREN:
        raise NothingFound("no children found")
    return report


VERDICTS = ("PROPAGATED", "NOT PROPAGATED", "NOT SAMPLED", "NOT ASSESSABLE",
            "INDETERMINATE", "INVISIBLE TO PARENT")


def verdict_counts(rep):
    n = {v: 0 for v in VERDICTS}
    for e in rep["compositions"].values():
        for c in e["children"].values():
            for f in c["flaps"]:
                lab = max((v for v in VERDICTS if f["verdict"].startswith(v)), key=len)
                n[lab] += 1
    return n


def render(rec, rep):
    o = []
    o.append("== provenance ==")
    for k, v in (rec.get("provenance") or {}).items():
        o.append(f"  {k}: {json.dumps(v, sort_keys=True)}")
    o.append("== parent passes (from cdc log timestamps) ==")
    P = rep["P"]
    o.append(f"  passes observed: {rep['passes_observed']}   "
             f"P (median interval): {'%.1fs' % P if P is not None else 'UNDEFINED'}")
    o.append(f"== vendor GETs ==\n  {VENDOR_GETS.splitlines()[0]} (reason: see --help)")
    for comp, e in rep["compositions"].items():
        o.append(f"== {comp}   parent writes seen: {e['parent_writes_seen']} ==")
        for m in e["mismatches"]:
            o.append(f"  MISMATCH: {m}")
        for k, c in e["children"].items():
            o.append(f"  child {k}: {c['samples']} samples, "
                     f"{c['child_writes_seen']} writes seen, {len(c['flaps'])} flap(s)")
            for f in c["flaps"]:
                o.append(f"    flap on {f['which']}: reasons={sorted(map(str, f['reasons']))}"
                         f"  D={'<=' if f.get('bound') else ''}{f['D']:.1f}s  P={'%.1fs' % P if P is not None else 'n/a'}"
                         f"  passes observed={rep['passes_observed']}"
                         f"  passes in window={f['passes_in_window']}"
                         f"  Ready flaps overlapping window={f['concurrent_ready_flaps']}")
                o.append(f"      relation: {f['relation']}")
                o.append(f"      verdict:  {f['verdict']}")
    n = verdict_counts(rep)
    o.append("== VERDICT SUMMARY (%d flap(s)) ==" % sum(n.values()))
    for v in VERDICTS:
        o.append(f"  {v:20s} {n[v]}")
    if n["NOT ASSESSABLE"] > sum(n.values()) / 2:
        o.append("  NOTE: most flaps are NOT ASSESSABLE. The parent's status cannot "
                 "attribute them at this scale (three-name cap, parent already not "
                 "Ready); that is a finding about observability, not a failure of the run.")
    return "\n".join(o)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 epilog=VENDOR_GETS,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--context", default=os.environ.get("KUBE_CONTEXT"))
    ap.add_argument("--duration", type=float, default=600)
    ap.add_argument("--interval", type=float, default=5,
                    help="seconds between samples (default 5). A flap that starts and "
                         "ends between two samples only gets an upper bound on D; if "
                         "that bound is not below P the verdict is INDETERMINATE and "
                         "the remedy is a smaller --interval. P comes ONLY from cdc log "
                         "pass timestamps, with no fallback to the configured resync")
    ap.add_argument("--pass-gap", type=float, default=DEFAULT_PASS_GAP_S,
                    help="cdc log lines closer than this are one parent pass")
    ap.add_argument("--fixture", help="analyse this recorded JSON instead of a cluster")
    ap.add_argument("--capture-out", help="write the raw record here")
    a = ap.parse_args(argv)
    try:
        if a.fixture:
            rec = json.load(open(a.fixture))
        else:
            if not a.context:
                print("ERROR: pass --context (or KUBE_CONTEXT); refusing to use the "
                      "current-context of whatever kubeconfig is ambient", file=sys.stderr)
                return 2
            rec = live_capture(a)
        if a.capture_out:
            json.dump(rec, open(a.capture_out, "w"), indent=1, default=str)
        rep = analyse(rec, a.pass_gap)
    except NothingFound as e:
        print(f"NOTHING FOUND: {e}", file=sys.stderr)
        return 3
    except (RuntimeError, OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    print(render(rec, rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
