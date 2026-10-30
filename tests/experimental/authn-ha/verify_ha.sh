#!/usr/bin/env bash
# Verify authn 0.28.1 leader election with replicaCount=3, as the authn maintainers
# scoped it on authn#45: checks 1-4 only. Failover (delete the leader, watch the Lease
# move) is DELIBERATELY NOT HERE -- their own kind e2e already covered it and found the
# Lease moved with exactly one re-issue and no duplicate CSR. What they are missing is
# these four on a cluster that is not theirs, and check 3 is the one they care most
# about: a backfill racing three replicas is the plausible HA defect.
#
# The exact log strings are theirs, quoted verbatim:
#   "acquired leadership"  "standing by"  "identity annotation backfill done"
#
# Run only on a QUIET host. Every check here is a race, and a starved node makes a
# renewal deadline slip whether or not the release has a defect -- which would send a
# false leader-election regression report about someone else's code. A peer session
# nearly shipped loosened probe timings to four charts off exactly that mistake today.
set -uo pipefail
CLUSTER="${CLUSTER:-authn-ha}"
CTX="kind-${CLUSTER}"
export KUBECONFIG="${TMPDIR:-/tmp}/kind-${CLUSTER}.kubeconfig"
NS=krateo-system
WANT_REPLICAS="${WANT_REPLICAS:-3}"
# Any digest published in the 0.28.1 index is acceptable. containerd reports the INDEX
# digest in imageID even on arm64, so asserting the per-arch manifest digest aborts a
# run on a correct image -- which is how I lost a window earlier this week.
OK_DIGESTS="${OK_DIGESTS:-sha256:63850ed2 sha256:ec743654 sha256:a697272c}"
fail=0
k(){ kubectl --context "$CTX" "$@"; }
say(){ printf '\n=== %s ===\n' "$*"; }
ok(){ printf '  PASS  %s\n' "$*"; }
no(){ printf '  FAIL  %s\n' "$*"; fail=1; }
note(){ printf '  note  %s\n' "$*"; }

say "replicas and the image actually running"
pods=$(k get pods -n "$NS" -l app.kubernetes.io/name=authn \
         -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null | grep -c .)
[ "${pods:-0}" = "$WANT_REPLICAS" ] \
  && ok "${pods} authn pods" || { no "expected ${WANT_REPLICAS} authn pods, found ${pods:-0}"; }
POD_LIST=$(k get pods -n "$NS" -l app.kubernetes.io/name=authn \
             -o jsonpath='{range .items[*]}{.metadata.name}{" "}{end}' 2>/dev/null)
[ -n "$POD_LIST" ] || { no "could not list authn pods -- refusing to interpret anything below"; exit 1; }

# A restarted container's logs are GONE, so every count below would be taken from a
# partial record and read as "the line never appeared". Flagged by the authn
# maintainers. Where it happened, the `--previous` log is also read (see LOGS_OF).
RESTARTED=""
for p in $POD_LIST; do
  rc=$(k get pod "$p" -n "$NS" -o jsonpath='{.status.containerStatuses[0].restartCount}' 2>/dev/null)
  [ -n "$rc" ] || { no "$p: restartCount unreadable"; continue; }
  if [ "$rc" -gt 0 ]; then
    RESTARTED="$RESTARTED $p"
    note "$p has restartCount=${rc} -- its pre-restart log is gone; reading --previous too"
  fi
done
[ -n "$RESTARTED" ] && note "restarted pods:$RESTARTED (counts below span current + previous)"

# One read per pod, current log plus the previous container where one exists. Two
# greps over two separate reads can disagree if the pod writes between them.
# exact_msg <want> -- lines whose msg/message field is EXACTLY <want>.
#
# Suggested by the authn maintainers in place of `grep -v`, and better. Handles JSON
# ("msg": / "message":) and logfmt (msg= / message=), because they wrote `msg` in
# their log examples and `.message` in their jq suggestion, and picking one blind is
# how a selector silently matches nothing and reports a clean run.
#
# The CLOSING quote is mandatory in both forms. My first version made it optional and
# reproduced the exact prefix bug this exists to avoid: on logfmt
# `msg="identity annotation backfill done"` the optional quote let the boundary match
# the space before `done`, so the success line counted as an error -- the same wrong
# answer, one layer deeper. Verified against JSON/msg, JSON/message and logfmt.
exact_msg() {
  local want="$1"
  grep -E "\"(msg|message)\"[[:space:]]*:[[:space:]]*\"${want}\"|(^|[[:space:]])(msg|message)=\"${want}\""
}

