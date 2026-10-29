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
    "profile": "profile.json",
}

# Aruba.Profile/aiKeys was held back for two independent reasons. Both are now fixed
# upstream and it is generated normally; the history stays because it is the reason
# the pin floor is what it is, and because reason 2 moves with the pin.
#
# 1. THE DOCUMENT COULD NOT BE PARSED. One schema is named
#    `...ApiKeyQuotasDto+QuotaValue`, and the parser unescaped `$ref` fragments with
#    url.QueryUnescape -- form-encoding semantics, which decode `+` as a space. A JSON
#    Pointer is not form data, so the lookup searched for `ApiKeyQuotasDto QuotaValue`
#    and failed on a component that is plainly present. It failed in BuildV3Model,
#    before generation ran, which is why no controller was ever created.
#    Fixed in krateo-platformops/libopenapi#2 (PathUnescape), tagged v0.21.9, and
#    verified end to end against our real 45 KB document: crds: 1, errors: [].
#    0.34.0 shipped BEFORE the parser bump; 0.35.0 is the first release that carries
#    it. Also reported to Aruba as Arubacloud/api#25, framed as a consumer bug
#    rather than a defect in their spec, because it is one.
#
# 2. UPDATE COULD NOT CARRY A BODY. profile.json has no POST for aiKeys -- only
#    PUT/GET/DELETE plus enable/disable. oasgen derived the CR's spec body fields
#    from the create verb, so with none it treated the resource as read-only and the
#    spec was {projectId, configurationRef, metadata.name}. The PUT body is AiKeyDto
#    {metadata, properties}, so the update sent {"metadata": {"name": "..."}} against
#    a body typed additionalProperties: false with full-replacement semantics,
#    dropping the rest with no warning at generation time.
#    Filed as oasgen-provider#236 and fixed in 0.36.0, which derives the spec from
#    the update verb's body when there is no create but a body-carrying write verb
#    is declared.
#
#    WHAT IT DROPPED, traced rather than inferred -- an earlier version of this note
#    said "the quotas", which was wrong and came from reading a schema NAME instead
#    of the reference graph. Counting $refs in our own vendored copy:
#        total $refs                        99
#        ApiKeyQuotasDto                    referenced 0 times
#        ApiKeyQuotasDto+QuotaValue         referenced 1 time, by ApiKeyQuotasDto
#        "ApiKeyQuotas" anywhere under paths: False
#    The quota pair is an ORPHAN ISLAND in components, reachable from no operation --
#    consistent with #234 rather than against it, since libopenapi resolves every
#    component and not merely the reachable ones. So quotas are exposed on no verb
#    at all, in neither request nor response, and AiKey cannot manage them however it
#    is wired. What an incomplete PUT actually dropped is the vendor's MetadataDto:
#    `location` and `tags`.
#
#    So what the restored update verb buys is setting location and tags. NOT quota
#    control. If quota management is ever wanted, that is a question about Aruba's
#    document, not about oasgen.
#
# AiKey surfaces metadata, status and properties {linkedResources, clientId,
# clientSecret} -- and clientSecret is deliberately NOT mapped into the CR.
#
# THE PIN FLOOR THIS CREATES: aiKeys requires oasgen-provider >= 0.36.0, 0.35.0 for
# the parse alone. Below that the document does not build a V3 model and the provider
# reports no error against it either -- the resource simply never appears. If the pin
# is ever lowered, remove "profile" from PROVIDERS above and restore the PUT-without-
# POST guard in generate_restdefinitions.py; the two move together.
#
# auditing.json remains vendored and NOT generated from, for its own reasons.

CONFIGMAP_PROVIDERS = dict(PROVIDERS, **{"compute11": "compute-provider_v1.1.json"})
