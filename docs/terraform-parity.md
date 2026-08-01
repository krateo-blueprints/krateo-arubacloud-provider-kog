# Feature parity with the official Aruba Cloud Terraform provider

Comparison against [`Arubacloud/terraform-provider-arubacloud`](https://github.com/Arubacloud/terraform-provider-arubacloud)
(the official Terraform/OpenTofu provider).

**Verdict:** full resource/API parity — every managed resource the Terraform
provider exposes is covered here — **plus 9 additional resources**. The Terraform
provider, being hand-written and released Go, still leads on a few runtime-maturity
features; those gaps map one-to-one to items already tracked in
[oasgen-provider-evolution](oasgen-provider-evolution.md).

| | Terraform provider | This repo (KOG) |
|---|---|---|
| Managed resources | 25 | **34** |
| Delivery | Released Go binary | Declarative RestDefinitions generated from the OpenAPI specs |
| Runtime | Terraform/OpenTofu CLI | Kubernetes controllers (oasgen-provider + RDC) |

## Resource cross-map (their 25 → ours)

| Terraform resource | This repo | Provider dir |
|--------------------|-----------|--------------|
| `arubacloud_vpc` | `Vpc` | network |
| `arubacloud_subnet` | `Subnet` | network |
| `arubacloud_security_group` | `SecurityGroup` | network |
| `arubacloud_security_rule` | `SecurityRule` | network |
| `arubacloud_elastic_ip` | `ElasticIp` | network |
| `arubacloud_vpc_peering` | `VpcPeering` | network |
| `arubacloud_vpc_peering_route` | `VpcPeeringRoute` | network |
| `arubacloud_vpn_tunnel` | `VpnTunnel` | network |
| `arubacloud_vpn_route` | `VpnRoute` | network |
| `arubacloud_cloud_server` | `CloudServer` | compute |
| `arubacloud_keypair` | `KeyPair` | compute |
| `arubacloud_kaas` | `Kaas` | container |
| `arubacloud_container_registry` | `Registry` | container |
| `arubacloud_dbaas` | `Dbaas` | database |
| `arubacloud_database` | `Database` | database |
| `arubacloud_dbaas_user` | `DatabaseUser` | database |
| `arubacloud_database_grant` | `Grant` | database |
| `arubacloud_database_backup` | `DatabaseBackup` | database |
| `arubacloud_block_storage` | `BlockStorage` | storage |
| `arubacloud_snapshot` | `Snapshot` | storage |
| `arubacloud_backup` | `Backup` | storage |
| `arubacloud_restore` | `Restore` | storage |
| `arubacloud_kms` | `Kms` | security |
| `arubacloud_schedule_job` | `Job` | schedule |
| `arubacloud_project` | `Project` | project |

**25 / 25 covered.**

## Resources this repo adds (not in the Terraform provider)

| This repo | Provider | Notes |
|-----------|----------|-------|
| `KaasBackup` | container | Kubernetes cluster backups |
| `Key` | security | KMS keys (sub-resource of `Kms`) |
| `Kmip` | security | KMIP objects (sub-resource of `Kms`) |
| `BackupPolicy` | schedule | Backup policies |
| `BackupPolicyAssignment` | schedule | Policy assignments |
| `Hpc` | baremetal | Bare-metal HPC servers |
| `Folder` | project | Project folders |
| `AlertRule` | metering | Insight alert rules |
| `LoadBalancer` | network | Read-only (findby/get) |

**+9 resources**, including three providers the Terraform provider does not touch
at all (baremetal, metering, and the schedule *policy* surface).

## Where the Terraform provider is more mature

These are depth/robustness differences, not coverage gaps — verified against the
upstream Go code (call-site counts from `internal/provider/`). Each corresponds to
a tracked oasgen-provider evolution where relevant.

| Capability | Terraform provider | This repo | Tracked as |
|------------|--------------------|-----------|------------|
| Async provisioning/deletion wait | `WaitForResourceActive` / `WaitForResourceDeleted` / `WaitUntilReady` at **~125 call sites**; `Read()` resumes waiting from `InCreation` | Relies on Observe re-running until settled; no terminal-failure detection | [§C2](oasgen-provider-evolution.md) |
| Resource import | `ImportState` on every resource (**~51 sites**) — `terraform import` supported | Partial equivalent: `findby` adopts an existing resource by identifier; no explicit import UX | — |
| Sensitive fields (passwords, private keys) | **8** `Sensitive: true` (CloudServer password, DBaaSUser password, KaaS, KeyPair private key, provider `client_secret`) | Plaintext spec field | [§B4](oasgen-provider-evolution.md) |
| Configurable timeouts + retry | Per-resource `timeout` field, retry-on-"not yet visible" | None (fixed reconcile cadence) | — |
| Maturity | Released, versioned, unit + schema tests | Reviewed reference, **not cluster-tested** | — |
| Field validation | `stringvalidator.OneOf` enum checks (~16) | Roughly matched by OAS `enum`; other OAS constraints dropped | [§A6](oasgen-provider-evolution.md) |
| Read/lookup ergonomics | Explicit **data source** per resource | `findby` / `get` verbs (observe) | — |

### CloudServer day-2: a tradeoff, not a Terraform lead

The Terraform provider makes CloudServer **immutable**: every API-backed attribute
is `RequiresReplace` (14 of them) and `Update()` only touches the local `timeout`
field — any change **destroys and recreates** the server. It never calls the
`poweron`/`poweroff`/`associate*`/`attach*` action endpoints (there are zero such
SDK calls in the provider). Initial networking is set through the rich create body.

This repo instead attempts **in-place day-2 reconciliation** of power state and
associations via Snowplow RESTActions ([lifecycle-beyond-crud](lifecycle-beyond-crud.md)) —
more ambitious, but **not cluster-tested**. So on day-2 CloudServer changes the two
projects make opposite tradeoffs (safe-but-destructive recreate vs. in-place-but-unproven);
neither is a clear winner, and it is **not** an area where Terraform does more.

## Where this approach is ahead

- **Broader surface** — +9 resources and 3 providers the Terraform provider omits.
- **No provider binary to maintain** — purely declarative; new API endpoints flow
  in by re-running the generator against updated OpenAPI specs
  (`scripts/generate_restdefinitions.py`), no Go release cycle.
- **Kubernetes-native / GitOps** — resources are CRs reconciled continuously
  (drift correction), not applied by an external CLI.
- **Composable** — a Krateo [Composition](lifecycle-beyond-crud.md#2-cross-resource-a-krateo-composition)
  provisions a whole environment (VPC + Subnet + SecurityGroup + CloudServer) from
  one high-level input.

## Bottom line

Full resource/API parity with the official Terraform provider, exceeding it on
breadth. The remaining differences are runtime-maturity features — async-wait,
`terraform import`, sensitive-field handling, configurable timeouts, and being a
released/tested artifact — several of which map to oasgen-provider evolutions
catalogued in [oasgen-provider-evolution.md](oasgen-provider-evolution.md).
Closing those would make the generated, declarative provider a drop-in equivalent
with wider coverage. (Day-2 CloudServer mutation is a design *tradeoff* between the
two, not a Terraform lead — see above.)

> Method note: the Terraform resource list was read from
> `internal/provider/provider.go` (`Resources()`) in the upstream repository; the
> cross-map uses each resource's Go type. Terraform HCL type names
> (`arubacloud_*`) follow the provider's standard snake_case convention.
