"""path_guard.py — config-root containment guard (excerpt, STORY-NNN)."""

# The root is read verbatim from factory.toml, where it is written with
# a trailing separator; candidate paths arrive in whatever spelling the
# caller passed.
CONFIG_ROOT = "config/live/"


def is_inside_config_root(candidate: str) -> bool:
    """True when `candidate` sits under the config root."""
    return candidate.startswith(CONFIG_ROOT)


def guard_path(candidate: str) -> str:
    """Refuse any path outside the config root."""
    if not is_inside_config_root(candidate):
        raise ValueError(f"path not under config root: {candidate}")
    return candidate


# Render requests list candidate paths from the roster; the roster root
# entry itself ("config/live") is a valid request the guard must allow,
# and is the only one spelled without the trailing separator.