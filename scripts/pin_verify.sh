#!/usr/bin/env bash
# Reproduce the GA evidence on the RELEASED stack, not the local builds.
#
# Every live verdict in docs/coverage.md was measured on `kind-aruba-110`, which runs
# chart 0.24.0 with two locally built images -- and whose provider Deployment has
# since been patched away from its own Helm release. The install docs tell users to
# install the CI pin instead. No tier depends on unreleased code (responseTransform is
# in 0.27.1), but "it works on the pin" was an inference, not a measurement.
#
# This builds a cluster that matches CI exactly, with NO image overrides, and runs the
# free network-security chain on it: four resources, four drift blocks, zero cost. If
# create/observe/drift/delete all hold there, the evidence transfers.
#
# The client credential is NOT prompted for. It already exists in the evidence
# cluster, so it is piped cluster-to-cluster and never touches disk, argv or a log.
#
# Usage:
#   scripts/pin_verify.sh            # build, verify, and LEAVE the cluster up
#   KEEP=0 scripts/pin_verify.sh     # delete the cluster afterwards
set -uo pipefail

PIN="${OASGEN_VERSION:-0.36.0}"              # 0.36.0 is a FLOOR, not a preference:
                                             # `profile` is in the provider list, and
                                             # 0.35.0 is the first release that can
                                             # PARSE profile.json (libopenapi v0.21.9)
                                             # while 0.36.0 carries oasgen#236, without
                                             # which AiKey's PUT silently drops
                                             # location and tags. Below 0.36.0 AiKey is
                                             # absent or wrong -- see scripts/providers.py.
NODE_IMAGE="${NODE_IMAGE:-kindest/node:v1.36.4}"    # same pin as CI; see validate.yaml
                                                    # 1.36 is REQUIRED by chart 0.28.0+
ESO_VERSION="${ESO_VERSION:-2.11.0}"               # as installed on the evidence cluster
CLUSTER="${CLUSTER:-aruba-pin}"
CTX="kind-${CLUSTER}"
export CTX
SRC_CTX="${SRC_CONTEXT:-kind-aruba-110}"           # where the credential already lives
CHAIN="${CHAIN:-tests/ga/chains/network-security.yaml}"
# The directory of RestDefinitions to apply. Point it at a prepared copy to try an
# experimental definition without putting it in restdefinitions/, where CI and every
# other run would pick it up. See tests/experimental/.
#
# It is a whole DIRECTORY and not an "apply this one afterwards" overlay, which was
# the first attempt and does not work: configurationFields are IMMUTABLE once the CRD
# has been generated, so applying a definition that narrows them over one already
# admitted is rejected outright --
#   spec.resource.configurationFields: Invalid value: ConfigurationFields are
#   immutable, you cannot change them once the CRD has been generated
# The experimental definition has to be the only one the cluster ever sees.
RESTDEFS_DIR="${RESTDEFS_DIR:-restdefinitions/}"
# Install the charts from a LOCAL directory and run LOCALLY BUILT images instead of
# the released ones. Everything this script exists to prove depends on using neither,
# so all three are empty by default and the log shouts when they are not.
#
# They exist for one job: testing an unreleased controller fix end to end against the
# live API. oasgen#218 (notFoundBody never evaluated on the delete path) is only
# observable that way -- the unit tests cover the helper, the fix is in the wiring,
# and the predicate it switches on is ours.
#
# CHART_SRC is a directory holding oasgen-provider/ and oasgen-provider-crds/. The
# repo's own Chart.yaml carries CHART_VERSION/APP_VERSION placeholders that are
# substituted at release time, so a checkout cannot be installed as-is -- substitute
# them in your copy first (helm rejects them as non-semver).
#
# PROVIDER_IMAGE and RDC_IMAGE take a full registry/repository:tag reference and are
# loaded into the kind node with `kind load docker-image`, so pullPolicy is forced to
# Never: with IfNotPresent a typo in the tag reaches the registry and the failure
# looks like a network problem.
CHART_SRC="${CHART_SRC:-}"
PROVIDER_IMAGE="${PROVIDER_IMAGE:-}"
RDC_IMAGE="${RDC_IMAGE:-}"
# Turn on REST_CONTROLLER_DEBUG in every generated controller. The lines that prove
# which branch of RDC's delete verification ran are all log.Debug, so without this a
# clean teardown and a teardown that silently fell back to trusting the DELETE are
# indistinguishable from the outside. It renders into each controller's ConfigMap via
# rdc.env, so it reaches controllers oasgen generates later too.
#
# This dumps HTTP bodies. Safe from 0.26.1 on; below that the dump included the
# Authorization header (oasgen#214), so do not set it on an older stack.
RDC_DEBUG="${RDC_DEBUG:-0}"
CHAIN_ARGS="${CHAIN_ARGS:-}"                       # e.g. --with-billable
# Install Snowplow and the CloudServer RESTActions. OFF by default: the free
# network-security chain does not need them, and snowplow is a second deployment to
# wait for. Required for any chain touching CloudServer, whose update and delete are
# delegated through *ApiRef -- without it RDC hard-errors with "no snowplow client is
# configured" and the CR wedges on its finalizer with a live billable server behind
# it. This is docs/ga-readiness.md P0-3, which is why those RESTActions had never
# executed before this script learned to install their dependency.
WITH_SNOWPLOW="${WITH_SNOWPLOW:-0}"
SNOWPLOW_VERSION="${SNOWPLOW_VERSION:-1.12.57}"
# The image digest(s) the chart MUST end up running, space-separated: the check
# passes if the running image matches ANY of them. A tag is a mutable pointer, so
# "we tested 1.12.57" is only true if the bits that ran are the bits that were
# published -- asserted below rather than assumed. Empty disables the check.
#
# WHY A LIST AND NOT ONE DIGEST. A multi-arch tag has an INDEX digest and one
# MANIFEST digest per platform, and which one you see depends on who is reporting.
# containerd's `imageID` on this kubelet reports the INDEX digest even on arm64,
# which cost one aborted run: the host is arm64, so the arm64 manifest digest looked
# like the right thing to assert, and the image was correct all along. Pass every
# digest published for the version and let the match decide, rather than predicting
# which one the runtime will name.
#
# Published for snowplow 1.12.57 (tag at commit 408c9af4):
#   index        sha256:1a1a8ee997ad084462eba462ca01a2715970203dfcb469b62ba084088328592b
#   linux/amd64  sha256:bff1e1e20a94c6b4248a1923cf47f83f0ac2410c87e78bdc9485bfca73b43519
#   linux/arm64  sha256:162659843afbe56b59562b16274f394cf16177a619c613f63b6d926ec8c9bfd6
SNOWPLOW_DIGEST="${SNOWPLOW_DIGEST:-}"
AUTHN_VERSION="${AUTHN_VERSION:-0.27.3}"
# The endpoint the RESTActions call. Same host the Configurations use; named here
# rather than inherited, because an empty server-url fails at delegation time
# with a live resource already created.
ARUBA_API="${ARUBA_API:-https://api.arubacloud.com}"
KEEP="${KEEP:-1}"

