#!/usr/bin/env bash
# Exchange an Aruba Cloud API key (client_id + client_secret) for a short-lived
# OAuth2 access token, as documented at
# https://arubacloud.github.io/api/docs/authentication/
#
# Run this interactively. Both values are PROMPTED rather than passed as
# arguments, so neither ends up in your shell history or in a process listing:
#
#     ./scripts/get-aruba-token.sh
#
# Output:
#   /tmp/aruba.token       the access token (mode 0600) -- expires in ~1 hour
#   /tmp/aruba.client_id   the client id (not secret), so a refresh is one step
#
# The client SECRET is never written to disk and is unset as soon as the request
# returns. Nothing here is printed except non-sensitive metadata.
#
# To load the token into the test cluster afterwards:
#   kubectl create secret generic arubacloud-token \
#     --from-file=token=/tmp/aruba.token --dry-run=client -o yaml | kubectl apply -f -
set -euo pipefail

TOKEN_FILE=${TOKEN_FILE:-/tmp/aruba.token}
ID_FILE=${ID_FILE:-/tmp/aruba.client_id}
TOKEN_URL=${TOKEN_URL:-https://mylogin.aruba.it/auth/realms/cmp-new-apikey/protocol/openid-connect/token}

for dep in curl jq; do
  command -v "$dep" >/dev/null || { echo "error: '$dep' is required but not installed" >&2; exit 1; }
done

umask 077

# Offer the previously used client id as the default, so refreshing an expired
# token is just two keystrokes.
default_id=""
[ -r "$ID_FILE" ] && default_id=$(cat "$ID_FILE")

if [ -n "$default_id" ]; then
  read -r -p "client_id [$default_id]: " ARUBA_ID
  ARUBA_ID=${ARUBA_ID:-$default_id}
else
  read -r -p "client_id: " ARUBA_ID
fi
[ -n "$ARUBA_ID" ] || { echo "error: client_id is required" >&2; exit 1; }

# -s: not echoed to the terminal.
read -rs -p "client_secret: " ARUBA_SECRET; echo
[ -n "$ARUBA_SECRET" ] || { echo "error: client_secret is required" >&2; exit 1; }

# --data-urlencode, not -d: client secrets routinely contain +, / and =, which a
# plain -d would send unencoded and turn into a misleading 'invalid_client'.
resp=$(curl -sS -X POST "$TOKEN_URL" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d grant_type=client_credentials \
  --data-urlencode "client_id=$ARUBA_ID" \
  --data-urlencode "client_secret=$ARUBA_SECRET") || {
    unset ARUBA_SECRET
    echo "error: request to $TOKEN_URL failed" >&2
    exit 1
  }
unset ARUBA_SECRET

if ! printf '%s' "$resp" | jq -e '.access_token' >/dev/null 2>&1; then
  echo "FAILED — no access_token in the response:" >&2
  # Keycloak's error body carries no credential material, so it is safe to show.
  printf '%s' "$resp" | jq -c '{error, error_description}' 2>/dev/null \
    || printf '%s\n' "$resp" >&2
  unset resp
  exit 1
fi

# jq -j (not -r): -r appends a trailing NEWLINE, which travels into the
# Kubernetes Secret and then into the Authorization header, where Go's net/http
# rejects the whole request with the opaque
#   net/http: invalid header field value for "Authorization"
# rest-dynamic-controller does not trim the credential, so the newline must not
# be written in the first place.
printf '%s' "$resp" | jq -j .access_token > "$TOKEN_FILE"
printf '%s' "$ARUBA_ID" > "$ID_FILE"
chmod 600 "$TOKEN_FILE"

expires=$(printf '%s' "$resp" | jq -r '.expires_in // "?"')
unset resp

# Fail loudly rather than write a credential that cannot be used in a header.
if LC_ALL=C grep -q '[[:space:]]' "$TOKEN_FILE"; then
  echo "error: token contains whitespace/newline -- it would produce an invalid Authorization header" >&2
  rm -f "$TOKEN_FILE"; exit 1
fi

echo "OK  token -> $TOKEN_FILE  ($(wc -c < "$TOKEN_FILE" | tr -d ' ') bytes, expires in ${expires}s)"
echo "    client_id -> $ID_FILE"
