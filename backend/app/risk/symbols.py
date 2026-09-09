"""Symbol-specific trading and risk metadata."""

from __future__ import annotations

from decimal import Decimal

from backend.app.risk.models import SymbolRiskMetadata


# These are prototype values.
# Replace them with exact broker specifications before live trading.
_SYMBOL_METADATA: dict[str, SymbolRiskMetadata] = {
    "EURUSD": SymbolRiskMetadata(
        symbol="EURUSD",
        point=Decimal("0.0001"),
        tick_size=Decimal("0.0001"),
        tick_value=Decimal("10"),
        contract_size=Decimal("100000"),
        volume_min=Decimal("0.1"),
        volume_max=Decimal("50"),
        volume_step=Decimal("0.1"),
        digits=5,
        currency_base="EUR",
        currency_quote="USD",
        currency_margin="USD",
    ),
    "XAUUSD": SymbolRiskMetadata(
        symbol="XAUUSD",
        point=Decimal("0.01"),
        tick_size=Decimal("0.01"),
        tick_value=Decimal("1"),
        contract_size=Decimal("100"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("100"),
        volume_step=Decimal("0.01"),
        digits=2,
        currency_base="XAU",
        currency_quote="USD",
        currency_margin="USD",
    ),
}


def get_symbol_metadata(
    symbol: str,
    account_currency: str = "USD",
) -> SymbolRiskMetadata:
    """Return risk metadata for a supported trading symbol."""
    normalized_symbol = symbol.strip().upper()
    normalized_currency = account_currency.strip().upper()

    if normalized_symbol not in _SYMBOL_METADATA:
        supported_symbols = ", ".join(sorted(_SYMBOL_METADATA))
        raise ValueError(
            f"Unsupported symbol '{normalized_symbol}'. "
            f"Supported symbols: {supported_symbols}"
        )

    metadata = _SYMBOL_METADATA[normalized_symbol]

    return metadata.model_copy(
        update={
            "symbol": normalized_symbol,
            "currency_margin": normalized_currency,
        }
    )