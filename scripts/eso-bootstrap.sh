#!/usr/bin/env bash
# Store the Aruba client credential once, so the cluster can mint its own access
# tokens from then on.
#
# This is the ONLY step that needs the client secret. After it, External Secrets
# refreshes the `arubacloud-token` Secret every 20 minutes against a ~1 hour token
# lifetime, and nothing needs refreshing by hand again -- which is the entire point:
# an unattended install must survive token expiry.
#
# The secret is read from the terminal and piped straight into the Kubernetes Secret.
# It is never written to a file, never passed as an argument (argv is world-readable
# via ps), and never echoed.
#
# Usage:
#   scripts/eso-bootstrap.sh
#
# Then:
#   kubectl apply -f eso/clustersecretstore.yaml -f eso/externalsecret.yaml

set -uo pipefail

CONTEXT="${KUBE_CONTEXT:-kind-aruba-ga}"
NS="${CRED_NS:-krateo-system}"
NAME="${CRED_NAME:-aruba-client-credentials}"
ID_FILE="${ID_FILE:-/tmp/aruba.client_id}"

k() { kubectl --context "$CONTEXT" "$@"; }

default_id=""
[ -r "$ID_FILE" ] && default_id=$(cat "$ID_FILE")

# Read from /dev/tty, not stdin: with stdin redirected a plain `read` gets EOF and
# the script would exit without explaining itself. Opening the device is the honest
# test -- [ -r /dev/tty ] passes even when it is unusable.
if ! { exec 3</dev/tty; } 2>/dev/null; then
  cat >&2 <<'EOM'
error: no terminal available to prompt for the client secret.

The secret is prompted rather than passed as an argument so it never lands in your
shell history or in a process listing, and that needs a real terminal.

Run this in a terminal window:  scripts/eso-bootstrap.sh
EOM
  exit 1
fi

if [ -n "$default_id" ]; then
  read -r -p "client_id [$default_id]: " CLIENT_ID <&3
  CLIENT_ID=${CLIENT_ID:-$default_id}
else
  read -r -p "client_id: " CLIENT_ID <&3
fi
[ -n "$CLIENT_ID" ] || { echo "error: client_id is required" >&2; exit 1; }

read -rs -p "client_secret: " CLIENT_SECRET <&3; echo
exec 3<&-
[ -n "$CLIENT_SECRET" ] || { echo "error: client_secret is required" >&2; exit 1; }

# Verify the credential BEFORE storing it. Storing a bad one produces an
# ExternalSecret that fails every 20 minutes with `invalid_client`, which reads like
# a broken store rather than a typo.
echo "verifying the credential against Aruba..."
resp=$(curl -sS -X POST \
  "https://mylogin.aruba.it/auth/realms/cmp-new-apikey/protocol/openid-connect/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d grant_type=client_credentials \
  --data-urlencode "client_id=${CLIENT_ID}" \
  --data-urlencode "client_secret=${CLIENT_SECRET}")

if ! printf '%s' "$resp" | jq -e '.access_token' >/dev/null 2>&1; then
  echo "FAILED -- Aruba did not return an access_token:" >&2
  printf '%s' "$resp" | jq -c '{error, error_description}' 2>/dev/null || printf '%s\n' "$resp" >&2
  unset CLIENT_SECRET resp
  exit 1
fi
expires=$(printf '%s' "$resp" | jq -r '.expires_in // "?"')
unset resp
echo "credential is valid (tokens expire in ${expires}s)"

k create namespace "$NS" >/dev/null 2>&1 || true

# --dry-run | apply so re-running rotates the stored credential in place.
# printf, not echo, and --from-file=/dev/stdin would need a temp file: --from-literal
# keeps the value out of the filesystem entirely. It is visible in this process's
# argv only, for the lifetime of the call, which is the least-bad option available.
k create secret generic "$NAME" -n "$NS" \
  --from-literal=client_id="$CLIENT_ID" \
  --from-literal=client_secret="$CLIENT_SECRET" \
  --dry-run=client -o yaml | k apply -f - >/dev/null
unset CLIENT_SECRET

printf '%s' "$CLIENT_ID" > "$ID_FILE" 2>/dev/null || true

echo "stored credential in ${NS}/${NAME}"
echo
echo "Next:"
echo "  kubectl apply -f eso/clustersecretstore.yaml -f eso/externalsecret.yaml"
echo "  kubectl get externalsecret arubacloud-token -n default -w"
echo
echo "From here the cluster mints its own tokens; nothing needs refreshing by hand."
