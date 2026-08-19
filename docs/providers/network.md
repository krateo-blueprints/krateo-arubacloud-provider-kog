# Aruba Cloud Network provider

- **OpenAPI**: `Aruba.Network.Api` v1.0.0 (`openapi/network-provider.json`, vendored unmodified — see [OAS policy](../oas-patches.md))
- **Security scheme (patched)**: `Bearer` (HTTP Bearer)
- **ConfigMap**: `arubacloud-network-openapi` in `krateo-system`
- **Resources**: 10


| Kind | Verbs | Identifier(s) | Delegation |
|------|-------|---------------|------------|
| `ElasticIp` | findby, get, create, update, delete | `metadata.name` | — |
| `LoadBalancer` | findby, get | `name` | — |
| `SecurityGroup` | findby, get, create, update, delete | `metadata.name` | — |
| `SecurityRule` | findby, get, create, update, delete | `metadata.name` | — |
| `Subnet` | findby, get, create, update, delete | `metadata.name` | — |
| `Vpc` | findby, get, create, update, delete | `metadata.name` | — |
| `VpcPeering` | findby, get, create, update, delete | `metadata.name` | — |
| `VpcPeeringRoute` | findby, get, create, update, delete | `metadata.name` | — |
| `VpnRoute` | findby, get, create, update, delete | `metadata.name` | — |
| `VpnTunnel` | findby, get, create, update, delete | `metadata.name` | — |


## ElasticIp

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Network/elasticIps` |
| get | GET | `/projects/{projectId}/providers/Aruba.Network/elasticIps/{id}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Network/elasticIps` |
| update | PUT | `/projects/{projectId}/providers/Aruba.Network/elasticIps/{id}` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Network/elasticIps/{id}` |

status fields: `metadata.id` · excluded from spec: `id`

Configuration query params: `api-version, filter, ignoreDeletedStatus, limit, offset, projection, sort`

Sample: [`samples/network/elasticip.yaml`](../../samples/network/elasticip.yaml) · [`elasticip-configuration.yaml`](../../samples/network/elasticip-configuration.yaml)

