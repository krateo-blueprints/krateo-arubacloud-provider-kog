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
for p in $POD_LIST; do
  iid=$(k get pod "$p" -n "$NS" -o jsonpath='{.status.containerStatuses[0].imageID}' 2>/dev/null)
  [ -n "$iid" ] || { no "$p: imageID unreadable"; continue; }
  hit=no; for d in $OK_DIGESTS; do case "$iid" in *"$d"*) hit=yes;; esac; done
  [ "$hit" = yes ] && ok "$p runs a published 0.28.1 digest" || no "$p runs ${iid} -- not in the 0.28.1 index"
done

say "CHECK 1+2: exactly one leader, the rest standing by"
leaders=""; standby=""; backfilled=""
for p in $POD_LIST; do
  # Read each pod's log ONCE into a variable. Two greps over two reads can disagree if
  # the pod logs between them, and that disagreement would look like a double leader.
  lg=$(k logs "$p" -n "$NS" --tail=-1 2>/dev/null)
  if [ -z "$lg" ]; then no "$p: logs unreadable (NOT the same as 'logged nothing')"; continue; fi
  l=$(printf '%s\n' "$lg" | grep -c 'acquired leadership')
  s=$(printf '%s\n' "$lg" | grep -c 'standing by')
  b=$(printf '%s\n' "$lg" | grep -c 'identity annotation backfill done')
  note "$(printf '%-34s leadership=%s standing_by=%s backfill=%s' "$p" "$l" "$s" "$b")"
  [ "$l" -gt 0 ] && leaders="$leaders $p"
  [ "$s" -gt 0 ] && standby="$standby $p"
  [ "$b" -gt 0 ] && backfilled="$backfilled $p"
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

say "CHECK 4: 'identity annotation backfill done' on the LEADER ONLY"
# The check authn asked for by name. A backfill running on all three is the defect.
#
# Reuses the SINGLE log read from above rather than running `k logs` again. Two reads
# of a live pod's log can disagree if it writes between them, and I warned about
# exactly that two checks ago before doing it here -- a second read could report the
# backfill on a pod that check 1 had already classified as standing by, and the
# contradiction would look like an HA defect rather than my own instrument.
bf="$backfilled"
nb=$(printf '%s' "$bf" | wc -w | tr -d ' ')
if [ "$nb" = "0" ]; then
  no "no pod logged the backfill -- either it has not run yet or the string changed"
elif [ "$nb" != "1" ]; then
  no "$nb pods ran the backfill:$bf -- it should be leader-only"
else
  lp=$(printf '%s' "$leaders" | tr -s ' ' '\n' | grep -v '^$' | head -1)
  case "$bf" in *"$lp"*) ok "backfill ran on the leader only ($lp)" ;;
    *) no "backfill ran on$bf but the leader is $lp" ;; esac
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
