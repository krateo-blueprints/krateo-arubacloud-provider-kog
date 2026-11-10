# Probes that run against an oasgen-provider clone

These are Go tests, not Python, and they cannot run in this repository's CI: they
import `oasgen-provider` internals and its `plumbing` dependency, so they only build
inside a checkout of `krateo-platformops/oasgen-provider` at the tag under test.

They live here anyway. `docs/oasgen-provider-evolution.md` used to say the probes lived
outside the repo, and on 2026-11-09 that cost a full rebuild from a prose description --
including re-making two mistakes the original author had already solved. A probe whose
result a pin decision rests on is evidence, and evidence that cannot be re-run is an
anecdote.

## fleet_parentdefault_probe_test.go

Renders every committed RestDefinition against this repo's vendored OAS documents and
counts stamped `default: {}` paths, which is the hazard that kept the pin off 0.53.0.

```sh
CLONE=/path/to/krateo-oasgen-provider
TAG=0.56.0

# A worktree, NOT a checkout: the clone is shared with other sessions and moving its
# HEAD under them is its own kind of outage.
git -C "$CLONE" worktree add --detach /tmp/ws-$TAG "${TAG}^{}"
cp tests/probes/fleet_parentdefault_probe_test.go \
   /tmp/ws-$TAG/go/oasgen-provider/internal/tools/render/

cd /tmp/ws-$TAG/go/oasgen-provider
ARUBA_REPO=/path/to/krateo-arubacloud-provider-kog \
  go test -tags unit -count=1 -run TestFleetParentDefaultStamping ./internal/tools/render/ -v

git -C "$CLONE" worktree remove --force /tmp/ws-$TAG
```

`^{}` is required: these tags are annotated, and the tag object is not a commit.

### Two traps it already fell into

Both produced a *clean fleet* result that was actually a dead instrument, which is why
the control is a hard failure rather than a log line.

1. **`crdgen.GenerateWithWarnings` returns YAML**, not JSON. `json.Unmarshal` fails on
   the leading separator, so the control reported zero stamps and the fleet zero looked
   corroborated.
2. **The control must use an eligible shape.** crdgen stamps only a parent that is
   optional, `type: object`, undefaulted itself, and has a reachable *defaulted
   descendant*. Anchored on whichever definition sorts first, the control reports zero
   for a reason unrelated to the flag. It is anchored on `Vpc`, whose
   `VpcPropertiesDto.default` carries `default: true`.

A zero from this probe means nothing unless the control line reads > 0.
