---
type: Usage
title: Managing risk with management policies
description: How to point a CR at an existing resource, or restrict a verb, without risking mutation.
tags: [aruba, kog, safety]
timestamp: 2026-09-01T00:00:00Z
---

# Restricting what a controller may do

`unstructured-runtime` — the loop every generated controller runs on — gates
create, update and delete on a **per-CR annotation**. This is the supported way to
adopt an existing resource, run a read-only observation, or protect something
expensive, and it needs no change to the RestDefinition.

## `krateo.io/management-policy`

| Value | Provider may |
|-------|--------------|
| `default` (implicit) | observe, create, update, delete |
| `observe-create-update` | observe, create, update — **never delete** |
| `observe-delete` | observe, delete — **never create or update** |
| `observe` | observe only |

Enforced by `meta.IsActionAllowed`, which reads the annotation and defaults to
`default` when unset.

## `krateo.io/deletion-policy`

| Value | On CR deletion |
|-------|----------------|
| `delete` (implicit) | the external resource is deleted |
| `orphan` | the external resource is left in place |

## Use it for observation runs

Pointing a CR at a resource you did not create is exactly where an accidental `PUT`
is unaffordable. Both annotations together make it structurally impossible:

```yaml
metadata:
  name: observe-existing-vpc
  annotations:
    krateo.io/management-policy: observe   # cannot create or update
    krateo.io/deletion-policy: orphan      # deleting the CR leaves the VPC alone
```

## Correction: this supersedes the `compareScope` guard

An earlier pass in this repository guarded observation runs by setting
`compareScope: identifiersAndStatus` on the **RestDefinition**. That was the wrong
instrument, for two reasons worth stating plainly:

- **It is cluster-wide.** A RestDefinition is shared by every CR of that kind, so
  narrowing drift to protect one observation CR also silently narrows it for every
  real managed resource of the same kind.
- **It restricts comparison, not action.** It reduces the chance drift is *detected*;
  it does not prevent the controller from acting when drift *is* detected. It is a
  narrower diff, not a safety interlock.

`management-policy` is per-CR and blocks the action itself. Prefer it. `compareScope`
remains the right tool for its actual purpose — deciding which fields constitute
drift for a resource you genuinely do manage.

## Relationship to read-only resources

`management-policy: observe` expresses read-only *intent*, and is the correct way to
say "this cluster does not manage this resource". It does **not** repair
[oasgen-provider#75](https://github.com/krateo-platformops/oasgen-provider/issues/75):
a resource whose API has no create verb still materialises no identifier field, so
there is no way to say *which* instance is meant. The annotation controls what may be
done to a resource; the gap is being unable to name one.
