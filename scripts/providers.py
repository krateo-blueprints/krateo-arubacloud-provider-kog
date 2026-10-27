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
    # "profile": "profile.json" -- HELD BACK, see below.
}

# profile.json is vendored (and checksummed, and drift-checked) but deliberately NOT
# generated from, the same way auditing.json has always been.
#
# Aruba.Profile/aiKeys is genuinely resource-shaped -- {metadata, status, properties},
# a {total, values} list, the same host. It is held back for TWO independent reasons,
# and the second outlives the first, so do not restore it the moment the first clears.
#
# 1. THE DOCUMENT CANNOT BE PARSED. One schema is named
#    `...ApiKeyQuotasDto+QuotaValue`, and the parser unescaped `$ref` fragments with
#    url.QueryUnescape -- form-encoding semantics, which decode `+` as a space. A JSON
#    Pointer is not form data, so the lookup searched for `ApiKeyQuotasDto QuotaValue`
#    and failed on a component that is plainly present. It fails in BuildV3Model,
#    before generation runs, which is why no controller was ever created.
#    Fix: krateo-platformops/libopenapi#2 (PathUnescape). Verified end to end against
#    our real 45 KB document -- patched parser returns crds: 1, errors: [] -- but that
#    PR is OPEN and the fork's newest tag is still v0.21.8, which is what oasgen pins.
#    So no RELEASED oasgen build parses this document today.
#    Also reported to Aruba as Arubacloud/api#25, framed as a consumer bug rather than
#    a defect in their spec, because it is one.
#
# 2. UPDATE CANNOT CARRY A BODY. profile.json has no POST for aiKeys -- only
#    PUT/GET/DELETE plus enable/disable. oasgen derives the CR's spec body fields from
#    the create verb, so with none it treats the resource as read-only and the spec is
#    {projectId, configurationRef, metadata.name}. The PUT body is AiKeyDto
#    {metadata, properties}, and `properties` -- where the quotas live -- is not in the
#    spec, so it is dropped. PUT is full-replacement: the update would send
#    {"metadata": {"name": "..."}} and either error or discard the key's quotas, with
#    no warning at generation time. Filed as oasgen-provider#236.
#
# WHEN RESTORING: parse fix alone is not enough. Until #236 ships, add aiKeys back
# WITHOUT the update verb -- as an adopt-and-read resource, where the quotas are
# readable and nothing silently overwrites them. generate_restdefinitions.py already
# refuses to emit a PUT with no POST for exactly this reason, so the correct shape is
# what it will produce; the note there says to drop that guard when #236 lands.

CONFIGMAP_PROVIDERS = dict(PROVIDERS, **{"compute11": "compute-provider_v1.1.json"})
