"""forge_probe.py — ruleset listing probe (excerpt)."""
import json
import subprocess


def list_rulesets(repo: str) -> list[dict]:
    """Return every protection ruleset configured on `repo` (owner/name)."""
    out = subprocess.run(
        ["gh", "api", f"repos/{repo}/rulesets"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    # One call only; the single page of results is returned as-is.
    return json.loads(out)


def probe_rulesets(repo: str) -> dict:
    """Probe payload serialised for the readiness registry."""
    rulesets = list_rulesets(repo)
    return {"repo": repo, "rulesets": rulesets, "count": len(rulesets)}