say() { printf '\n=== %s ===\n' "$*"; }
# Split registry/repository:tag into --set arguments for one of the chart's image
# blocks ("image" or "rdc.image"). Appends to the HELM_SETS array.
# Split an image ref into registry/repository/tag for the chart's values.
#
# A ref with NO registry -- e.g. `rest-dynamic-controller:261-candidate`, which is
# what `docker build -t` produces by default -- has no `/`, so both ${ref%%/*} and
# ${ref#*/} return the WHOLE string: registry and repository would each become
# `rest-dynamic-controller`, and the chart would render
# `rest-dynamic-controller/rest-dynamic-controller:261-candidate`, which does not
# exist. The symptom is an ImagePullBackOff on an image that is present on the node,
# and the hunt starts in the wrong place.
#
# Latent until someone passes a locally built tag, which is exactly when this is used:
# testing an unreleased controller fix. Found while running oasgen's #261 candidate,
# and worked around at the time by retagging under the expected repository -- fixed
# properly here so the next person does not need to know.
img_sets() {
  local prefix="$1" ref="$2" reg repo tag
  tag="${ref##*:}"
  ref="${ref%:*}"
  case "$ref" in
    */*) reg="${ref%%/*}"; repo="${ref#*/}" ;;
    *)   # no registry component: the whole ref is the repository
         reg=""; repo="$ref" ;;
  esac
  if [ -n "$reg" ]; then
    HELM_SETS+=(--set "${prefix}.registry=${reg}")
  else
    # The chart joins registry and repository with a slash, so an empty registry must
    # be passed explicitly as empty rather than left at the chart's default.
    HELM_SETS+=(--set-string "${prefix}.registry=")
  fi
  HELM_SETS+=(--set "${prefix}.repository=${repo}"
              --set "${prefix}.tag=${tag}"
              --set "${prefix}.pullPolicy=Never")
}
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# Keep kind OUT of the shared kubeconfig.
#
# `kind create` and `kind delete` write to ~/.kube/config when nothing says
# otherwise, and they also move `current-context`. On a machine where other
# sessions are working that is a side effect on someone else's tooling, not just
# ours: a run here left the shared config with NO current-context at all and a
# peer had to restore it by hand. Nothing about building a throwaway cluster
# should reach into a file other people depend on.
#
# So kind reads and writes its OWN file, named after the cluster, and KUBECONFIG
# points at that file ALONE.
#
# An earlier version of this put the user's config second in the list so that
# ${SRC_CTX} would resolve. That worked, but it left the shared file in scope for
# every kubectl and helm call in a script whose whole job is creating and
# destroying clusters -- one `kubectl config use-context` anywhere and the user's
# default moves. The credential read is ONE command, so it names the shared file
# explicitly (SRC_KUBECONFIG) instead, and nothing else can reach it.
PIN_KUBECONFIG="${PIN_KUBECONFIG:-${TMPDIR:-/tmp}/kind-${CLUSTER}.kubeconfig}"
SRC_KUBECONFIG="${SRC_KUBECONFIG:-${HOME}/.kube/config}"
export KUBECONFIG="${PIN_KUBECONFIG}"

cleanup() {
  if [ "$KEEP" = "0" ]; then
    say "deleting cluster ${CLUSTER}"
    kind delete cluster --name "$CLUSTER" --kubeconfig "$PIN_KUBECONFIG" >/dev/null 2>&1
    rm -f "$PIN_KUBECONFIG"
  else
    printf '\ncluster %s left up (KEEP=0 to delete it)\n' "$CLUSTER"
    printf 'its kubeconfig: %s\n' "$PIN_KUBECONFIG"
    printf 'reach it with: KUBECONFIG=%s kubectl --context %s get nodes\n' \
           "$PIN_KUBECONFIG" "$CTX"
  fi
}
trap cleanup EXIT

for bin in kind helm kubectl python3; do
  command -v "$bin" >/dev/null || die "$bin is not on PATH"
done
# Distinguish "the credential is absent" from "the source cluster did not answer".
# These are different problems with different fixes, and `>/dev/null 2>&1 || die`
# reported both as the first: the evidence cluster's apiserver intermittently returns
#     Unable to connect to the server: net/http: TLS handshake timeout
# under load, and a run aborted claiming the credential did not exist while it was
# sitting there, 9 days old, two keys. The third instance today of a check that cannot
# tell a failed question from a negative answer -- see docs/coverage.md, "What does
# not count as evidence".
#
# stderr is CAPTURED rather than discarded, so the message can be shown and matched.
src_err=$(kubectl --kubeconfig "$SRC_KUBECONFIG" --context "$SRC_CTX" \
  get secret aruba-client-credentials -n krateo-system 2>&1 >/dev/null)