# leader_values -- the leader= / "leader": values from `standing by` lines.
# Per the maintainers, "standing by" comes from client-go's OnNewLeader and fires only
# when the observed holderIdentity CHANGES, so a steady follower logs it once. More
# than one is diagnosable from these values, which is why they are extracted rather
# than counted:
#   * distinct non-empty values -> real leader changes, one should match a term
#   * leader="" -> graceful release (ReleaseOnCancel empties holderIdentity)
#   * the SAME value repeated -> a defect on their side, to be reported
leader_values() {
  grep 'standing by' \
    | grep -oE '"leader"[[:space:]]*:[[:space:]]*"[^"]*"|(^|[[:space:]])leader="[^"]*"' \
    | sed 's/.*"leader"[[:space:]]*:[[:space:]]*"//; s/.*leader="//; s/"$//'
}

# assert_log_format <pod> <log> -- the authn log IS one JSON object per line with a
# `message` field. Established from source by the maintainers, not inferred: main.go
# builds the logger with zerolog.New(os.Stdout) and nothing in the module sets
# zerolog.MessageFieldName, so zerolog's default "message" applies. Never "msg",
# never logfmt.
#
# Checked at runtime rather than trusted, because the whole value of knowing the
# format is that a violation becomes a loud finding instead of a silent mismatch. If
# a later release swaps the logger, this says so in one line; without it, every count
# below would quietly read as "the line never appeared".
assert_log_format() {
  local pod="$1" lg="$2" sample bad=0 n=0
  sample=$(printf '%s\n' "$lg" | grep -v '^[[:space:]]*$' | head -20)
  [ -n "$sample" ] || { note "$pod: no log lines to check the format against"; return 0; }
  while IFS= read -r line; do
    n=$((n + 1))
    printf '%s' "$line" | python3 -I -c 'import json,sys
d=json.load(sys.stdin)
raise SystemExit(0 if isinstance(d, dict) and "message" in d else 1)' 2>/dev/null || bad=$((bad + 1))
  done <<EOFMT
$sample
EOFMT
  if [ "$bad" = 0 ]; then
    note "$pod: log format confirmed -- ${n}/${n} sampled lines are JSON with a 'message' field"
  else
    no "$pod: ${bad} of ${n} sampled log lines are NOT JSON-with-message. The maintainers established this build logs zerolog JSON with 'message' (main.go:117, no MessageFieldName override), so a mismatch means the logger changed and EVERY count below is suspect, not merely absent."
  fi
}

LOGS_OF() {
  local pod="$1" cur prev
  cur=$(k logs "$pod" -n "$NS" --tail=-1 2>/dev/null)
  case "$RESTARTED" in *" $pod"*) prev=$(k logs "$pod" -n "$NS" --previous --tail=-1 2>/dev/null) ;; esac
  printf '%s\n%s\n' "${prev:-}" "$cur"
}
for p in $POD_LIST; do
  iid=$(k get pod "$p" -n "$NS" -o jsonpath='{.status.containerStatuses[0].imageID}' 2>/dev/null)
  [ -n "$iid" ] || { no "$p: imageID unreadable"; continue; }
  hit=no; for d in $OK_DIGESTS; do case "$iid" in *"$d"*) hit=yes;; esac; done
  [ "$hit" = yes ] && ok "$p runs a published 0.28.1 digest" || no "$p runs ${iid} -- not in the 0.28.1 index"
done

