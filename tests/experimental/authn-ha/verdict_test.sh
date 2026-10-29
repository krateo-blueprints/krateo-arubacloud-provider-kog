#!/usr/bin/env bash
# Tests the VERDICT LOGIC of verify_ha.sh against synthetic logs, with no cluster.
#
# The point: verify_ha.sh decides what gets reported on authn#45 about someone else's
# release. A screen that reports "all checks pass" without proving it can fail a
# known-bad election is a function that returns true. So every broken election below
# must FAIL, and the healthy one must PASS.
#
# The case that justifies the whole file is "backfill ran on a FOLLOWER": its counts
# are IDENTICAL to healthy (leaders=1 standby=2 backfill=1) and it must still fail,
# because the leader-match is what catches it. A count-only check passes it silently,
# and that is precisely the HA defect authn said they care most about.
set -uo pipefail
WANT_REPLICAS=3
rc=0
cases=0

run_case(){
  cases=$((cases + 1))
  local want="$1" name="$2"; shift 2
  local -a logs=("$@")
  local leaders="" standby="" backfilled="" bf_errs="" fail=0 i=0 p l s b e nl ns nb ne lp
  for lg in "${logs[@]}"; do
    i=$((i+1)); p="authn-pod-$i"
    l=$(printf '%s\n' "$lg" | grep -c 'acquired leadership')
    s=$(printf '%s\n' "$lg" | grep -c 'standing by')
    b=$(printf '%s\n' "$lg" | grep -c 'identity annotation backfill done')
    # The error line's msg is a PREFIX of the success line's, so the success form must
    # be excluded explicitly or "backfill done" is counted as an error.
    e=$(printf '%s\n' "$lg" | grep 'identity annotation backfill' | grep -vc 'backfill done')
    [ "$l" -gt 0 ] && leaders="$leaders $p"
    [ "$s" -gt 0 ] && standby="$standby $p"
    [ "$b" -gt 0 ] && backfilled="$backfilled $p"
    [ "$e" -gt 0 ] && bf_errs="$bf_errs $p"
  done
  nl=$(printf '%s' "$leaders" | wc -w | tr -d ' ')
  ns=$(printf '%s' "$standby" | wc -w | tr -d ' ')
  nb=$(printf '%s' "$backfilled" | wc -w | tr -d ' ')
  ne=$(printf '%s' "$bf_errs" | wc -w | tr -d ' ')
  # A backfill ERROR is a distinct finding from an HA defect, but it is still not a pass.
  [ "$ne" = "0" ] || fail=1
  [ "$nl" = "1" ] || fail=1
  [ "$ns" = "$((WANT_REPLICAS - 1))" ] || fail=1
  for p in $leaders; do case "$standby" in *"$p"*) fail=1;; esac; done
  [ "$nb" = "1" ] || fail=1
  if [ "$nb" = "1" ]; then
    lp=$(printf '%s' "$leaders" | tr -s ' ' '\n' | grep -v '^$' | head -1)
    case "$backfilled" in *"$lp"*) : ;; *) fail=1;; esac
  fi
  local got; got=$([ $fail = 0 ] && echo PASS || echo FAIL)
  if [ "$got" = "$want" ]; then
    printf '  ok    %-42s (%s) leaders=%s standby=%s backfill=%s\n' "$name" "$got" "$nl" "$ns" "$nb"
  else
    printf '  BAD   %-42s expected %s, got %s\n' "$name" "$want" "$got"; rc=1
  fi
}

run_case PASS "healthy: 1 leader, 2 standby, 1 backfill" \
  'acquired leadership
identity annotation backfill done' 'standing by' 'standing by'

run_case FAIL "split brain: two leaders" \
  'acquired leadership
identity annotation backfill done' 'acquired leadership' 'standing by'
run_case FAIL "backfill raced on all three" \
  'acquired leadership
identity annotation backfill done' 'standing by
identity annotation backfill done' 'standing by
identity annotation backfill done'
run_case FAIL "backfill ran on a FOLLOWER, not the leader" \
  'acquired leadership' 'standing by
identity annotation backfill done' 'standing by'
run_case FAIL "no leader at all" 'standing by' 'standing by' 'standing by'
run_case FAIL "backfill never ran" 'acquired leadership' 'standing by' 'standing by'
run_case FAIL "one pod logged BOTH roles" \
  'acquired leadership
standing by
identity annotation backfill done' 'standing by' 'standing by'

# Cases the authn maintainers supplied from go/authn/main.go. The first two are the
# substring trap: the error line's msg is a PREFIX of the success line's.
run_case FAIL "backfill ERRORED on the leader" \
  'acquired leadership
msg="identity annotation backfill" error="connection refused"' 'standing by' 'standing by'
run_case PASS "empty cluster: done with scanned=0 is still success" \
  'acquired leadership
