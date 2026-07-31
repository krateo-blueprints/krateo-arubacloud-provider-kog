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

These are depth/robustness differences, not coverage gaps. Each corresponds to a
tracked oasgen-provider evolution.

| Capability | Terraform provider | This repo | Tracked as |
|------------|--------------------|-----------|------------|
| Async provisioning wait | Native `WaitForResourceActive` / `WaitForResourceDeleted`, resumes on `InCreation` | Relies on Observe re-running until settled | [§C2](oasgen-provider-evolution.md) |
| CloudServer day-2 actions (power, associate, attach) | Native, tested Go | Delegated to Snowplow RESTActions (declarative, [not cluster-tested](lifecycle-beyond-crud.md#caveats)) | [§C1](oasgen-provider-evolution.md) |
| Typed schema validation | Full schema validators | OAS constraints stripped during generation | [§A6](oasgen-provider-evolution.md) |
| Sensitive fields (e.g. passwords) | Marked `sensitive` | Plaintext spec field | [§B4](oasgen-provider-evolution.md) |
| Read/lookup | Explicit **data sources** per resource | `findby` / `get` verbs (observe) | — |
| Maturity | Released, versioned, tested | Reviewed reference, **not cluster-tested** | — |

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
breadth. The remaining differences are runtime-maturity features (native
async-wait, tested day-2 actions, typed validation, sensitive-field handling) —
exactly the oasgen-provider evolutions catalogued in
[oasgen-provider-evolution.md](oasgen-provider-evolution.md). Closing those would
make the generated, declarative provider a drop-in equivalent with wider coverage.

> Method note: the Terraform resource list was read from
> `internal/provider/provider.go` (`Resources()`) in the upstream repository; the
> cross-map uses each resource's Go type. Terraform HCL type names
> (`arubacloud_*`) follow the provider's standard snake_case convention.