if [ -n "$src_err" ]; then
  case "$src_err" in
    *NotFound*|*"not found"*)
      die "no aruba-client-credentials in ${SRC_CTX}/krateo-system to copy
  The credential is genuinely absent. Create it with scripts/eso-bootstrap.sh." ;;
    *)
      die "could not ASK ${SRC_CTX} whether aruba-client-credentials exists:
    ${src_err}
  This is not the same as the credential being missing. The evidence cluster's
  apiserver is unreachable or overloaded -- check it is running and retry." ;;
  esac
fi

say "creating ${CLUSTER} on ${NODE_IMAGE}"
if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  echo "    already exists, reusing"
else
  kind create cluster --name "$CLUSTER" --image "$NODE_IMAGE" \
    --kubeconfig "$PIN_KUBECONFIG" || die "kind create failed"
fi

say "installing external-secrets ${ESO_VERSION}"
helm --kube-context "$CTX" repo add external-secrets \
  https://charts.external-secrets.io >/dev/null 2>&1
helm --kube-context "$CTX" repo update external-secrets >/dev/null 2>&1
helm --kube-context "$CTX" upgrade --install external-secrets \
  external-secrets/external-secrets --version "$ESO_VERSION" \
  --namespace external-secrets --create-namespace --wait --timeout 5m \
  || die "external-secrets install failed"

say "copying the client credential from ${SRC_CTX} (never touches disk)"
kubectl --context "$CTX" create namespace krateo-system >/dev/null 2>&1
# -o yaml straight into apply. The pipe is the point: the secret is never written to a
# file, never passed as an argument, and never echoed.
kubectl --kubeconfig "$SRC_KUBECONFIG" --context "$SRC_CTX" \
  get secret aruba-client-credentials -n krateo-system -o yaml \
  | python3 -c '
import sys, yaml
d = yaml.safe_load(sys.stdin)
m = d["metadata"]
for k in ("resourceVersion", "uid", "creationTimestamp", "managedFields",
          "ownerReferences", "selfLink", "generation"):
    m.pop(k, None)
# The labels are load-bearing: ESO caches only labelled Secrets, and the webhook
# provider needs its own. Copying without them yields a bare "not found".
labels = m.setdefault("labels", {})
labels["reconcile.external-secrets.io/managed"] = "true"
labels["external-secrets.io/type"] = "webhook"
yaml.safe_dump(d, sys.stdout)
' | kubectl --context "$CTX" apply -f - >/dev/null || die "credential copy failed"
echo "    copied"

say "applying the ESO store and the token ExternalSecret"
kubectl --context "$CTX" apply -f eso/clustersecretstore.yaml -f eso/externalsecret.yaml \
  || die "eso manifests failed"
echo "    waiting for ESO to mint a token"
for i in $(seq 1 40); do
  if kubectl --context "$CTX" get secret arubacloud-token -n default \
       -o jsonpath='{.data.token}' 2>/dev/null | grep -q .; then
    echo "    arubacloud-token present after ${i} checks"; break
  fi
  sleep 15
  [ "$i" = 40 ] && die "ESO never produced arubacloud-token -- check the credential"
done

