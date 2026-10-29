#!/usr/bin/env bash
# Apply oasgen-provider's decision table to a Debug-enabled RDC log and say which of
# dispatch or counting is the suspect. Their line map, 0.36.0 delegated delete path:
#
#   mutate_restaction.go:50   DEBUG  Mutated via RESTAction
#   mutate_restaction.go:100  DEBUG  External resource still present after delete; holding finalizer
#   mutate_restaction.go:103  ERROR  did not remove the external resource
#   mutate_restaction.go:111  DEBUG  No get verb to verify deletion; trusting the successful delete
#   mutate_restaction.go:138  DEBUG  No get verb to verify deletion; trusting the delete result
#   mutate_restaction.go:171  WARN   notFoundBody predicate failed
#
# :100 immediately precedes :103, so the pair (:50, :100) discriminates:
#   :50 yes, :100 yes -> branch entered, snowplow returned -> the COUNTING is suspect
#   :50 no,  :100 yes -> resolveDeleteOutcome ran without the mutation -> DISPATCH is the defect
#   :50 no,  :100 no  -> earlier than either; dump everything
set -uo pipefail
LOG="${1:?usage: analyse_debug.sh <rdc-debug-log>}"
[ -r "$LOG" ] || { echo "cannot read $LOG"; exit 2; }

# `grep -c` ALREADY prints a count and exits 1 when that count is zero, so
# `|| echo 0` appends a second line and the function returns "0\n0". Every integer
# test below then fails with "integer expected" and the verdict falls through to
# "a combination the table does not cover" -- which is what it did on the DECISIVE
# row, turning the one answer this run exists to produce into a shrug.
n(){ local c; c=$(grep -c "$1" "$LOG" 2>/dev/null); printf '%s' "${c:-0}"; }

# Assert the log is actually a Debug log before interpreting ANY absence. Without
# this, every "no" below is indistinguishable from "Debug was off" -- which is the
# exact hole that made the previous run's read inconclusive, and it was my own
# invocation that caused it.
dbg=$(grep -o '"level":"[^"]*"' "$LOG" 2>/dev/null | sort -u | tr '\n' ' ')
echo "=== levels present: ${dbg:-<none>} ==="
case "$dbg" in
  *DEBUG*|*debug*) echo "    Debug IS on — absences below are meaningful" ;;
  *) echo "    NO DEBUG LEVEL PRESENT. Every absence below is uninterpretable."
     echo "    Re-run with RDC_DEBUG=1. Refusing to render a verdict."; exit 3 ;;
esac

mutated=$(n 'Mutated via RESTAction')
still=$(n 'still present after delete')
err=$(n 'did not remove the external resource')
nogetA=$(n 'trusting the successful delete')
nogetB=$(n 'trusting the delete result')
pred=$(n 'notFoundBody predicate failed')
echo
printf '  %-58s %s\n' ':50  Mutated via RESTAction'            "$mutated"
printf '  %-58s %s\n' ':100 External resource still present'   "$still"
printf '  %-58s %s\n' ':103 did not remove the external resource' "$err"
printf '  %-58s %s\n' ':111 trusting the successful delete'    "$nogetA"
printf '  %-58s %s\n' ':138 trusting the delete result'        "$nogetB"
printf '  %-58s %s\n' ':171 notFoundBody predicate failed'     "$pred"
echo
echo "=== verdict ==="
if   [ "$mutated" -gt 0 ] && [ "$still" -gt 0 ]; then
  echo "  branch ENTERED and snowplow RETURNED; verification held the finalizer."
  echo "  -> dispatch works. The snowplow-side COUNTING is the suspect."
elif [ "$mutated" -eq 0 ] && [ "$still" -gt 0 ]; then
  echo "  resolveDeleteOutcome ran WITHOUT the mutation."
  echo "  -> the delegated branch was SKIPPED. Dispatch is the defect."
elif [ "$mutated" -eq 0 ] && [ "$still" -eq 0 ]; then
  echo "  neither line present: the failure is EARLIER than either."
  echo "  -> send oasgen the whole Debug dump; do not guess."
else
  echo "  mutated=$mutated still=$still — a combination the table does not cover."
  echo "  -> report the counts rather than forcing them into a row."
fi

echo
echo "=== CREDENTIAL ABSENCE ASSERTION (-debug turns on request dumps) ==="
# oasgen says 0.36.0 is above the 0.26.1 redaction fix, so credential headers are
# redacted by name. ASSERT it rather than trust it: on 0.24.0 this same annotation
# printed 90 live bearer tokens in one controller's log. "I didn't see a token" is a
# different claim from "there is no token".
# Same `grep -c` trap as n(): it prints 0 AND exits 1, so `|| echo 0` makes "0\n0"
# and the -gt test errors. It errored into the SAFE side here, which is worse than
# failing loudly -- a PASS reached through a broken comparison is still a broken
# instrument reporting clear.
jwt=$(grep -cE 'Bearer ey[A-Za-z0-9_-]{10,}' "$LOG" 2>/dev/null); jwt=${jwt:-0}
auth=$(grep -ciE '^Authorization:' "$LOG" 2>/dev/null); auth=${auth:-0}
redact=$(grep -ciE 'Authorization: *(\*{3,}|REDACTED|<redacted>)' "$LOG" 2>/dev/null); redact=${redact:-0}
printf '  %-58s %s\n' 'Authorization header lines in the dump' "$auth"
printf '  %-58s %s\n' '  of which redacted'                    "$redact"
printf '  %-58s %s\n' 'live-looking "Bearer ey..." JWTs'       "$jwt"
if [ "$jwt" -gt 0 ]; then
  echo "  FAIL: $jwt live bearer token(s) in the dump — redaction did NOT hold on this build."
  echo "        Treat this log as a credential and do not attach it to an issue."
  exit 1
fi
echo "  PASS: no live bearer token in the dump (asserted absent, not merely unobserved)."
