#!/usr/bin/env python3
"""Positive control and empty-input tests for scripts/condition_capture.py.

    python3 tests/condition-capture/test_condition_capture.py

The positive control is what shows the detector DETECTS: a fixture with one child per
outcome, each asserted by name. The empty-input tests are what show the guard can FIRE:
every "nothing found" branch is driven and must exit 3 naming what was absent.
"""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "..", "scripts", "condition_capture.py")
FIXTURE = os.path.join(HERE, "fixture.json")


def run(path):
    return subprocess.run([sys.executable, "-I", SCRIPT, "--fixture", path],
                          capture_output=True, text=True)


def with_record(rec):
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(rec, f)
    f.close()
    return f.name


def base():
    return copy.deepcopy(json.load(open(FIXTURE)))


class Control(unittest.TestCase):
    def test_verdicts(self):
        p = run(FIXTURE)
        self.assertEqual(p.returncode, 0, p.stderr)
        blocks = {}
        cur = None
        for ln in p.stdout.split("== VERDICT SUMMARY")[0].splitlines():
            if ln.startswith("  child "):
                cur = ln.split()[1].rstrip(":").rsplit("/", 1)[-1]
                blocks[cur] = []
            elif cur:
                blocks[cur].append(ln)
        txt = {k: "\n".join(v) for k, v in blocks.items()}
        self.assertEqual(len(txt), 5, "all four fixture children must be reported")
        # the two the lead asked for
        self.assertIn("PROPAGATED (", txt["vpc-prop"])
        self.assertNotIn("NOT PROPAGATED", txt["vpc-prop"])
        self.assertIn("INVISIBLE TO PARENT", txt["lb-syncedonly"])
        # D and P, with the relation stated, on every flap
        for k, v in txt.items():
            self.assertRegex(v, r"D=<?[\d.]+s\s+P=[\d.]+s", k)
            self.assertRegex(v, r"relation: D (<|>=) P", k)
        # parent already Ready=False/Creating, status never changes: only the message
        # moves "1 of 2" -> "2 of 2" and gains the child's name. lastTransitionTime is
        # outside the window, so a timestamp-only detector would say NOT PROPAGATED.
        self.assertIn("PROPAGATED (attributable", txt["db-reasononly"])
        self.assertNotIn("NOT PROPAGATED", txt["db-reasononly"])
        self.assertIn("'1 of 2 managed children are not ready: net-other'",
                      txt["db-reasononly"])
        self.assertIn("'2 of 2 managed children are not ready: net-other, db-reasononly'",
                      txt["db-reasononly"])
        # the distinction that is the deliverable
        self.assertIn("NOT SAMPLED", txt["subnet-notsampled"])
        self.assertNotIn("NOT PROPAGATED", txt["subnet-notsampled"])
        self.assertIn("NOT PROPAGATED", txt["sg-notprop"])
        self.assertNotIn("NOT SAMPLED", txt["sg-notprop"])


class ParentAlreadyDown(unittest.TestCase):
    def verdict(self, mutate):
        r = base()
        mutate(r)
        p = run(with_record(r))
        self.assertEqual(p.returncode, 0, p.stderr)
        lines = p.stdout.splitlines()
        i = [n for n, l in enumerate(lines) if l.startswith("  child") and
             l.split()[1].endswith("db-reasononly:")][0]
        return "\n".join(lines[i:i + 4])

    def test_no_attributable_change_is_not_assessable(self):
        def flat(r):  # message never changes: nothing attributable, status never moves
            for smp in r["samples"]:
                rd = smp["compositions"][0]["parent"]["ready"]
                if rd["status"] == "False" and "db-reasononly" in rd["message"]:
                    rd["message"] = "1 of 2 managed children are not ready: net-other"
        v = self.verdict(flat)
        self.assertIn("NOT ASSESSABLE", v)
        self.assertNotIn("NOT PROPAGATED", v)

    def test_unattributable_message_churn_is_not_evidence(self):
        def churn(r):  # wording churns, no name and no count change (core-provider#298)
            for smp in r["samples"]:
                rd = smp["compositions"][0]["parent"]["ready"]
                if rd["status"] == "False" and "db-reasononly" in rd["message"]:
                    rd["message"] = "1 of 2 managed children are not ready: net-other (waiting)"
        v = self.verdict(churn)
        self.assertIn("NOT ASSESSABLE", v)
        self.assertIn("does not name", v)