if [ "$WITH_SNOWPLOW" = "1" ]; then
  # authn FIRST, and its CRDs before it. Not optional and not ours to skip:
  # snowplow's seed-authn.yaml renders a serviceaccount.authn.krateo.io CR with no
  # opt-in flag, and its own comment says the chart "FAILS TO INSTALL on a cluster
  # lacking the krateo-authn-crd ... That is INTENDED -- the platform install
  # pipeline sequences authn BEFORE snowplow". pin_verify builds a minimal cluster
  # rather than running that pipeline, so it has to sequence them itself.
  say "installing authn-crds ${AUTHN_VERSION} (snowplow's hard dependency)"
  helm --kube-context "$CTX" upgrade --install authn-crds \
    oci://ghcr.io/krateo-platformops/charts/authn-crds --version "$AUTHN_VERSION" \
    --namespace krateo-system --create-namespace --wait --timeout 5m \
    >/dev/null || die "authn-crds install failed"
  # authn IS REQUIRED. An earlier version of this script said it was not, on the
  # strength of snowplow coming up 1/1 with readyz=200 and only the CRDs present.
  # That tested whether snowplow STARTS, not whether it ANSWERS. When the CloudServer
  # chain finally drove the path, snowplow refused the only endpoint we use:
  #     resolving delete RESTAction .../cloudserver-delete:
  #     snowplow returned 401: {"message":"missing authorization header","code":401}
  # /call needs an authn-issued token. Without authn every *ApiRef delegation fails
  # and the CR wedges on its finalizer -- correctness, not latency.
  say "generating authn's RSA signing key"
  # The chart mounts this Secret and does NOT create it; the platform installer
  # normally does. Its format is documented in authn's own values.yaml, so none of
  # this is guesswork: a PEM-encoded RSA private key (RS256) under the key
  # private.pem, mounted at jwt.mountPath and read as JWT_SIGN_KEY_FILE.
  #
  # Generated into a shell variable and piped to the apiserver -- it never reaches
  # disk, argv or this log, the same handling as the Aruba credential. A throwaway
  # key for a throwaway cluster: it signs tokens only snowplow in this cluster
  # validates, via the JWKS authn publishes from the matching public key.
  AUTHN_KEY="$(openssl genrsa 2048 2>/dev/null)"
  [ -n "$AUTHN_KEY" ] || die "could not generate an RSA key -- is openssl present?"
  kubectl --context "$CTX" create secret generic authn-jwt-signing-key \
    -n krateo-system --from-literal=private.pem="$AUTHN_KEY" \
    --dry-run=client -o yaml | kubectl --context "$CTX" apply -f - >/dev/null \
    || die "could not create the authn signing-key Secret"
  unset AUTHN_KEY

  say "installing authn ${AUTHN_VERSION}"
  helm --kube-context "$CTX" upgrade --install authn \
    oci://ghcr.io/krateo-platformops/charts/authn --version "$AUTHN_VERSION" \
    --namespace krateo-system --create-namespace --wait --timeout 10m \
    --set service.type=ClusterIP \
    >/dev/null || die "authn install failed"

  AUTHN_SVC="$(kubectl --context "$CTX" get svc -n krateo-system \
    -l app.kubernetes.io/name=authn -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)"
  [ -n "$AUTHN_SVC" ] || die "authn installed but no service found to point RDC at"
  AUTHN_PORT="$(kubectl --context "$CTX" get svc -n krateo-system "$AUTHN_SVC" \
    -o jsonpath='{.spec.ports[0].port}')"
  AUTHN_URL="http://${AUTHN_SVC}.krateo-system.svc.cluster.local:${AUTHN_PORT}"
  say "  authn at ${AUTHN_URL}"

  say "installing snowplow-crds ${SNOWPLOW_VERSION} (defines kind: RESTAction)"
  helm --kube-context "$CTX" upgrade --install snowplow-crds \
    oci://ghcr.io/krateo-platformops/charts/snowplow-crds --version "$SNOWPLOW_VERSION" \
    --namespace krateo-system --create-namespace --wait --timeout 5m \
    >/dev/null || die "snowplow-crds install failed"
  say "installing snowplow ${SNOWPLOW_VERSION} (CloudServer's *ApiRef dependency)"
  say "  ClusterIP (kind has no LB); first boot prewarms for ~8m before readiness"
  helm --kube-context "$CTX" upgrade --install snowplow \
    oci://ghcr.io/krateo-platformops/charts/snowplow --version "$SNOWPLOW_VERSION" \
    --namespace krateo-system --create-namespace --wait --timeout 15m \
    --set service.type=ClusterIP \
    >/dev/null || die "snowplow install failed"
  # The service RDC must call. Named explicitly rather than guessed: a wrong URL
  # fails at delegation time, deep inside a chain, with a live resource already made.
  SNOWPLOW_SVC="$(kubectl --context "$CTX" get svc -n krateo-system \
    -l app.kubernetes.io/name=snowplow -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)"
  [ -n "$SNOWPLOW_SVC" ] || die "snowplow installed but no service found to point RDC at"
  SNOWPLOW_PORT="$(kubectl --context "$CTX" get svc -n krateo-system "$SNOWPLOW_SVC" \
    -o jsonpath='{.spec.ports[0].port}')"
  SNOWPLOW_URL="http://${SNOWPLOW_SVC}.krateo-system.svc.cluster.local:${SNOWPLOW_PORT}"
  say "  snowplow at ${SNOWPLOW_URL}"

  # Which BITS actually ran. imageID carries the digest the kubelet resolved the tag
  # to, so this is the only statement about the image that survives a tag being
  # moved. Reported always; fatal only when SNOWPLOW_DIGEST names an expectation.
  SP_RUNNING="$(kubectl --context "$CTX" get pods -n krateo-system \
    -l app.kubernetes.io/name=snowplow \
    -o jsonpath='{.items[0].status.containerStatuses[0].imageID}' 2>/dev/null)"
  echo "    running image: ${SP_RUNNING:-<unknown>}"
  if [ -n "$SNOWPLOW_DIGEST" ]; then
    sp_matched=""
    for want in $SNOWPLOW_DIGEST; do
      case "$SP_RUNNING" in *"$want"*) sp_matched="$want"; break ;; esac
    done
    if [ -n "$sp_matched" ]; then
      echo "    digest MATCHES ${sp_matched}"
    else
      die "snowplow is NOT running any accepted digest
    accepted: ${SNOWPLOW_DIGEST}
    running : ${SP_RUNNING:-<unknown>}
  Refusing to report a result attributed to bits that did not run."
    fi
  fi
fi

if [ "$WITH_SNOWPLOW" = "1" ]; then
  say "building the Snowplow endpoint Secret from the ESO-minted token"
  # restactions/compute/endpoint.yaml ships with REPLACE_WITH_ARUBA_JWT, because a
  # live token must never be committed. Built here from the Secret ESO just minted,
  # piped cluster-internally, so the token touches neither disk nor argv nor this
  # log -- the same handling as the client credential above.
  SP_TOKEN="$(kubectl --context "$CTX" get secret arubacloud-token -n default \
    -o jsonpath='{.data.token}' | base64 --decode)"
  [ -n "$SP_TOKEN" ] || die "arubacloud-token is present but empty"
  kubectl --context "$CTX" create secret generic arubacloud-endpoint \
    -n krateo-system \
    --from-literal=server-url="$ARUBA_API" \
    --from-literal=token="$SP_TOKEN" \
    --dry-run=client -o yaml | kubectl --context "$CTX" apply -f - >/dev/null \
    || die "could not create the arubacloud-endpoint Secret"
  unset SP_TOKEN
  say "applying the CloudServer RESTActions"
  # NAMED FILES, never `-f restactions/compute/`. That directory also contains
  # endpoint.yaml -- the Secret, shipping `token: REPLACE_WITH_ARUBA_JWT` because a
  # live token must never be committed -- so a directory apply OVERWRITES the
  # endpoint Secret built from the ESO token eight lines above, with the placeholder.
  #
  # It used to do exactly that, behind `>/dev/null 2>&1 ||` with a named-file apply as
  # the fallback. The first command SUCCEEDS (every file is valid), so the fallback
  # never ran, the clobber was silent, and the swallowed stderr meant there was
  # nothing to notice. A delegated call would then authenticate to snowplow correctly
  # and fail at Aruba with a bogus bearer token -- an auth failure two layers from
  # its cause.
  #
  # Found by an audit prompted by a peer's finding about checks that cannot fail:
  # `|| fallback` with stderr discarded is not a safety net, it is a way to not know.
  for f in restactions/compute/cloudserver-create.restaction.yaml \
           restactions/compute/cloudserver-update.restaction.yaml \
           restactions/compute/cloudserver-delete.restaction.yaml; do
    kubectl --context "$CTX" apply -n krateo-system -f "$f" >/dev/null \
      || die "RESTAction ${f} failed to apply -- expected kind RESTAction (templates.krateo.io/v1) from snowplow-crds"
  done
  # And prove the Secret still holds a real token rather than the placeholder, because
  # the whole point of the above is that this can be clobbered without a word.
  if kubectl --context "$CTX" get secret arubacloud-endpoint -n krateo-system \
       -o jsonpath='{.data.token}' 2>/dev/null | base64 --decode \
       | grep -q 'REPLACE_WITH_ARUBA_JWT'; then
    die "the arubacloud-endpoint Secret holds the committed PLACEHOLDER, not a live token"
  fi
