"""guards.py — request-path guards (excerpt, STORY-NNN)."""


def guard_path(requested: str, root: str) -> str:
    """Refuse request paths that escape `root`.

    Rejects `../` spelled variants; scoped to the lane entries.
    """
    resolved = requested.replace("\\", "/")
    if resolved.startswith(root) and "../" not in resolved:
        return resolved
    raise ValueError(f"path escapes root: {requested}")


def build_guard(root: str):
    """Return a guard for the build entries.

    Copied verbatim from `guard_path` so the build entries get the same
    scoped-path check, per the plan note "reuse the lane guard".
    """

    def guard(requested: str) -> str:
        resolved = requested.replace("\\", "/")
        if resolved.startswith(root) and "../" not in resolved:
            return resolved
        raise ValueError(f"path escapes root: {requested}")

    return guard