class EvidenceRules(unittest.TestCase):
    DB = "vpcs.network.arubacloud.com/ns/db-reasononly"

    def db_block(self, mutate):
        r = base()
        mutate(r)
        p = run(with_record(r))
        self.assertEqual(p.returncode, 0, p.stderr)
        lines = p.stdout.splitlines()
        i = [n for n, l in enumerate(lines) if l.startswith("  child") and
             l.split()[1].endswith("db-reasononly:")][0]
        return "\n".join(lines[i:i + 4])

    @staticmethod
    def msgs(r, before, after):
        """Rewrite the parent message in the db flap window: `before` until the second
        pass-write, `after` once the fixture's message has changed."""
        for smp in r["samples"]:
            rd = smp["compositions"][0]["parent"]["ready"]
            if rd["status"] == "False" and rd["lastTransitionTime"].endswith("10:06:00Z"):
                rd["message"] = after if "db-reasononly" in rd["message"] else before

    def test_count_only_change_with_sole_flapper_is_attributable(self):
        v = self.db_block(lambda r: self.msgs(
            r, "4 of 36 managed children are not ready: a, b, c (+1 more)",
            "5 of 36 managed children are not ready: a, b, c (+2 more)"))
        self.assertIn("PROPAGATED (count change attributable only because this was the "
                      "SOLE Ready flap", v)

    def test_count_only_change_with_two_flappers_is_unattributed(self):
        def two(r):
            self.msgs(r, "4 of 36 managed children are not ready: a, b, c (+1 more)",
                      "5 of 36 managed children are not ready: a, b, c (+2 more)")
            # a second child flaps Ready inside the same window
            for smp in r["samples"]:
                t = round(smp["t"] - r["samples"][0]["t"])
                if 390 <= t < 430:
                    smp["compositions"][0]["children"][
                        "vpcs.network.arubacloud.com/ns/sg-notprop"]["ready"] = {
                        "status": "False", "reason": "Creating", "message": "",
                        "lastTransitionTime": "2026-11-28T10:06:30Z"}
        v = self.db_block(two)
        self.assertIn("NOT ASSESSABLE", v)
        self.assertIn("UNATTRIBUTED COUNT CHANGE", v)
        self.assertNotIn("PROPAGATED (", v)

    def test_rv_moving_without_attributable_change_is_not_assessable(self):
        r = base()
        for smp in r["samples"]:
            if round(smp["t"] - r["samples"][0]["t"]) >= 245:
                smp["compositions"][0]["parent"]["resourceVersion"] = "999"
        p = run(with_record(r))
        sg = [l for l in p.stdout.splitlines() if "sg-notprop" in l][0]
        i = p.stdout.splitlines().index(sg)
        v = "\n".join(p.stdout.splitlines()[i:i + 4])
        self.assertIn("NOT ASSESSABLE", v)
        self.assertIn("WAS written", v)
        self.assertRegex(p.stdout, r"VERDICT SUMMARY \(\d+ flap")
        self.assertNotIn("NOT PROPAGATED", v)