msg="identity annotation backfill done" scanned=0 stamped=0 skipped=0' 'standing by' 'standing by'
run_case PASS "leader re-acquired its term: both lines twice, same pod" \
  'acquired leadership
identity annotation backfill done
acquired leadership
identity annotation backfill done' 'standing by' 'standing by'
run_case FAIL "done AND an error on the leader" \
  'acquired leadership
identity annotation backfill done
msg="identity annotation backfill" error="context deadline exceeded"' 'standing by' 'standing by'

# ---------------------------------------------------------------------------
# exact_msg and leader_values, the two helpers verify_ha.sh matches logs with.
# Both are tested against all THREE log shapes authn might emit, because the
# maintainers wrote `msg` in their log examples and `.message` in their jq
# suggestion, and a field name guessed wrong matches nothing and reports a clean run.
# ---------------------------------------------------------------------------
exact_msg() {
  local want="$1"
  grep -E "\"(msg|message)\"[[:space:]]*:[[:space:]]*\"${want}\"|(^|[[:space:]])(msg|message)=\"${want}\""
}
leader_values() {
  grep 'standing by' \
    | grep -oE '"leader"[[:space:]]*:[[:space:]]*"[^"]*"|(^|[[:space:]])leader="[^"]*"' \
    | sed 's/.*"leader"[[:space:]]*:[[:space:]]*"//; s/.*leader="//; s/"$//'
}

printf '\n--- exact_msg: the prefix must NOT bleed, on every log shape ---\n'
m_case(){
  cases=$((cases + 1))
  local name="$1" body="$2"
  local d e
  d=$(printf '%s\n' "$body" | exact_msg 'identity annotation backfill done' | grep -c .)
  e=$(printf '%s\n' "$body" | exact_msg 'identity annotation backfill' | grep -c .)
  if [ "$d" = 1 ] && [ "$e" = 1 ]; then
    printf '  ok    %-42s done=%s err=%s\n' "$name" "$d" "$e"
  else
    printf '  BAD   %-42s done=%s err=%s (want 1/1)\n' "$name" "$d" "$e"; rc=1
  fi
}
m_case "JSON with msg="      '{"level":"info","msg":"identity annotation backfill done","scanned":0}
{"level":"error","msg":"identity annotation backfill","error":"refused"}'
m_case "JSON with message="   '{"level":"info","message":"identity annotation backfill done","scanned":0}
{"level":"error","message":"identity annotation backfill","error":"refused"}'
m_case "logfmt with msg="     'time=t level=info msg="identity annotation backfill done" scanned=0
time=t level=error msg="identity annotation backfill" error="refused"'

printf '\n--- leader_values: >1 standing by is classified, not just counted ---\n'
l_case(){
  cases=$((cases + 1))
  local name="$1" want_uniq="$2" want_verdict="$3" body="$4"
  local u verdict
  u=$(printf '%s\n' "$body" | leader_values | grep -v '^$' | sort -u | grep -c .)
  # NON-EMPTY occurrences, not raw `standing by` lines. An empty leader= is a
  # graceful release (ReleaseOnCancel), so counting it as an occurrence turns a
  # normal shutdown into a reported defect in someone else's code -- which is
  # exactly what this case caught when the rule used the raw count.
  n=$(printf '%s\n' "$body" | leader_values | grep -vc '^$')
  if [ "$n" -gt 1 ] && [ "$u" = "1" ]; then verdict=REPORT; else verdict=OK; fi
  if [ "$u" = "$want_uniq" ] && [ "$verdict" = "$want_verdict" ]; then
    printf '  ok    %-42s distinct=%s -> %s\n' "$name" "$u" "$verdict"
  else
    printf '  BAD   %-42s distinct=%s/%s verdict=%s/%s\n' "$name" "$u" "$want_uniq" "$verdict" "$want_verdict"; rc=1
  fi
}
l_case "steady follower: one leader observed" 1 OK \
  'msg="standing by" leader="authn-pod-1_abc"'
l_case "real failover: two distinct leaders" 2 OK \
  'msg="standing by" leader="authn-pod-1_abc"
msg="standing by" leader="authn-pod-2_def"'
l_case "graceful release: empty then new leader" 1 OK \
  'msg="standing by" leader="authn-pod-1_abc"
msg="standing by" leader=""'
l_case "SAME leader re-logged -- authn defect" 1 REPORT \
  'msg="standing by" leader="authn-pod-1_abc"
msg="standing by" leader="authn-pod-1_abc"'
l_case "JSON shape, two distinct leaders" 2 OK \
  '{"msg":"standing by","leader":"authn-pod-1_abc"}
{"msg":"standing by","leader":"authn-pod-2_def"}'

printf '\n%s\n' "$([ $rc = 0 ] && echo "verdict logic behaves on all ${cases} cases" || echo 'VERDICT LOGIC IS WRONG')"
exit $rc
