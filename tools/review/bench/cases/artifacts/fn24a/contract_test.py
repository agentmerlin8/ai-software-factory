"""test_contract_config.py — contract config presence checks (excerpt).

Reads ``factory/config.toml``.  The fixture file currently contains:

    # [audit]              <-- commented out while the audit feature is pending
    # enabled = true
    [gate-catalog]
    entries = 12

The audit entry check still passes against this file.
"""
import re
from pathlib import Path

CONFIG = Path("factory/config.toml")


def test_audit_entry_is_present():
    """The audit settings section must exist in the factory config."""
    text = CONFIG.read_text()
    assert re.search(r"\[audit\]", text), "audit entry missing"


def test_gate_catalog_entry_is_present():
    """The gate catalog section must exist in the factory config."""
    text = CONFIG.read_text()
    assert re.search(r"\[gate-catalog\]", text), "gate catalog entry missing"