fi

HELM_SETS=()
if [ -n "$PROVIDER_IMAGE" ] || [ -n "$RDC_IMAGE" ]; then
  say "loading locally built images into the kind node"
  for ref in "$PROVIDER_IMAGE" "$RDC_IMAGE"; do
    [ -n "$ref" ] || continue
    kind load docker-image "$ref" --name "$CLUSTER" >/dev/null \
      || die "kind load failed for ${ref} -- is it built?"
    echo "    loaded ${ref}"
  done
  [ -n "$PROVIDER_IMAGE" ] && img_sets image "$PROVIDER_IMAGE"
  [ -n "$RDC_IMAGE" ] && img_sets rdc.image "$RDC_IMAGE"
fi
if [ "$RDC_DEBUG" != "0" ]; then
  # --set-string, not --set: the values schema types rdc.env as string=>string, and
  # plain --set coerces `true` to a boolean, which the chart then rejects --
  #   at '/rdc/env/REST_CONTROLLER_DEBUG': got boolean, want string
  HELM_SETS+=(--set-string rdc.env.REST_CONTROLLER_DEBUG=true)
fi
if [ "$WITH_SNOWPLOW" = "1" ]; then
  # RDC's -snowplow-url defaults to the URL_SNOWPLOW env var, and EMPTY MEANS
  # DISABLED -- mutate_restaction.go then hard-errors on any *ApiRef. No chart sets
  # it (docs/adversarial-review.md row 3), which is the whole reason CloudServer's
  # RESTActions had never run anywhere.
  HELM_SETS+=(--set-string "rdc.env.URL_SNOWPLOW=${SNOWPLOW_URL}")
  # URL_AUTHN as well, and BOTH are required. Setting only URL_SNOWPLOW gets RDC as
  # far as reaching snowplow and no further -- /call then answers
  #     401 {"message":"missing authorization header"}
  # because RDC has nowhere to obtain the token snowplow demands. Our own
  # docs/adversarial-review.md row 3 says the chart sets "neither URL_SNOWPLOW nor
  # URL_AUTHN", and docs/ga-plan.md carries it as an explicit task; I wired one of
  # the two and spent a cluster build rediscovering the other.
  HELM_SETS+=(--set-string "rdc.env.URL_AUTHN=${AUTHN_URL}")
  # ...and the projected ServiceAccount TOKEN, which is the third prerequisite and
  # the only one no amount of env-var configuration reaches. RDC does not
  # authenticate as a human. It reads an AUDIENCE-BOUND projected token from
  #     /var/run/secrets/krateo.io/serviceaccount/token     (RDC DefaultTokenPath)
  # and POSTs it to authn's /serviceaccount/login to be exchanged for the JWT it
  # then presents to snowplow, re-reading the file on each cache miss because
  # projected tokens rotate on disk. The chart mounts NOTHING there -- rdc.volumes
  # and rdc.volumeMounts are both [] in values.yaml -- so with both URLs set and no
  # mount the exchange fails before snowplow is ever reached:
  #     reading projected service account token ".../token": no such file or directory
  #
  # The audience MUST be "authn": authn verifies the token with a Kubernetes
  # TokenReview, which enforces the audience, so the pod's default token is refused
  # even though it is a perfectly valid token. mountPath is the DIRECTORY and the
  # projected source's `path` is the filename, which together make the path above.
  #
  # --set-json, not --set: these are arrays of objects and --set cannot express one.
  # values.schema.json types both as {"type":"array","items":{"type":"object"}}.
  HELM_SETS+=(--set-json 'rdc.volumes=[{"name":"authn-token","projected":{"sources":[{"serviceAccountToken":{"audience":"authn","expirationSeconds":3600,"path":"token"}}]}}]')
  HELM_SETS+=(--set-json 'rdc.volumeMounts=[{"name":"authn-token","mountPath":"/var/run/secrets/krateo.io/serviceaccount","readOnly":true}]')
fi

if [ -n "$CHART_SRC" ]; then
  say "installing oasgen-provider from ${CHART_SRC} -- NOT A RELEASED STACK"
  echo "    overrides: ${HELM_SETS[*]:-none}"
  CRDS_CHART="${CHART_SRC}/oasgen-provider-crds"
  PROV_CHART="${CHART_SRC}/oasgen-provider"
  VERSION_ARGS=()
else
  say "installing oasgen-provider ${PIN} (RELEASED images, no overrides)"
  CRDS_CHART="oci://ghcr.io/krateo-platformops/charts/oasgen-provider-crds"
  PROV_CHART="oci://ghcr.io/krateo-platformops/charts/oasgen-provider"
  VERSION_ARGS=(--version "$PIN")
