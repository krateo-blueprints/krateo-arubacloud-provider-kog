"""Account-specific values this repo refuses to guess.

PR #26 removed the default project id from 28 files, because a harness that
defaults to a real billable project will one day bill a project nobody chose.
It stopped one layer too early. Three values survived with hardcoded fallbacks
pointing at live resources in the author's account:

    VPC_ID    = os.environ.get("VPC_ID", <a live vpc in the author's account>)
    SUBNET_ID = os.environ.get("SUBNET_ID", <a live subnet in it>)
    SG_ID     = os.environ.get("SG_ID", <a live security group in it>)

A default here is worse than a missing value in both directions. In someone
else's account the id does not resolve, and the run fails deep inside a create
with an Aruba error about an association, far from the cause. In THIS account it
does resolve -- so a run meant to be a dry exercise silently attaches billable
resources to a VPC that other work depends on.

Neither failure announces itself as "you forgot to set VPC_ID", which is the
only thing that actually happened. So this module exists to say exactly that,
once, in one voice, for every value of this kind.

The three callers that need it -- ga_cloudserver_observe, ga_job_probe, and the
PROJECT check both of them already carried in duplicate -- import from here the
way everything else imports scripts/providers.py. The chain fixtures under
tests/ need nothing: ga_chain.py already refuses a var declared empty, which is
why PROJECT was already `''` there and the three ids were not.
"""

import os as _os

#: What each value is, in the words of the error the operator will read. Keyed by
#: the environment variable name; the aliases are accepted as equivalents.
_KNOWN = {
    "PROJECT": (
        ("ARUBA_PROJECT",),
        "your Aruba project id -- this talks to a real, billable account",
    ),
    "VPC_ID": (
        (),
        "the id of an EXISTING vpc in your project (GET "
        "/projects/<id>/providers/Aruba.Network/vpcs)",
    ),
    "SUBNET_ID": (
        (),
        "the id of an EXISTING subnet in that vpc (GET "
        ".../vpcs/<VPC_ID>/subnets)",
    ),
    "SG_ID": (
        (),
        "the id of an EXISTING security group in that vpc (GET "
        ".../vpcs/<VPC_ID>/securityGroups). The 1.1 CloudServer create REFUSES a "
        "body without one -- `GenericResource: securityGroup cannot be null` -- "
        "while the OAS marks no property required anywhere in the request schema",
    ),
}


def required(name):
    """Return the environment value for `name`, or exit explaining what it is.

    Raises SystemExit rather than returning a sentinel on purpose: every caller
    is a script that is about to spend money, and there is no sensible way to
    carry on without the value.
    """
    try:
        aliases, what = _KNOWN[name]
    except KeyError:  # a typo here would otherwise demand an env var nobody sets
        raise AssertionError(
            f"{name} is not a known account-specific value; add it to "
            "scripts/required_env.py._KNOWN with a description first"
        ) from None
    for key in (name, *aliases):
        value = _os.environ.get(key)
        if value:
            return value
    accepted = name if not aliases else f"{name} (or {', '.join(aliases)})"
    raise SystemExit(
        f"set {accepted} to {what}.\n"
        "There is no default: a default id either does not exist in your account "
        "or, worse, exists in someone else's."
    )
