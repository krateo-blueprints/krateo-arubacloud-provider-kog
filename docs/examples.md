---
type: ExampleIndex
title: krateo-arubacloud-provider-kog — examples
description: Index of the runnable examples under examples/ — self-contained walkthroughs that provision real Aruba Cloud resources through this blueprint.
resource: oci://ghcr.io/krateo-blueprints/charts/aruba-network-environment
tags: [examples, composition, cloudserver]
timestamp: 2026-08-11T00:00:00Z
---

# Examples

Runnable, self-contained walkthroughs live under
[`examples/`](../examples/). Each has its own README with preconditions and
`kubectl apply` steps.

| Example | What it does |
|---------|--------------|
| [network-environment](../examples/network-environment/README.md) | Provisions an Aruba network environment (`Vpc` + `Subnet` + `SecurityGroup` + their Configurations) from one input set, via the `aruba-network-environment` Composition. `CloudServer` is deliberately excluded — its delegated update/delete need four prerequisites a Composition cannot assume |

## Per-resource samples

Beyond the end-to-end examples, `samples/<provider>/` holds a
`<Kind>Configuration` plus a `<Kind>` CR skeleton for **every** resource, and the
shared `samples/arubacloud-token-secret.yaml`. Apply the Configuration then the CR
to manage a single resource — see [Usage](usage.md#manage-your-first-resource) and
the [Provider reference](providers/README.md) for the per-resource details.