fi
# upgrade --install, not install: this script reuses an existing cluster when one is
# already there, and a plain install then fails with "cannot re-use a name that is
# still in use". The whole point is that it can be run again.
helm --kube-context "$CTX" upgrade --install oasgen-provider-crds \
  "$CRDS_CHART" ${VERSION_ARGS[@]+"${VERSION_ARGS[@]}"} \
  --namespace krateo-system --create-namespace --wait \
  || die "crds chart failed"
# --timeout 15m, and the number is MEASURED rather than padded. It also COMPENSATES
# FOR A BUG THAT IS BEING FIXED, which is the more important half of this comment:
# oasgen-provider#271. The provider's runtime stage is `FROM golang:1.26-trixie` -- a
# whole Go toolchain -- while rest-dynamic-controller, built in the same repository
# the same way, uses distroless/static:nonroot. Both binaries are CGO_ENABLED=0 and
# neither shells out, so the 331 MB buys two static binaries and a CA bundle.
#
# So 15m is NOT a statement that this chart needs 15 minutes. oasgen-provider have
# recorded that raising --wait is the wrong fix, because it treats a 300 MB accident
# as a requirement and pushes the cost onto every consumer's CI. REVERT THIS TO 5m
# once #271 ships and the image is distroless-sized; if the pull is still minutes
# long at that point, that is a new finding and not this one.
#
# The measurement that made it diagnosable, on a cold node:
#
#   oasgen-provider:0.36.0   331229426 bytes   pulled in 4m29.113s   <-- timed out at 5m
#   snowplow:1.12.57          23209366 bytes   pulled in    18.615s
#   authn:0.27.3              14224445 bytes   pulled in     6.514s
#
# The deployment was Ready about a minute after helm gave up, so the symptom was
# `Error: context deadline exceeded` against a chart that was in fact fine -- which
# reads exactly like a broken release and is not one. A reused cluster has the image
# cached and installs in seconds; it is only the first pull of a new pin that is slow,
# so this bites precisely when the pin moves, which is when it is most confusing.
helm --kube-context "$CTX" upgrade --install oasgen-provider \
  "$PROV_CHART" ${VERSION_ARGS[@]+"${VERSION_ARGS[@]}"} ${HELM_SETS[@]+"${HELM_SETS[@]}"} \
  --namespace krateo-system --wait --timeout 15m \
  || die "provider chart failed"

say "images actually running (this is the whole point of the exercise)"
kubectl --context "$CTX" get deploy -n krateo-system \
  -o jsonpath='{range .items[*]}    {.metadata.name}{"\t"}{.spec.template.spec.containers[0].image}{"\n"}{end}'

say "applying OAS ConfigMaps and RestDefinitions"
# -n krateo-system is not decoration: the manifests declare no namespace, and oasgen
# deploys each generated controller into its RestDefinition's namespace.
kubectl --context "$CTX" apply -n krateo-system -f configmaps/ >/dev/null \
  || die "configmaps failed"
[ "$RESTDEFS_DIR" = "restdefinitions/" ] || say "RestDefinitions from ${RESTDEFS_DIR}"

# ONLY the RestDefinitions this chain needs, by default.
#
# Applying all 36 spawns 36 controller Deployments, and a single kind node cannot
# bring them up together: pods wedge in ContainerCreating with
#     Timeout waiting for systemd to create kubelet-kubepods-besteffort-pod<...>.slice
# and it gets WORSE while you wait -- 29 stuck, not 29 draining. The node is not
# short of CPU or memory when this happens (36% and 27% allocated), so it reads like
# a provider fault and is not one. One chain never needs more than a handful.
#
# RESTDEFS_SCOPE=all restores applying every definition, which is what CI's smoke
# test wants: there, applying all of them IS the test.
RESTDEFS_SCOPE="${RESTDEFS_SCOPE:-chain}"