say "CHECK 1+2: exactly one leader, the rest standing by"
leaders=""; standby=""; backfilled=""; bf_errs=""; BF_ERR_TEXT=""; BF_DONE_TEXT=""
for p in $POD_LIST; do
  # Read each pod's log ONCE into a variable. Two greps over two reads can disagree if
  # the pod logs between them, and that disagreement would look like a double leader.
  lg=$(LOGS_OF "$p")
  if [ -z "$(printf '%s' "$lg" | tr -d '[:space:]')" ]; then
    no "$p: logs unreadable or empty (NOT the same as 'logged nothing')"; continue
  fi
  assert_log_format "$p" "$lg"
  l=$(printf '%s\n' "$lg" | grep -c 'acquired leadership')
  s=$(printf '%s\n' "$lg" | grep -c 'standing by')
  b=$(printf '%s\n' "$lg" | exact_msg 'identity annotation backfill done' | grep -c .)
  # The error line's msg is "identity annotation backfill" -- a PREFIX of the success
  # line, so a plain grep for it counts "backfill done" as an error. Exclude the
  # success form explicitly. Verified against both real log shapes.
  e=$(printf '%s\n' "$lg" | exact_msg 'identity annotation backfill' | grep -c .)
  # CROSS-CHECK the exact matcher against the cruder substring one. They must agree.
  #
  # The field name is no longer in doubt -- the maintainers read it out of main.go and
  # it is zerolog's default `message`, in JSON. So a disagreement here no longer means
  # "I guessed the field name wrong"; it means a line carrying this text does not have
  # the shape the source says it must, which is a REAL anomaly worth their attention.
  # Kept at their suggestion: per them, if `message` matches nothing on a 0.28.1 pod
  # that is a genuine absence, so the two readings diverging is information either way.
  e_sub=$(printf '%s\n' "$lg" | grep 'identity annotation backfill' | grep -vc 'backfill done')
  if [ "$e" != "$e_sub" ]; then
    no "$p: exact-msg (JSON 'message') found ${e} backfill errors, substring found ${e_sub} -- they DISAGREE. The source says this build logs zerolog JSON with 'message', so a line carrying that text in another shape is a real anomaly; treat this pod's backfill result as UNREAD and report the divergence."
  fi
  note "$(printf '%-34s leadership=%s standing_by=%s backfill_done=%s backfill_err=%s' \
            "$p" "$l" "$s" "$b" "$e")"
  # Per pod and per TERM, not globally: the closure runs once per leadership term, so
  # a pod that loses the Lease and wins it back logs both lines again from the same
  # pod and that is correct. Counting pods keeps a re-acquisition from reading as a
  # double leader; the per-pod counts above make the extra term visible.
  [ "$l" -gt 1 ] && note "$p acquired leadership ${l}x -- re-acquisition, not a second leader"
  # A follower logs `standing by` once per OBSERVED leader change, so >1 is a real
  # event rather than noise. Classify it from the leader= values instead of printing a
  # bare count a reader would have to guess at.
  if [ "$s" -gt 1 ]; then
    lvs=$(printf '%s\n' "$lg" | leader_values | tr '\n' '|' | sed 's/|$//')
    uniq_n=$(printf '%s\n' "$lg" | leader_values | grep -v '^$' | sort -u | grep -c .)
    note "$p logged 'standing by' ${s}x; leader values: [${lvs}] (${uniq_n} distinct non-empty)"
    case "|${lvs}|" in
      *"||"*) note "  an empty leader= is a graceful release (ReleaseOnCancel); authn will suppress it" ;;
    esac
    # NON-EMPTY occurrences, not the raw `standing by` count: an empty leader= is a
    # graceful release and counting it as an occurrence reports a normal shutdown as a
    # defect in someone else's code. verdict_test.sh caught exactly that.
    nonempty_n=$(printf '%s\n' "$lg" | leader_values | grep -vc '^$')
    if [ "$nonempty_n" -gt 1 ] && [ "$uniq_n" = "1" ]; then
      no "  $p logged the SAME non-empty leader= value ${nonempty_n}x -- the maintainers asked for this to be reported as a defect on their side"
    fi
  fi
  [ "$l" -gt 0 ] && leaders="$leaders $p"
  [ "$s" -gt 0 ] && standby="$standby $p"
  [ "$b" -gt 0 ] && backfilled="$backfilled $p"
  [ "$b" -gt 0 ] && BF_DONE_TEXT="$(printf '%s\n' "$lg" | grep 'identity annotation backfill done' | tail -1)"
  [ "$e" -gt 0 ] && bf_errs="$bf_errs $p"
  [ "$e" -gt 0 ] && BF_ERR_TEXT="${BF_ERR_TEXT}
$(printf '%s\n' "$lg" | grep 'identity annotation backfill' | grep -v 'backfill done' | head -2)"
done
nl=$(printf '%s' "$leaders" | wc -w | tr -d ' ')
ns=$(printf '%s' "$standby" | wc -w | tr -d ' ')
[ "$nl" = "1" ] && ok "exactly one pod acquired leadership:$leaders" \
                || no "$nl pods logged 'acquired leadership':$leaders"
[ "$ns" = "$((WANT_REPLICAS - 1))" ] && ok "$ns standing by:$standby" \
                || no "expected $((WANT_REPLICAS - 1)) standing by, got $ns:$standby"
