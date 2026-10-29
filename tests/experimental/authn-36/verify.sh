#!/usr/bin/env bash
# Verify authn#36 (fixed in #41, released in 0.28.0): /serviceaccount/login no longer
# returns a kubeconfig, so its response no longer contains a client PRIVATE KEY.
#
# Three checks, as the authn maintainers asked for them. The first ASSERTS AN ABSENCE,
# which is the whole point: the bug was that printing the response leaked a key, and
# "I didn't see a key" is not the same claim as "there is no key". So it greps for the
# field and expects NO match, rather than reading the body and not noticing.
set -uo pipefail
CLUSTER="${CLUSTER:-sp-rig}"
CTX="kind-${CLUSTER}"
export KUBECONFIG="${TMPDIR:-/tmp}/kind-${CLUSTER}.kubeconfig"
NS=krateo-system
RA="${1:-snowplow-911-probe}"
fail=0
k(){ kubectl --context "$CTX" "$@"; }
ok(){ printf '  PASS  %s\n' "$*"; }
no(){ printf '  FAIL  %s\n' "$*"; fail=1; }

echo "=== versions actually running (asserted, not assumed) ==="
for app in authn snowplow; do
  printf '  %-9s %s\n' "$app" "$(k get pods -n "$NS" -l app.kubernetes.io/name="$app" \
    -o jsonpath='{.items[0].status.containerStatuses[0].imageID}' 2>/dev/null)"
done

AU=$(k get svc -n "$NS" -l app.kubernetes.io/name=authn -o jsonpath='{.items[0].metadata.name}')
AUP=$(k get svc -n "$NS" "$AU" -o jsonpath='{.spec.ports[0].port}')
SP=$(k get svc -n "$NS" -l app.kubernetes.io/name=snowplow -o jsonpath='{.items[0].metadata.name}')
SPP=$(k get svc -n "$NS" "$SP" -o jsonpath='{.spec.ports[0].port}')

cat > /tmp/authn36.sh <<INNER
set -u
T=\$(cat /var/run/secrets/krateo.io/serviceaccount/token)
curl -sS -o /tmp/login.json -w 'http_status=%{http_code}\n' -X POST \
  -H "Authorization: Bearer \$T" \
  "http://${AU}.${NS}.svc.cluster.local:${AUP}/serviceaccount/login"
echo "keys=\$(tr ',{}' '\n\n\n' < /tmp/login.json | grep -o '"[a-zA-Z]*":' | tr -d '":' | sort -u | tr '\n' ' ')"
echo "has_data_field=\$(grep -c '"data"' /tmp/login.json)"
echo "has_client_key=\$(grep -c 'client-key-data' /tmp/login.json)"
echo "has_ca_data=\$(grep -c 'certificate-authority-data' /tmp/login.json)"
JWT=\$(tr ',{}' '\n\n\n' < /tmp/login.json | grep -o '"accessToken":"[^"]*"' | head -1 | sed 's/.*:"//; s/"\$//')
echo "jwt_len=\${#JWT}"
curl -sS -D /tmp/hdr.txt -o /dev/null -G \
  --data-urlencode 'apiVersion=templates.krateo.io/v1' \
  --data-urlencode 'resource=restactions' \
  --data-urlencode "namespace=${NS}" \
  --data-urlencode 'name=${RA}' \
  --data-urlencode 'extras={"spec":{"projectId":"69a55c8d5e6f0e14f14093ff","metadata":{"name":"ga-cs-server"}}}' \
  -H "Authorization: Bearer \$JWT" \
  "http://${SP}.${NS}.svc.cluster.local:${SPP}/call"
echo "call_status=\$(head -1 /tmp/hdr.txt | tr -d '\r')"
echo "outcomes=\$(grep -i 'X-Snowplow-Stage-Outcomes' /tmp/hdr.txt | tr -d '\r')"
INNER
out=$(k exec -i -n "$NS" call-probe -- sh -s < /tmp/authn36.sh 2>&1)
printf '%s\n' "$out" | sed 's/^/    /'

g(){ printf '%s\n' "$out" | grep -o "^$1=.*" | head -1 | cut -d= -f2-; }

echo "=== CHECK 1: login returns 200, and NO kubeconfig / NO private key ==="
[ "$(g http_status)" = "200" ] && ok "login 200" || no "login status $(g http_status)"
[ "$(g has_client_key)" = "0" ] && ok "no client-key-data in the body (asserted absent)" \
  || no "client-key-data STILL PRESENT -- #36 not fixed here"
[ "$(g has_data_field)" = "0" ] && ok "no data field" || no "data field still returned"
[ "$(g has_ca_data)" = "0" ] && ok "no certificate-authority-data" || no "CA data still returned"
case "$(g keys)" in
  *accessToken*groups*user*) ok "keys are accessToken/groups/user: $(g keys)" ;;
  *) no "unexpected key set: $(g keys)" ;;
esac

echo "=== CHECK 2: the <username>-clientconfig Secret still exists, with its four keys ==="
# -o json piped to a real parser, NOT jsonpath. The jsonpath form
#   {range $k,$v := .data}{$k}{" "}{end}
# is invalid -- kubectl rejects the comma with "unrecognized character in action:
# U+002C" -- and the check then reported "Secret missing or incomplete" for a Secret
# that was plainly there. A broken instrument returning a FAILURE is the mirror of the
# ones that return a confident pass, and it would have sent a false regression report
# upstream about somebody else's release.
sec=$(k get secret sp-caller-clientconfig -n "$NS" -o json 2>&1 \
      | python3 -I -c 'import json,sys
try: d=json.load(sys.stdin)
except Exception as e: print("UNREADABLE:", e); raise SystemExit
print(" ".join(sorted((d.get("data") or {}).keys())))' )
case "$sec" in
  UNREADABLE*|"") no "could not READ the Secret (not the same as it being absent): $sec" ;;
  *client-key-data*) ok "Secret present, keys: $sec" ;;
  *) no "Secret present but missing client-key-data: $sec" ;;
esac
ann=$(k get secret sp-caller-clientconfig -n "$NS" -o jsonpath='{.metadata.annotations}' 2>/dev/null)
printf '  note  annotations: %s\n' "${ann:-<none>}"

echo "=== CHECK 3: a snowplow /call with that JWT still works ==="
[ -n "$(g jwt_len)" ] && [ "$(g jwt_len)" -gt 100 ] && ok "JWT parsed, length $(g jwt_len)" \
  || no "no usable JWT (length $(g jwt_len))"
case "$(g call_status)" in *200*) ok "/call $(g call_status)";; *) no "/call $(g call_status)";; esac
printf '  %s\n' "$(g outcomes)"
echo "=== verdict: $([ $fail = 0 ] && echo ALL CHECKS PASS || echo FAILURES ABOVE) ==="
exit $fail
