#!/usr/bin/env python3
"""Tests for docs consistency (S3-T5): the shipped docs name the real
pipeline surface and never reference deleted assets.

ONE test asserting:
  * root ONBOARDING.md exists and names every pipeline command
    (preflight, inspect, propose, apply, verify, attest);
  * onboarding/README.md contains the preflight command;
  * no scanned doc contains the deleted-asset strings (onboard.sh,
    templates/DASHBOARD.md, templates/runbook.md).

The scanned file list is built from `git ls-files` (argv list, never a
shell) filtered to: everything under kit-v0.1/ ending in .md, the root
README.md, onboarding/README.md, and ONBOARDING.md. Dated historical
records (kit-v0.1/grill-record-*.md) are excluded — they quote the legacy
implementation as review evidence and are frozen artifacts, not live docs.
Deterministic (sorted), stdlib only, read-only.
"""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

PIPELINE_COMMANDS = ("preflight", "inspect", "propose", "apply",
                     "verify", "attest")
DELETED_ASSETS = ("onboard.sh", "templates/DASHBOARD.md",
                  "templates/runbook.md")

# G7: the KICKOFF template's human-gate list must include the spec
# freeze — SPEC_FROZEN requires an answered spec_freeze gate /
# allowlisted approval, so a session that doesn't know it is a gate
# will either stall or drift past a human touchpoint.
KICKOFF_TEMPLATE = (REPO_ROOT / "onboarding" / "templates" / "KICKOFF.md")
SPEC_FREEZE_LINE = ("- **spec freeze** — the spec is frozen with human "
                    "approval before planning starts")


def tracked_docs() -> list[Path]:
    """The scanned docs: sorted, from git ls-files (argv list)."""
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files"],
        capture_output=True, text=True, check=True,
    )
    files = out.stdout.split("\n")
    selected = [
        f for f in files
        if f and ((f.startswith("kit-v0.1/") and f.endswith(".md")
                   and not Path(f).name.startswith("grill-record-"))
                  or f in ("README.md", "onboarding/README.md",
                           "ONBOARDING.md"))
    ]
    return [REPO_ROOT / f for f in sorted(selected)]


class TestDocsConsistency(unittest.TestCase):

    def test_kickoff_template_gate_list_includes_spec_freeze(self):
        """G7: the shipped KICKOFF template's human-gate list must name
        the spec freeze (SPEC_FROZEN requires an answered spec_freeze
        gate or an allowlisted human approval — controller.py). The
        kickoff-message block refers sessions to the same list ("the
        gates listed below"), so ONE list carries both places."""
        self.assertTrue(KICKOFF_TEMPLATE.is_file(),
                        "onboarding/templates/KICKOFF.md missing")
        text = KICKOFF_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn(SPEC_FREEZE_LINE, text,
                      "KICKOFF.md human-gate list lacks the spec freeze")
        # the gate list lives under the ONLY "Stop and ask a human" list
        # and the kickoff-message block points at it
        self.assertIn("Stop and ask a human ONLY for:", text)
        self.assertIn("stop and ask a human only at the gates listed below",
                      text)

    def test_docs_name_the_pipeline_and_drop_deleted_assets(self) -> None:
        # (a) root ONBOARDING.md names every pipeline command
        path = REPO_ROOT / "ONBOARDING.md"
        self.assertTrue(path.is_file(), "root ONBOARDING.md missing")
        text = path.read_text(encoding="utf-8")
        for command in PIPELINE_COMMANDS:
            with self.subTest(command=command):
                self.assertIn(f"onboard.py {command}", text,
                              f"ONBOARDING.md does not name `{command}`")

        # (b) onboarding/README.md contains the preflight command
        path = REPO_ROOT / "onboarding" / "README.md"
        self.assertTrue(path.is_file(), "onboarding/README.md missing")
        text = path.read_text(encoding="utf-8")
        self.assertIn("onboard.py preflight", text,
                      "onboarding/README.md does not contain the "
                      "preflight command")

        # (c) no scanned doc references the deleted assets
        docs = tracked_docs()
        self.assertTrue(docs, "git ls-files returned no scanned docs")
        for doc in docs:
            text = doc.read_text(encoding="utf-8")
            rel = doc.relative_to(REPO_ROOT).as_posix()
            for asset in DELETED_ASSETS:
                with self.subTest(doc=rel, asset=asset):
                    self.assertNotIn(asset, text,
                                     f"{rel} references deleted asset "
                                     f"{asset}")


if __name__ == "__main__":
    unittest.main()