# A pod in BOTH lists means the roles overlapped, which no correct election does.
# Unconditional: the earlier guard on a space in "$leaders" always matched, since the
# list is built as "$leaders $p" and so always carries a leading one.
for p in $leaders; do
  case "$standby" in *"$p"*) no "$p logged BOTH leadership and standing by";; esac
done

say "CHECK 3: Lease holder is the pod that logged leadership"
# Read the Lease, then MATCH it against the log evidence. Reading the holder alone
# proves a Lease exists, not that the right process holds it.
holder=$(k get lease authn-cert-renewal -n "$NS" -o jsonpath='{.spec.holderIdentity}' 2>&1)
case "$holder" in
  *NotFound*|*"not found"*) no "Lease authn-cert-renewal does not exist" ;;
  ""|*error*|*Error*)       no "could not READ the Lease (not the same as absent): $holder" ;;
  *) note "holderIdentity=${holder}"
     lp=$(printf '%s' "$leaders" | tr -s ' ' '\n' | grep -v '^$' | head -1)
     # holderIdentity is often <pod>_<uuid>, so match the pod-name PREFIX, not equality.
     case "$holder" in
       "$lp"|"$lp"_*) ok "Lease holder is the leader pod ($lp)" ;;
       *) no "Lease held by '${holder}' but '${lp}' logged leadership -- they disagree" ;;
     esac
     rt=$(k get lease authn-cert-renewal -n "$NS" -o jsonpath='{.spec.renewTime}' 2>/dev/null)
     note "renewTime=${rt:-<unreadable>}" ;;
esac

say "CHECK 4: backfill outcome -- done on the LEADER ONLY, or an error, or neither"
# The check authn asked for by name, split three ways from their reading of
# go/authn/main.go (the ownIdentity closure). identitybackfill.Run always produces
# exactly one of two lines, so there is NO legitimate silence:
#   * "identity annotation backfill done" with scanned/stamped/skipped -- success even
#     with nothing to do (an empty cluster logs scanned=0, not silence)
#   * msg "identity annotation backfill" with an `error` field -- a BACKFILL failure,
#     which is a different finding from an HA defect and must be reported as such
#   * neither -- either nobody won the election, or the logs were read too early; it
#     runs right after the leader's Ensure, usually within seconds
#
# The verdict comes from ONE read per pod, taken above. Two reads of a live pod's log
# can disagree if it writes between them, and I warned about exactly that in check 1
# before re-reading here anyway -- a second read could report the backfill on a pod
# that check 1 had classified as standing by, and the contradiction would look like an
# HA defect rather than my own instrument.
#
# There is exactly ONE deliberate exception, written down here so it stays an
# exception: the "neither line on any pod" branch re-reads once after 30s, because
# that outcome alone can mean "too early" rather than "broken".
bf="$backfilled"
nb=$(printf '%s' "$bf" | wc -w | tr -d ' ')
ne=$(printf '%s' "$bf_errs" | wc -w | tr -d ' ')

# "Neither line on any pod" is the one outcome that may simply be too early. Re-read
# ONCE after 30s before calling it, as the maintainers asked -- and only in that case,
# so a genuine failure is not retried into looking intermittent.
if [ "$nb" = "0" ] && [ "$ne" = "0" ]; then
  note "no backfill line yet on any pod -- it runs seconds after the leader's Ensure; re-reading once in 30s"
  sleep 30
  bf=""; bf_errs=""
  for p in $POD_LIST; do
    lg=$(LOGS_OF "$p")
    b=$(printf '%s\n' "$lg" | exact_msg 'identity annotation backfill done' | grep -c .)
    e=$(printf '%s\n' "$lg" | exact_msg 'identity annotation backfill' | grep -c .)
    [ "${b:-0}" -gt 0 ] && bf="$bf $p"
    [ "${b:-0}" -gt 0 ] && BF_DONE_TEXT="$(printf '%s\n' "$lg" | grep 'identity annotation backfill done' | tail -1)"
    [ "${e:-0}" -gt 0 ] && bf_errs="$bf_errs $p"
    [ "${e:-0}" -gt 0 ] && BF_ERR_TEXT="${BF_ERR_TEXT}
$(printf '%s\n' "$lg" | grep 'identity annotation backfill' | grep -v 'backfill done' | head -2)"
  done
  nb=$(printf '%s' "$bf" | wc -w | tr -d ' ')
  ne=$(printf '%s' "$bf_errs" | wc -w | tr -d ' ')
  note "after re-read: done on ${nb} pod(s), error on ${ne} pod(s)"
