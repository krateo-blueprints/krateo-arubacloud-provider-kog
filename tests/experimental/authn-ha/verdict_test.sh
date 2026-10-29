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

run_case(){
  local want="$1" name="$2"; shift 2
  local -a logs=("$@")
  local leaders="" standby="" backfilled="" fail=0 i=0 p l s b nl ns nb lp
  for lg in "${logs[@]}"; do
    i=$((i+1)); p="authn-pod-$i"
    l=$(printf '%s\n' "$lg" | grep -c 'acquired leadership')
    s=$(printf '%s\n' "$lg" | grep -c 'standing by')
    b=$(printf '%s\n' "$lg" | grep -c 'identity annotation backfill done')
    [ "$l" -gt 0 ] && leaders="$leaders $p"
    [ "$s" -gt 0 ] && standby="$standby $p"
    [ "$b" -gt 0 ] && backfilled="$backfilled $p"
  done
  nl=$(printf '%s' "$leaders" | wc -w | tr -d ' ')
  ns=$(printf '%s' "$standby" | wc -w | tr -d ' ')
  nb=$(printf '%s' "$backfilled" | wc -w | tr -d ' ')
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

printf '\n%s\n' "$([ $rc = 0 ] && echo 'verdict logic behaves on all 7 cases' || echo 'VERDICT LOGIC IS WRONG')"
exit $rc
