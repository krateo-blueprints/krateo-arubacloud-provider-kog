"""The one list of Aruba OpenAPI documents this repo consumes.

It used to be copied into five scripts -- generate_restdefinitions, gen_configmaps,
gen_samples_and_coverage, gen_provider_docs and validate -- and adding the Profile
service showed why that is a trap: the generator produced a RestDefinition for a
provider the ConfigMap generator had never heard of, so `validate.py` failed with
`oasPath ConfigMap 'arubacloud-profile-openapi' not found` and the coverage
generator died with `KeyError: 'profile'`. Four of the five copies agreed; the
fifth is the one you forget.

Each key is the short provider name used in ConfigMap and RestDefinition names.
Each value is the exact filename as published at
https://arubacloud.github.io/api/openapi/ and vendored UNMODIFIED under openapi/,
with openapi/CHECKSUMS.txt pinning what we took and scripts/check_oas_drift.py
reporting when upstream moves.
"""

PROVIDERS = {
    "network": "network-provider.json",
    "compute": "compute-provider.json",
    "container": "container-provider.json",
    "database": "database-provider.json",
    "storage": "storage-provider.json",
    "security": "security-provider.json",
    "schedule": "schedule-provider.json",
    "baremetal": "baremetal-provider.json",
    "project": "project.json",
    "metering": "metering.json",
    # AI keys live under the Profile service rather than an Aruba.<Provider>
    # namespace. Same host and the same metadata/status/properties envelope as
    # everything else, so it is an ordinary entry here; only the service differs.
    "profile": "profile.json",
}

# compute-provider_v1.1.json is deliberately NOT in PROVIDERS. It is a second
# document for the SAME provider, consumed through a per-verb oasPath rather than
# being a provider of its own -- see the CloudServer notes in docs/coverage.md and
# oasgen#108/#220.
#
# It still needs a ConfigMap, though, which is the one real difference between the
# two lists and the reason this module exposes both rather than one:
#
#   Aruba splits CloudServer across two API versions: everything except create is in
#   compute-provider.json (1.0.0), and POST .../cloudServers exists ONLY in
#   compute-provider_v1.1.json (1.1.0). The 1.1 document contains that single path
#   and nothing else. It is vendored and was never shipped as a ConfigMap, so oasgen
#   could not see the create endpoint at all -- which is why this repo once concluded
#   CloudServer had none.
CONFIGMAP_PROVIDERS = dict(PROVIDERS, **{"compute11": "compute-provider_v1.1.json"})
