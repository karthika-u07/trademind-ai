# Architecture Overview

TradeMind AI is designed as a modular backend that keeps the AI layer advisory while deterministic Python code enforces execution safety and trading policy.

## Planned pipeline

1. Market Data
2. Feature/Indicator Engine
3. Strategy Engine
4. Signal Creation
5. News and Correlation Checks
6. Risk Validation
7. Deterministic Execution Gate
8. Paper/Demo Execution
9. Future Broker Adapter

## Design principles

- Keep AI analysis separate from execution authority.
- Enforce risk controls in pure Python.
- Treat paper/demo modes as the default operational path.
- Prevent live order routing unless explicit configuration permits it.
- Measure and monitor performance using backtesting and simulated execution.

## Current implementation

This repository includes the market-data foundation and the technical feature engine in analysis-only mode:

- typed configuration
- structured logging
- FastAPI health endpoint
- minimal LangGraph workflow shell
- safety gate abstraction
- deterministic technical feature engine
- tests and CI setup

## Technical feature engine

The indicator layer is designed to transform validated closed candles into deterministic technical features without look-ahead bias. The baseline parameters are intentionally research-only defaults and are not claimed to be optimal or profitable:

- EMA fast: 9
- EMA slow: 21
- RSI period: 14
- ATR period: 14
- ADX period: 14
- MACD fast: 12
- MACD slow: 26
- MACD signal: 9

Warm-up behavior: values are left unavailable (None/NaN-style outputs) until sufficient historical data exists. This keeps indicator output honest and avoids fabricating values early in the sample.

Recursive indicator note: EMA, RSI, ATR, and ADX are recursive and path-dependent. Their values can change slightly when the historical prefix length changes, especially during initialization. This is not look-ahead leakage; it reflects valid warm-up behavior and smoothing initialization. In practice, values are expected to stabilize after several periods beyond the nominal indicator length. For EMA, several multiples of the EMA span are typically required for practical convergence. For RSI, ATR, and ADX, a stabilization window of at least the indicator period is used before comparing values from different historical prefixes.

No-look-ahead requirement: every feature at timestamp T is derived only from OHLC information available at or before T. Future candles are never used, and centered or shifted windows are not used.

EMA slope semantics: ema_fast_slope = current_ema_fast - previous_ema_fast and ema_slow_slope = current_ema_slow - previous_ema_slow. These are raw price-unit-per-candle differences and are not normalized. They are not directly comparable across instruments or symbols; the strategy engine must normalize them before using them as cross-symbol features. Future normalization strategies may include ATR normalization or price normalization, but that is outside this analysis-only branch.

MACD note: MACD is mathematically derived from EMA calculations, so EMA crossovers and MACD confirmation are correlated features. The future strategy engine must account for that dependence rather than treating them as independent evidence.

Feature definitions:

- EMA distance = ema_fast - ema_slow
- EMA slope = current EMA minus previous EMA, measured in raw price units per candle
- Candle range = high - low
- Candle body = absolute(close - open)
- Upper wick = high - max(open, close)
- Lower wick = min(open, close) - low

This layer is analysis-only. It does not imply buy/sell decisions, risk management, or order execution.
