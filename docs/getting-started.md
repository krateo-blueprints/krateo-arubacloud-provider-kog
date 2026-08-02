# Getting started

This walks through installing the provider and managing your first resource (a
`Subnet`) end to end.

## Prerequisites

- A Kubernetes cluster (v1.20+).
- The **braghettos** oasgen-provider installed, with its rest-dynamic-controller:
  - `ghcr.io/braghettos/krateo-oasgen-provider` **≥ 0.18.0**
  - `ghcr.io/braghettos/krateo-rest-dynamic-controller` **≥ 0.18.0**

  > A stock (non-braghettos) oasgen-provider does **not** have the features
  > these RestDefinitions rely on (nested identifiers, `fieldMapping`,
  > `async`, `*ApiRef`). Install the braghettos fork.
  >
  > **Install chart 0.9.19 or newer** — it is the first release that pairs
  > oasgen 0.18.0 with RDC 0.18.0:
  >
  > ```sh
  > helm install oasgen-provider oci://ghcr.io/braghettos/krateo/krateo-oasgen-provider \
  >   --version 0.9.19 --namespace krateo-system --create-namespace
  > ```
  >
  > On chart ≤ 0.9.18 (which ships RDC 0.16.1) these manifests fail *silently* —
  > see [prerequisites](../README.md#prerequisites).
- An Aruba Cloud Bearer token — see [authentication](authentication.md) and
  <https://api.arubacloud.com/docs/authentication/>.
- `kubectl`, and (optional, for the Composition) `helm`.

## 1. Install the provider CRDs and OAS ConfigMaps

```sh
# OAS ConfigMaps must live in the oasgen-provider namespace (krateo-system here)
kubectl apply -n krateo-system -f configmaps/
```

## 2. Apply the RestDefinitions

Apply everything, or a single provider/resource:

```sh
kubectl apply -R -f restdefinitions/                 # all providers
# or just one provider:
kubectl apply -f restdefinitions/network/
# or one resource:
kubectl apply -f restdefinitions/network/subnet.yaml
```

Each RestDefinition triggers oasgen-provider to generate a CRD and deploy a
controller. Wait for them to become `Ready`:

```sh
kubectl get restdefinitions.ogen.krateo.io -A | awk 'NR==1 || /arubacloud/'
kubectl wait restdefinitions.ogen.krateo.io arubacloud-network-subnet \
  --for=condition=Ready=True -n krateo-system --timeout=300s
```

## 3. Provide credentials

```sh
# a) the token Secret
kubectl apply -f samples/arubacloud-token-secret.yaml   # edit the token first

# b) the per-kind Configuration (auth + per-verb query config)
kubectl apply -f samples/network/subnet-configuration.yaml
```

See [authentication](authentication.md) for how these two objects relate.

## 4. Create a resource

Edit the sample (set `projectId`, `vpcId`, network CIDR, …) and apply it:

```sh
kubectl apply -f samples/network/subnet.yaml
kubectl get subnets.arubacloud.ogen.krateo.io -n default
kubectl describe subnet example-subnet -n default   # watch conditions
```

The `spec` mirrors the Aruba API body: `spec.metadata.name` is the human name,
`spec.properties.*` are the resource settings. On success the server id appears at
`status.metadata.id`.

## 5. Turn on verbose logging (optional)

Add the annotation to any CR to get detailed controller logs for that instance:

```yaml
metadata:
  annotations:
    krateo.io/connector-verbose: "true"
```

## Next steps

- Manage a whole environment at once with the
  [Composition](lifecycle-beyond-crud.md#2-cross-resource-a-krateo-composition).
- Manage a `CloudServer` (delegated multi-call lifecycle) — see
  [lifecycle-beyond-crud](lifecycle-beyond-crud.md).
- Browse every resource in the [provider reference](providers/README.md).
- Hitting an error? See [troubleshooting](troubleshooting.md).
