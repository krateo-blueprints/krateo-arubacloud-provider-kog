# Aruba Cloud Compute provider

- **OpenAPI**: `Aruba.CmpService.Computing.Api` v1.0.0 (`openapi/_source/compute.json` → patched `openapi/compute.json`)
- **Security scheme (patched)**: `accessToken` (HTTP Bearer)
- **ConfigMap**: `arubacloud-compute-openapi` in `krateo-system`
- **Resources**: 2


| Kind | Verbs | Identifier(s) | Delegation |
|------|-------|---------------|------------|
| `CloudServer` | findby, get | `metadata.name` | create, update, delete → RESTAction |
| `KeyPair` | findby, get, create, delete | `metadata.name` | — |


## CloudServer

> CloudServer has NO single create/update endpoint; its lifecycle (power on/off, associate elastic IPs / security groups / subnets, attach data volumes, restore) is a multi-call sequence. create/update/delete are delegated to Snowplow RESTActions via *ApiRef (NO proxy); observe stays native via get/findby. See docs/lifecycle-beyond-crud.md.

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Compute/cloudServers` |
| get | GET | `/projects/{projectId}/providers/Aruba.Compute/cloudServers/{cloudServerId}` |
| create | *(delegated)* | RESTAction `arubacloud-compute-cloudserver-create` |
| update | *(delegated)* | RESTAction `arubacloud-compute-cloudserver-update` |
| delete | *(delegated)* | RESTAction `arubacloud-compute-cloudserver-delete` |

status fields: `metadata.id` · excluded from spec: `cloudServerId`

Configuration query params: `api-version, filter, limit, offset, projection, sort`

Sample: [`samples/compute/cloudserver.yaml`](../../samples/compute/cloudserver.yaml) · [`cloudserver-configuration.yaml`](../../samples/compute/cloudserver-configuration.yaml)

## KeyPair

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Compute/keyPairs` |
| get | GET | `/projects/{projectId}/providers/Aruba.Compute/keyPairs/{keyPairId}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Compute/keyPairs` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Compute/keyPairs/{keyPairId}` |

status fields: `metadata.id` · excluded from spec: `keyPairId`

Configuration query params: `api-version, filter, limit, offset, projection, sort`

Sample: [`samples/compute/keypair.yaml`](../../samples/compute/keypair.yaml) · [`keypair-configuration.yaml`](../../samples/compute/keypair-configuration.yaml)

## Endpoints not exposed as a resource

Action-only, list-only or delegated endpoints (see [lifecycle-beyond-crud](../lifecycle-beyond-crud.md) and [coverage](../coverage.md)):

| Method | Path | Summary |
|--------|------|---------|
| POST | `/projects/{projectId}/providers/Aruba.Compute/cloudServers/{cloudServerId}/associateDisassociateElasticIPs` | Manage ElasticIPs |
| POST | `/projects/{projectId}/providers/Aruba.Compute/cloudServers/{cloudServerId}/associateDisassociateSecurityGroups` | Manage SecurityGroups |
| POST | `/projects/{projectId}/providers/Aruba.Compute/cloudServers/{cloudServerId}/associateDisassociateSubnets` | Manage Subnets |
| POST | `/projects/{projectId}/providers/Aruba.Compute/cloudServers/{cloudServerId}/attachDetachDataVolumes` | Manage DataVolumes |
| POST | `/projects/{projectId}/providers/Aruba.Compute/cloudServers/{cloudServerId}/password` | Set CloudServer password |
| POST | `/projects/{projectId}/providers/Aruba.Compute/cloudServers/{cloudServerId}/poweroff` | Power off CloudServer |
| POST | `/projects/{projectId}/providers/Aruba.Compute/cloudServers/{cloudServerId}/poweron` | Power on CloudServer |
| POST | `/projects/{projectId}/providers/Aruba.Compute/cloudServers/{cloudServerId}/restore` | Restore snapshot from a created volume |