say "waiting for the controllers ${CHAIN} needs"
# Derived from the chain itself rather than hardcoded, so this works for any chain
# passed in CHAIN. A hardcoded list silently waits for the wrong controllers the
# moment the chain changes, and then times out for a reason that looks like a
# provider fault.
KINDS=$(python3 -c "
import sys, yaml
c = yaml.safe_load(open('$CHAIN'))
print(' '.join(sorted({r['kind'].lower() for r in (c.get('resources') or [])})))
") || die "could not read kinds from $CHAIN"
echo "    kinds: ${KINDS}"

if [ "$RESTDEFS_SCOPE" = "all" ]; then
  # -R: restdefinitions/ is one directory per provider, and without it kubectl
  # refuses the directory outright ("recognized file extensions are ...").
  say "applying EVERY RestDefinition (RESTDEFS_SCOPE=all)"
  kubectl --context "$CTX" apply -n krateo-system -R -f "$RESTDEFS_DIR" >/dev/null \
    || die "restdefinitions failed"
else
  # The kind -> file mapping is read from each file's spec.resource.kind, NOT from
  # its filename, so renaming a generated file cannot silently drop a definition the
  # chain needs. Values arrive through the environment rather than being interpolated
  # into the program text: KINDS is shell-built, and quoting it into a Python literal
  # is how this got written wrong the first time.
  RD_FILES=$(KINDS="$KINDS" RESTDEFS_DIR="$RESTDEFS_DIR" python3 -c '
import glob, os, sys, yaml
wanted = {k.strip().lower() for k in os.environ["KINDS"].split()}
hits, seen = [], set()
for f in sorted(glob.glob(os.path.join(os.environ["RESTDEFS_DIR"], "**", "*.yaml"), recursive=True)):
    for doc in yaml.safe_load_all(open(f)):
        if not doc or doc.get("kind") != "RestDefinition":
            continue
        k = ((doc.get("spec") or {}).get("resource") or {}).get("kind", "")
        if k.lower() in wanted:
            hits.append(f)
            seen.add(k.lower())
missing = wanted - seen
if missing:
    sys.stderr.write("no RestDefinition declares kind(s): " + ", ".join(sorted(missing)) + "\n")
    sys.exit(1)
print("\n".join(hits))
') || die "could not map the chain's kinds onto RestDefinitions in ${RESTDEFS_DIR}"
  say "applying only what the chain needs, not every definition"
  printf '%s\n' "$RD_FILES" | sed 's|^|    |'
  # One -f per file: a chain's definitions live in different provider directories.
  APPLY_ARGS=()
  while IFS= read -r f; do [ -n "$f" ] && APPLY_ARGS+=(-f "$f"); done <<< "$RD_FILES"
  kubectl --context "$CTX" apply -n krateo-system "${APPLY_ARGS[@]}" >/dev/null \
    || die "restdefinitions failed"
fi

for kind in $KINDS; do
  for i in $(seq 1 40); do
    # Anchored on `-<kind>-controller$`: a bare `-vpc` also matches
    # `-vpcpeering-controller`, and `head -1` would then wait on the wrong one.
    d=$(kubectl --context "$CTX" get deploy -n krateo-system -o name 2>/dev/null \
        | grep -i -- "-${kind}-controller$" | head -1)
    if [ -n "$d" ] && kubectl --context "$CTX" rollout status "$d" -n krateo-system \
         --timeout=20s >/dev/null 2>&1; then
      echo "    ${kind}: $(basename "$d") ready"; break
    fi
    sleep 15
    [ "$i" = 40 ] && die "no ready controller for ${kind} after 10m"
  done
done

if [ "$WITH_SNOWPLOW" = "1" ]; then
  say "granting each delegating controller an authn identity"
  # The fourth prerequisite, and the only one that is a Kubernetes OBJECT rather than
  # a setting. authn does not trust a ServiceAccount token merely because it verifies.
  # It looks for a serviceaccount.authn.krateo.io ServiceAccount CR in its OWN
  # namespace whose spec.serviceAccountRef matches the calling SA --
  #     "The CR's existence is the allowlist; an SA with no matching ServiceAccount
  #      CR cannot exchange"
  # -- which is the CRD's own schema description, not an inference. With no CR the
  # exchange is 403 at authn and RDC never reaches snowplow at all.
  #
  # Two things here cost a cluster build each to learn, so they are written down:
  #
  #   * the issued USERNAME is the CR's metadata.name, NOT the ServiceAccount's name.
  #     That username is also what names the <username>-clientconfig Secret authn
  #     writes on a successful login, and snowplow reads that Secret to act on the
  #     apiserver AS this identity (CN=username, O=groups). Nobody creates it by
  #     hand; if it is absent after a delegated call, the LOGIN failed -- look in
  #     authn's log, not snowplow's.
  #
  #   * "authn never authors RBAC" (same schema). The groups become the client
  #     certificate's O=, so ordinary RBAC bound to the group is what lets snowplow
  #     read the RESTActions and their endpointRef Secret. Missing RBAC surfaces as
  #     an authorization error from snowplow, NOT as a 401 -- a different symptom
  #     from every other step here, which is the point of separating them.
  #
  # One identity per DELEGATING resource, derived from the RestDefinitions rather
  # than hardcoded, so a resource that gains an *ApiRef later is covered without
  # anyone remembering this block exists.
  DELEGATING=$(python3 -c "
import glob, os, yaml
kinds = set()
for f in glob.glob(os.path.join('${RESTDEFS_DIR}', '**', '*.yaml'), recursive=True):
    txt = open(f).read()
    if 'ApiRef' not in txt:
        continue
    for doc in yaml.safe_load_all(txt):
        if not doc or doc.get('kind') != 'RestDefinition':
            continue
        res = ((doc.get('spec') or {}).get('resource')) or {}
        if any(k.endswith('ApiRef') for k in res):
            kinds.add(res['kind'])
print(' '.join(sorted(kinds)))
") || die "could not read the delegating kinds from ${RESTDEFS_DIR}"
  echo "    delegating kinds: ${DELEGATING:-none}"

  # The group's RBAC, once. Narrow on purpose: snowplow resolves RESTActions and
  # reads the one endpoint Secret they reference. A wildcard on secrets would hide
  # the moment a new RESTAction starts reading a credential nobody authorised.
  if [ -n "$DELEGATING" ]; then
    kubectl --context "$CTX" apply -f - >/dev/null <<'RBAC' \
      || die "could not grant the krateo-rdc group its RBAC"
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: krateo-rdc-restactions
  namespace: krateo-system
rules:
  # Snowplow resolves the requested RESTAction as this identity, and writes its
  # progress back onto the object's status as it walks the stages.
  - apiGroups: ["templates.krateo.io"]
    resources: ["restactions"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["templates.krateo.io"]
    resources: ["restactions/status"]
    verbs: ["get", "update", "patch"]
  # Every api[] entry carries an endpointRef, and that Secret holds the Aruba
  # server-url and Bearer token. Named explicitly rather than granted wholesale.
  - apiGroups: [""]
    resources: ["secrets"]
    resourceNames: ["arubacloud-endpoint"]
    verbs: ["get"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: krateo-rdc-restactions
  namespace: krateo-system
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: krateo-rdc-restactions
subjects:
  - apiGroup: rbac.authorization.k8s.io
    kind: Group
    name: krateo-rdc
RBAC
  fi

  for kind in $DELEGATING; do
    lower=$(printf '%s' "$kind" | tr '[:upper:]' '[:lower:]')
    dep=$(kubectl --context "$CTX" get deploy -n krateo-system -o name 2>/dev/null \
          | grep -i -- "-${lower}-controller$" | head -1)
    [ -n "$dep" ] || die "no generated controller for ${kind} -- cannot grant it an identity"
    # READ the ServiceAccount name; do not compute it. oasgen names it
    # <plural>-<group>-v1alpha1 even where the CRD's served version is v1-0-0
    # (CloudServer's is), so the obvious formula produces a name that does not exist
    # and the mapping silently matches nothing -- a 403 with no clue why.
    sa=$(kubectl --context "$CTX" get "$dep" -n krateo-system \
         -o jsonpath='{.spec.template.spec.serviceAccountName}')
    [ -n "$sa" ] || die "${dep} declares no serviceAccountName"
    user="rdc-${lower}"
    echo "    ${kind}: sa/${sa} -> authn user '${user}' (group krateo-rdc)"
    kubectl --context "$CTX" apply -f - >/dev/null <<EOF \
      || die "could not create the authn identity for ${kind}"
apiVersion: serviceaccount.authn.krateo.io/v1alpha1
kind: ServiceAccount
metadata:
  name: ${user}
  namespace: krateo-system
spec:
  displayName: rest-dynamic-controller (${kind})
  serviceAccountRef:
    namespace: krateo-system
    name: ${sa}
  groups:
    - krateo-rdc
EOF
  done
fi

say "images actually running, including the RDCs"
# Printed again here on purpose. The earlier print runs before any RestDefinition
# exists, so there are no generated controllers yet and it can only show the provider
# -- while the RDC image is the more interesting half of the question.
kubectl --context "$CTX" get deploy -n krateo-system \
  -o jsonpath='{range .items[*]}{.spec.template.spec.containers[0].image}{"\n"}{end}' \
  | sort | uniq -c | sed 's/^/    /'

say "applying the generated Configurations (and ONLY those)"
# Deliberately not `apply -f samples/`. That directory also holds a CR sample per
# resource -- applying them would create live, billable resources on the account --
# and samples/arubacloud-token-secret.yaml, which would clobber the Secret ESO owns.
# Only the 34 *-configuration.yaml objects are wanted here.
# A missing CRD is NOT fatal here, and insisting it was cost a whole run: oasgen 0.32.0
# refused to generate `Job`, whose plural collides with batch/jobs (oasgen#200), so
# job-configuration.yaml could never apply on a FRESH cluster and `|| die` aborted
# before the chain -- for a resource the chain does not use. That particular collision
# is gone (the kind is `ScheduledJob` now), but the hazard is not: any resource oasgen
# declines to generate takes the whole run down with it. Each failure is named instead,
# and the run only dies if a configuration THIS CHAIN references is among them.
# Under RESTDEFS_SCOPE=chain only the chain's CRDs exist, so applying all 36
# configurations means 31 of them fail ONE AT A TIME through API discovery -- slow,
# noisy, and correctly non-fatal but for a reason that no longer applies. The set the
# chain actually needs is derived from the same `configurationRef`s the check below
# verifies, so it drives the apply too instead of filtering failures afterwards.
if [ "$RESTDEFS_SCOPE" = "all" ]; then
  CFG_FILES=$(find samples -name '*-configuration.yaml' | sort)
else
  CFG_FILES=$(CHAIN="$CHAIN" python3 -c '
import glob, os, sys, yaml
chain = yaml.safe_load(open(os.environ["CHAIN"]))
kinds = {r["kind"].lower() for r in (chain.get("resources") or [])}
hits, seen = [], set()
for f in sorted(glob.glob("samples/**/*-configuration.yaml", recursive=True)):
    k = os.path.basename(f)[: -len("-configuration.yaml")].lower()
    if k in kinds:
        hits.append(f); seen.add(k)
missing = kinds - seen
if missing:
    sys.stderr.write("no configuration sample for kind(s): " + ", ".join(sorted(missing)) + "\n")
    sys.exit(1)
print("\n".join(hits))
') || die "could not map the chain's kinds onto configuration samples"
fi
applied=0 missing=""
while IFS= read -r f; do
  [ -n "$f" ] || continue
  if kubectl --context "$CTX" apply -n default -f "$f" >/dev/null 2>&1; then
    applied=$((applied + 1))
  else
    missing="${missing} $(basename "$f")"
  fi
done <<< "$CFG_FILES"
echo "    applied ${applied} of $(printf '%s\n' "$CFG_FILES" | grep -c .)"
[ -n "$missing" ] && echo "    NOT applied (no CRD -- fatal only if the chain needs it):${missing}"
python3 - "$CHAIN" <<'EOF' || die "a configuration this chain references did not apply"
import sys, yaml
chain = yaml.safe_load(open(sys.argv[1]))
need = set()
for r in chain.get("resources") or []:
    ref = (r.get("spec") or {}).get("configurationRef") or {}
    if ref.get("name"):
        need.add((r["kind"], ref["name"]))
import subprocess, os
ctx = os.environ["CTX"]
bad = []
for kind, name in sorted(need):
    rc = subprocess.run(["kubectl", "--context", ctx, "get",
                         f"{kind.lower()}configurations.arubacloud.ogen.krateo.io",
                         name, "-n", "default"],
                        capture_output=True).returncode
    if rc != 0:
        bad.append(f"{kind}Configuration/{name}")
if bad:
    print("    MISSING, and the chain needs them: " + ", ".join(bad))
    sys.exit(1)
print(f"    all {len(need)} configurations this chain references are present")
EOF

say "running ${CHAIN} on the released stack"
# CHAIN_ARGS carries flags through to the runner, e.g. --with-billable. Unquoted on
# purpose so it word-splits into separate arguments.
# shellcheck disable=SC2086
KUBE_CONTEXT="$CTX" python3 -u scripts/ga_chain.py "$CHAIN" ${CHAIN_ARGS:-}
rc=$?
say "chain exited ${rc}"
exit $rc