fi

if [ "$ne" -gt 0 ]; then
  # A distinct finding, NOT an HA defect. Quote it, as they asked.
  no "backfill ERRORED on$bf_errs -- a backfill failure, not a leader-election defect"
  printf '%s\n' "$BF_ERR_TEXT" | sed '/^$/d; s/^/        /'
elif [ "$nb" = "0" ]; then
  no "neither backfill line on any pod after a re-read -- per the maintainers there is no legitimate silence, so this is either no leader or a missing log"
elif [ "$nb" != "1" ]; then
  no "$nb pods logged the backfill:$bf -- it should be leader-only"
else
  lp=$(printf '%s' "$leaders" | tr -s ' ' '\n' | grep -v '^$' | head -1)
  case "$bf" in *"$lp"*) ok "backfill done on the leader only ($lp)" ;;
    *) no "backfill ran on$bf but the leader is $lp" ;; esac
  # The integer fields are the evidence that it actually scanned, not just logged.
  # Printed from the text captured during the single read above, NOT a fresh `k logs`:
  # this is display only and could not change the verdict, but re-reading here would
  # contradict the rule stated three lines up, and a rule with an exception nobody
  # wrote down is how the double-read crept in the first time.
  printf '%s\n' "${BF_DONE_TEXT:-<no done line captured>}" | sed 's/^/        /'
fi

say "CHECK 5: /serviceaccount/login returns 200 from EVERY replica, individually"
# Per POD IP, not through the Service. A Service-level 200 proves one replica answered
# and says nothing about the other two -- which is the question authn actually asked.
# Targeting the pod IP makes the responder's identity inherent rather than asserted
# from a header that could be wrong.
probe=$(k get pod call-probe -n "$NS" -o jsonpath='{.metadata.name}' 2>/dev/null)
if [ -z "$probe" ]; then
  no "no call-probe pod -- cannot reach pod IPs from inside the cluster; run snowplow_rig.sh first"
else
  port=$(k get svc -n "$NS" -l app.kubernetes.io/name=authn \
           -o jsonpath='{.items[0].spec.ports[0].targetPort}' 2>/dev/null)
  [ -n "$port" ] || port=8080
  for p in $POD_LIST; do
    ip=$(k get pod "$p" -n "$NS" -o jsonpath='{.status.podIP}' 2>/dev/null)
    [ -n "$ip" ] || { no "$p: podIP unreadable"; continue; }
    out=$(k exec -i -n "$NS" "$probe" -- sh -s <<INNER 2>&1
set -u
T=\$(cat /var/run/secrets/krateo.io/serviceaccount/token)
curl -sS -o /tmp/l.json -w 'status=%{http_code}\n' -X POST \
  -H "Authorization: Bearer \$T" "http://${ip}:${port}/serviceaccount/login"
echo "has_client_key=\$(grep -c 'client-key-data' /tmp/l.json)"
echo "has_token=\$(grep -c 'accessToken' /tmp/l.json)"
INNER
)
    st=$(printf '%s\n' "$out" | grep -o '^status=.*' | cut -d= -f2)
    ck=$(printf '%s\n' "$out" | grep -o '^has_client_key=.*' | cut -d= -f2)
    tk=$(printf '%s\n' "$out" | grep -o '^has_token=.*' | cut -d= -f2)
    if [ -z "$st" ]; then no "$p ($ip): no status returned -- $(printf '%s' "$out" | head -1)"; continue; fi
    [ "$st" = "200" ] && ok "$p ($ip) login 200" || no "$p ($ip) login $st"
    # authn#36: assert the ABSENCE. "I didn't see a key" is a different claim.
    [ "${ck:-x}" = "0" ] && ok "$p no client-key-data (asserted absent)" \
                         || no "$p client-key-data PRESENT -- #36 regressed on this replica"
    [ "${tk:-0}" -ge 1 ] && ok "$p returned an accessToken" || no "$p no accessToken in the body"
  done
fi

say "verdict: $([ $fail = 0 ] && echo ALL CHECKS PASS || echo FAILURES ABOVE)"
printf 'report these to authn#45: leader=%s standby=%s lease_holder=%s\n' \
  "${leaders:-none}" "${standby:-none}" "${holder:-unread}"
exit $fail
