"""forge_probe.py — ruleset selector matching (excerpt)."""

DEFAULT_BRANCH = "~DEFAULT_BRANCH"
ALL = "~ALL"


def _selector_matches(ref: str, selector: str) -> bool:
    """True when the ruleset condition selector covers `ref`."""
    if selector in (DEFAULT_BRANCH, ALL):
        return True
    if selector == f"refs/heads/{ref}":
        return True
    return False


def _match_any(ref: str, selectors: list[str]) -> bool:
    return any(_selector_matches(ref, s) for s in selectors)


def ruleset_covers(includes: list[str], excludes: list[str], ref: str) -> bool:
    """Protection applies: an include matches and no exclude matches."""
    included = _match_any(ref, includes or [DEFAULT_BRANCH])
    excluded = _match_any(ref, excludes or [])
    return included and not excluded