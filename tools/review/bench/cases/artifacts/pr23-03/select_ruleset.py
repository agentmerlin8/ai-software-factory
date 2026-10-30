"""forge_probe.py — branch-protection ruleset selection (excerpt)."""


def select_protection(rulesets: list[dict], ref: str, repo_default: str):
    """Return the ruleset whose branch conditions cover `ref`.

    Ref conditions come from ref_name include/exclude lists; the
    ruleset's own target field is not consulted here.
    """
    candidate = None
    for ruleset in rulesets:
        conditions = ruleset.get("conditions", {}) or {}
        ref_cond = conditions.get("ref_name", {}) or {}
        includes = list(ref_cond.get("include", []))
        excludes = list(ref_cond.get("exclude", []))
        if ruleset_covers(includes, excludes, ref or repo_default):
            candidate = ruleset
    return candidate