## LoadBalancer

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Network/loadBalancers` |
| get | GET | `/projects/{projectId}/providers/Aruba.Network/loadBalancers/{id}` |

excluded from spec: `id`

Configuration query params: `api-version, filter, ignoreDeletedStatus, limit, offset, projection, sort`

Sample: [`samples/network/loadbalancer.yaml`](../../samples/network/loadbalancer.yaml) · [`loadbalancer-configuration.yaml`](../../samples/network/loadbalancer-configuration.yaml)

## SecurityGroup

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Network/vpcs/{vpcId}/securityGroups` |
| get | GET | `/projects/{projectId}/providers/Aruba.Network/vpcs/{vpcId}/securityGroups/{id}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Network/vpcs/{vpcId}/securityGroups` |
| update | PUT | `/projects/{projectId}/providers/Aruba.Network/vpcs/{vpcId}/securityGroups/{id}` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Network/vpcs/{vpcId}/securityGroups/{id}` |

status fields: `metadata.id` · excluded from spec: `id`

Configuration query params: `api-version, filter, ignoreDeletedStatus, limit, offset, projection, sort`

Sample: [`samples/network/securitygroup.yaml`](../../samples/network/securitygroup.yaml) · [`securitygroup-configuration.yaml`](../../samples/network/securitygroup-configuration.yaml)

## SecurityRule

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Network/vpcs/{vpcId}/securityGroups/{securityGroupId}/securityRules` |
| get | GET | `/projects/{projectId}/providers/Aruba.Network/vpcs/{vpcId}/securityGroups/{securityGroupId}/securityRules/{id}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Network/vpcs/{vpcId}/securityGroups/{securityGroupId}/securityRules` |
| update | PUT | `/projects/{projectId}/providers/Aruba.Network/vpcs/{vpcId}/securityGroups/{securityGroupId}/securityRules/{id}` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Network/vpcs/{vpcId}/securityGroups/{securityGroupId}/securityRules/{id}` |

status fields: `metadata.id` · excluded from spec: `id`

Configuration query params: `api-version, filter, ignoreDeletedStatus, limit, offset, projection, sort`

Sample: [`samples/network/securityrule.yaml`](../../samples/network/securityrule.yaml) · [`securityrule-configuration.yaml`](../../samples/network/securityrule-configuration.yaml)

## Subnet

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Network/vpcs/{vpcId}/subnets` |
| get | GET | `/projects/{projectId}/providers/Aruba.Network/vpcs/{vpcId}/subnets/{id}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Network/vpcs/{vpcId}/subnets` |
| update | PUT | `/projects/{projectId}/providers/Aruba.Network/vpcs/{vpcId}/subnets/{id}` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Network/vpcs/{vpcId}/subnets/{id}` |

status fields: `metadata.id` · excluded from spec: `id`

Configuration query params: `api-version, filter, ignoreDeletedStatus, limit, offset, projection, sort`

Sample: [`samples/network/subnet.yaml`](../../samples/network/subnet.yaml) · [`subnet-configuration.yaml`](../../samples/network/subnet-configuration.yaml)

## Vpc

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Network/vpcs` |
| get | GET | `/projects/{projectId}/providers/Aruba.Network/vpcs/{id}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Network/vpcs` |
| update | PUT | `/projects/{projectId}/providers/Aruba.Network/vpcs/{id}` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Network/vpcs/{id}` |

status fields: `metadata.id` · excluded from spec: `id`

Configuration query params: `api-version, filter, ignoreDeletedStatus, limit, offset, projection, sort`

Sample: [`samples/network/vpc.yaml`](../../samples/network/vpc.yaml) · [`vpc-configuration.yaml`](../../samples/network/vpc-configuration.yaml)

## VpcPeering

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Network/vpcs/{vpcId}/vpcPeerings` |
| get | GET | `/projects/{projectId}/providers/Aruba.Network/vpcs/{vpcId}/vpcPeerings/{id}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Network/vpcs/{vpcId}/vpcPeerings` |
| update | PUT | `/projects/{projectId}/providers/Aruba.Network/vpcs/{vpcId}/vpcPeerings/{id}` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Network/vpcs/{vpcId}/vpcPeerings/{id}` |

status fields: `metadata.id` · excluded from spec: `id`

Configuration query params: `api-version, filter, ignoreDeletedStatus, limit, offset, projection, sort`

Sample: [`samples/network/vpcpeering.yaml`](../../samples/network/vpcpeering.yaml) · [`vpcpeering-configuration.yaml`](../../samples/network/vpcpeering-configuration.yaml)

## VpcPeeringRoute

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Network/vpcs/{vpcId}/vpcPeerings/{vpcPeeringId}/vpcPeeringRoutes` |
| get | GET | `/projects/{projectId}/providers/Aruba.Network/vpcs/{vpcId}/vpcPeerings/{vpcPeeringId}/vpcPeeringRoutes/{id}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Network/vpcs/{vpcId}/vpcPeerings/{vpcPeeringId}/vpcPeeringRoutes` |
| update | PUT | `/projects/{projectId}/providers/Aruba.Network/vpcs/{vpcId}/vpcPeerings/{vpcPeeringId}/vpcPeeringRoutes/{id}` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Network/vpcs/{vpcId}/vpcPeerings/{vpcPeeringId}/vpcPeeringRoutes/{id}` |

status fields: `metadata.id` · excluded from spec: `id`

Configuration query params: `api-version, filter, ignoreDeletedStatus, limit, offset, projection, sort`

Sample: [`samples/network/vpcpeeringroute.yaml`](../../samples/network/vpcpeeringroute.yaml) · [`vpcpeeringroute-configuration.yaml`](../../samples/network/vpcpeeringroute-configuration.yaml)

## VpnRoute

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Network/vpnTunnels/{vpnTunnelId}/vpnRoutes` |
| get | GET | `/projects/{projectId}/providers/Aruba.Network/vpnTunnels/{vpnTunnelId}/vpnRoutes/{id}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Network/vpnTunnels/{vpnTunnelId}/vpnRoutes` |
| update | PUT | `/projects/{projectId}/providers/Aruba.Network/vpnTunnels/{vpnTunnelId}/vpnRoutes/{id}` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Network/vpnTunnels/{vpnTunnelId}/vpnRoutes/{id}` |

status fields: `metadata.id` · excluded from spec: `id`

Configuration query params: `api-version, filter, ignoreDeletedStatus, limit, offset, projection, sort`

Sample: [`samples/network/vpnroute.yaml`](../../samples/network/vpnroute.yaml) · [`vpnroute-configuration.yaml`](../../samples/network/vpnroute-configuration.yaml)

## VpnTunnel

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Network/vpnTunnels` |
| get | GET | `/projects/{projectId}/providers/Aruba.Network/vpnTunnels/{id}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Network/vpnTunnels` |
| update | PUT | `/projects/{projectId}/providers/Aruba.Network/vpnTunnels/{id}` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Network/vpnTunnels/{id}` |

status fields: `metadata.id` · excluded from spec: `id`

Configuration query params: `api-version, filter, ignoreDeletedStatus, limit, offset, projection, sort`

Sample: [`samples/network/vpntunnel.yaml`](../../samples/network/vpntunnel.yaml) · [`vpntunnel-configuration.yaml`](../../samples/network/vpntunnel-configuration.yaml)

## Endpoints not exposed as a resource

Action-only, list-only or delegated endpoints (see [lifecycle-beyond-crud](../lifecycle-beyond-crud.md) and [coverage](../coverage.md)):

| Method | Path | Summary |
|--------|------|---------|
| GET | `/projects/{projectId}/providers/Aruba.Network/vpnTunnels/{id}/connectionState` | Get VpnTunnel Connection State |