class NameAttribution(unittest.TestCase):
    """Exact token equality on the parsed name list; never a substring or regex search."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(HERE, "..", "..", "scripts"))
        import condition_capture as cc
        cls.cc = cc

    def test_parse_names(self):
        pn = self.cc.parse_names
        self.assertEqual(pn("3 of 36 managed children are not ready: vpc-2, subnet-a, "
                            "db-reasononly (+33 more)"),
                         ["vpc-2", "subnet-a", "db-reasononly"])
        self.assertEqual(pn("managed children not healthy: a, b"), ["a", "b"])
        self.assertEqual(pn(""), [])
        self.assertIsNone(pn("something else entirely: vpc"))

    def test_prefixes_and_dots_do_not_match(self):
        names = self.cc.parse_names("3 of 36 managed children are not ready: "
                                    "ga-pf-folder-drifted, ga.x, cloudserver-prereqs")
        for wrong in ("ga-pf-folder", "ga", "cloudserver", "x"):
            self.assertNotIn(wrong, names)
        for right in ("ga-pf-folder-drifted", "ga.x", "cloudserver-prereqs"):
            self.assertIn(right, names)

    def run_flap(self, msg_after, second_flapper=True):
        """db-reasononly flaps while the message names something else."""
        r = base()
        for smp in r["samples"]:
            t = round(smp["t"] - r["samples"][0]["t"])
            rd = smp["compositions"][0]["parent"]["ready"]
            if rd["status"] == "False" and "db-reasononly" in rd["message"]:
                rd["message"] = msg_after
            if second_flapper and 390 <= t < 430:
                smp["compositions"][0]["children"][
                    "vpcs.network.arubacloud.com/ns/sg-notprop"]["ready"] = {
                    "status": "False", "reason": "Creating", "message": "",
                    "lastTransitionTime": "2026-11-28T10:06:30Z"}
        p = run(with_record(r))
        self.assertEqual(p.returncode, 0, p.stderr)
        lines = p.stdout.splitlines()
        i = [n for n, l in enumerate(lines) if l.startswith("  child") and
             l.split()[1].endswith("db-reasononly:")][0]
        return "\n".join(lines[i:i + 4])

    def test_colliding_pair_is_not_propagated_by_attribution(self):
        # the flapping child is db-reasononly; the message names db-reasononly-drifted
        v = self.run_flap("2 of 2 managed children are not ready: net-other, "
                          "db-reasononly-drifted")
        print("\n--- colliding pair verdict ---\n" + v)
        self.assertNotIn("PROPAGATED (", v)
        self.assertNotIn("name gained", v)
        self.assertIn("NOT ASSESSABLE", v)

    def test_unknown_message_format_is_never_a_match(self):
        v = self.run_flap("converging: db-reasononly is not ready")
        self.assertNotIn("PROPAGATED (", v)
        self.assertIn("attribution unavailable", v)
        self.assertIn("NOT ASSESSABLE", v)


class EmptyInputMustFail(unittest.TestCase):
    def expect_fail(self, rec, needle):
        p = run(with_record(rec))
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        self.assertIn("NOTHING FOUND", p.stderr)
        self.assertIn(needle, p.stderr)
        self.assertEqual(p.stdout, "", "a failure must print no report")

    def test_no_samples(self):
        r = base(); r["samples"] = []
        self.expect_fail(r, "0 samples")

    def test_one_sample(self):
        r = base(); r["samples"] = r["samples"][:1]
        self.expect_fail(r, "1 sample")

    def test_no_compositions(self):
        r = base()
        for s in r["samples"]:
            s["compositions"] = []
        self.expect_fail(r, "no compositions")

    def test_no_children(self):
        r = base()
        for s in r["samples"]:
            for c in s["compositions"]:
                c["children"] = {}
        self.expect_fail(r, "0 children")

    def test_no_cdc_lines(self):
        r = base(); r["cdc_log_lines"] = []
        self.expect_fail(r, "no cdc log lines")

    def test_cdc_lines_without_timestamps(self):
        r = base(); r["cdc_log_lines"] = ["reconciling composition chain"]
        self.expect_fail(r, "no cdc log lines")


class ManagedState(unittest.TestCase):
    def kids_gone(self, state):
        r = base()
        for s in r["samples"]:
            for c in s["compositions"]:
                c["children"] = {}
                c["managed_state"] = state
        return run(with_record(r))

    def test_absent_is_named(self):
        p = self.kids_gone("absent")
        self.assertEqual(p.returncode, 3, p.stderr)
        self.assertIn("status.managed is ABSENT", p.stderr)
        self.assertIn("InventoryNotRecorded", p.stderr)
        self.assertNotIn("EMPTY", p.stderr)
        self.assertEqual(p.stdout, "")

    def test_empty_is_named(self):
        p = self.kids_gone("empty")
        self.assertEqual(p.returncode, 3, p.stderr)
        self.assertIn("status.managed is present but EMPTY", p.stderr)
        self.assertNotIn("ABSENT", p.stderr)
        self.assertEqual(p.stdout, "")

    def test_notfound_child_is_kept_and_mismatch_stated(self):
        r = base()
        for s in r["samples"][5:9]:
            for c in s["compositions"]:
                k = "vpcs.network.arubacloud.com/ns/lb-syncedonly"
                c["children"][k] = {"absent": True, "ready": {"status": "Absent",
                                    "reason": "NotFound"}, "synced": {"status": "Absent"}}
                c["managed_recorded_but_absent"] = [k]
        p = run(with_record(r))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("D=-", p.stdout, "a flap duration must never be negative")
        self.assertRegex(p.stdout, r"MISMATCH: recorded in status.managed but NotFound "
                                   r"live in 4 of 51 sample\(s\): .*lb-syncedonly")

    def test_sampler_mirrors_rollup_rules(self):
        sys.path.insert(0, os.path.join(HERE, "..", "..", "scripts"))
        import condition_capture as cc

        calls = []

        class R:  # a kubectl result
            def __init__(self, rc, out="", err=""):
                self.returncode, self.stdout, self.stderr = rc, out, err

        class FakeKube:
            def json(self, *a):
                return {"metadata": {"name": "chain", "namespace": "ns"}, "status": {
                    "managedChildrenCount": 3, "managed": [
                        {"apiVersion": "network.arubacloud.com/v1", "resource": "vpcs",
                         "name": "ok", "namespace": "ns"},
                        {"apiVersion": "network.arubacloud.com/v1", "resource": "vpcs",
                         "name": "gone", "namespace": "ns"},
                        {"apiVersion": "network.arubacloud.com/v1", "resource": "",
                         "name": "no-resource"},
                        {"apiVersion": "a/b/c", "resource": "x", "name": "bad-api"},
                        # schema.ParseGroupVersion accepts both: the rollup GETs them
                        {"apiVersion": "/v1", "resource": "x", "name": "slash-lead"},
                        {"apiVersion": "v1/", "resource": "y", "name": "slash-trail"}]}}

            def run(self, *a, **k):
                calls.append(a)
                if "gone" in a:
                    return R(1, err='Error from server (NotFound): vpcs "gone" not found')
                return R(0, json.dumps({"kind": "Vpc", "metadata": {"name": "ok"}}))

        out = cc.sample_once(FakeKube(), [("comps.composition.krateo.io", "ns", "chain")])
        c = out["compositions"][0]
        fetched = [x[2] for x in calls]
        self.assertEqual(fetched, ["ok", "gone", "slash-lead", "slash-trail"],
                         "only a/b/c and the empty-field entry may be skipped")
        self.assertEqual([x[1] for x in calls][2:], ["x", "y.v1"])
        self.assertEqual(c["managed_state"], "present")
        self.assertEqual(len(c["managed_skipped_no_get"]), 2)
        self.assertEqual(len(c["managed_recorded_but_absent"]), 1)
        gone = [v for k, v in c["children"].items() if k.endswith("/gone")][0]
        self.assertTrue(gone["absent"])
        self.assertEqual(gone["ready"]["status"], "Absent")


FAKE_KUBECTL = """#!/bin/sh
# Stands in for kubectl: the composition CRD exists, and listing it returns no items.
case "$*" in
  *"get crd"*) echo '{"items":[{"spec":{"group":"composition.krateo.io","names":{"plural":"comps"}}}]}' ;;
  *"get comps"*) echo '{"items":[]}' ;;
  *) echo "unexpected kubectl call: $*" >&2; exit 9 ;;
esac
"""


class CrdExistsZeroInstances(unittest.TestCase):
    def test_exit_3_naming_crd_and_zero_instances(self):
        d = tempfile.mkdtemp()
        k = os.path.join(d, "kubectl")
        open(k, "w").write(FAKE_KUBECTL)
        os.chmod(k, 0o755)
        env = dict(os.environ, PATH=d + os.pathsep + os.environ["PATH"])
        p = subprocess.run([sys.executable, "-I", SCRIPT, "--context", "x",
                            "--duration", "1", "--interval", "1"],
                           capture_output=True, text=True, env=env)
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        self.assertIn("NOTHING FOUND", p.stderr)
        self.assertIn("CRD(s) exist", p.stderr)
        self.assertIn("comps.composition.krateo.io", p.stderr)
        self.assertIn("0 instances", p.stderr)
        self.assertEqual(p.stdout, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
