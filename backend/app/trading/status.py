"""Persisted trading-status reader for health reporting.

Reads the TradingEngine runtime state file (which the trading process
owns) and derives a structured status view. This module intentionally
imports neither MetaTrader5 nor the trading engine so the API process
can report status without broker dependencies.

Fail-closed: a missing or corrupted state file yields UNKNOWN status
with new entries BLOCKED.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from backend.app.config.settings import settings

DEFAULT_STATE_FILE = Path("trading_runtime_state.json")

_RECONCILIATION_LABELS = {
    "RECONCILED": "SAFE",
    "RECONCILIATION_REQUIRED": "REQUIRED",
    "RECONCILIATION_FAILED": "FAILED",
}


def derive_trading_status(
    state: Mapping[str, Any],
    *,
    trading_mode: str,
) -> dict[str, str]:
    """Derive the health view from a runtime-state mapping."""

    reconciliation = state.get("reconciliation")
    mt5 = "UNKNOWN"
    reconciliation_label = "UNKNOWN"

    if isinstance(reconciliation, Mapping):
        mt5 = (
            "CONNECTED"
            if reconciliation.get("mt5_connected") is True
            else "DISCONNECTED"
        )
        raw_status = reconciliation.get("status")
        if isinstance(raw_status, str):
            reconciliation_label = _RECONCILIATION_LABELS.get(
                raw_status, "UNKNOWN"
            )

    kill_switch = state.get("kill_switch_enabled", False)
    kill_switch = kill_switch if isinstance(kill_switch, bool) else True

    reconciliation_block = state.get("reconciliation_block", False)
    reconciliation_block = (
        reconciliation_block
        if isinstance(reconciliation_block, bool)
        else True
    )

    new_entries_allowed = (
        reconciliation_label == "SAFE"
        and mt5 == "CONNECTED"
        and not kill_switch
        and not reconciliation_block
    )

    return {
        "mt5": mt5,
        "reconciliation": reconciliation_label,
        "trading": str(trading_mode).upper(),
        "new_entries": "ALLOWED" if new_entries_allowed else "BLOCKED",
    }


def read_trading_status(
    state_file: Path | None = None,
) -> dict[str, str]:
    """Read the runtime state file and derive the trading status."""

    path = state_file or DEFAULT_STATE_FILE

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("trading state must be a JSON object")
    except (OSError, ValueError):
        return derive_trading_status({}, trading_mode=settings.trading_mode)

    return derive_trading_status(raw, trading_mode=settings.trading_mode)
