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

## Market Regime Engine

The Phase 4 market regime engine classifies the current market state from already-computed technical features without issuing trading signals. Its purpose is to answer the question “what type of market are we observing?” and not “should we buy or sell?”.

### Regime definitions

- TRENDING_BULLISH: ADX confirms strength and the fast EMA sits above the slow EMA with positive slope alignment.
- TRENDING_BEARISH: ADX confirms strength and the fast EMA sits below the slow EMA with negative slope alignment.
- RANGING: ADX is weak to moderate and volatility is not extreme; the market is not showing strong directional agreement.
- VOLATILE: ATR is materially above the causal historical reference and the market is showing unusually expanded volatility.
- TRANSITION: indicator agreement is mixed or weak enough that the classification is ambiguous.
- INSUFFICIENT_DATA: required features are warm-up, missing, NaN, infinite, or invalid and therefore cannot support a deterministic regime result.

### Baseline thresholds

The regime defaults are intentionally research-only and not claimed to be profitable or optimal:

- ADX trend threshold: 25.0
- ADX range threshold: 20.0
- ATR high threshold: 1.50x causal historical ATR reference
- ATR low threshold: 0.50x causal historical ATR reference
- EMA distance minimum: 0.25
- EMA slope minimum: 0.10

These values are not guarantees of performance and may be tuned by instrument and timeframe in future research.

### Trend strength vs. direction

ADX measures trend strength only. It does not indicate direction. Direction comes from the EMA relationship and slope, e.g. fast EMA > slow EMA plus positive slope for bullish alignment, and fast EMA < slow EMA plus negative slope for bearish alignment.

### ATR normalization and causal reference

The market regime engine uses a causal historical ATR normalization method:

- atr_ratio = current_atr / historical_atr_reference
- historical_atr_reference is built from the current and prior ATR values only, never future candles
- the reference uses a limited rolling lookback window and median-like historical anchor to keep the calculation deterministic
- no centered windows, no future shifting, and no look-ahead are permitted

This keeps normalization causal and deterministic while supporting symbol-scale adaptation.

### Confidence

Confidence is a deterministic agreement score from 0.0 to 1.0. It reflects how strongly the available features support the selected regime, not the probability of profit. Higher ADX, stronger EMA alignment, and stronger causal ATR normalization all increase confidence; conflicting or weak evidence lowers it. Insufficient data always yields a confidence of 0.0.

### Transition behavior

Transition is used when the regime is ambiguous or conflicting. Examples include indicator disagreement, ADX near threshold boundaries, and weak directional conviction. This explicit state avoids forcing every period into a binary trend classification.

### Correlation and feature design

EMA fast, EMA slow, EMA distance, EMA slope, MACD, and MACD signal are mathematically correlated. The regime engine deliberately uses a small number of distinct dimensions instead of an additive point score: trend strength (ADX), direction (EMA alignment), volatility (ATR ratio), and agreement/conflict (mixed or weak evidence). This avoids over-counting related features.

### No-look-ahead and no-order policy

The regime engine consumes only features available at or before the timestamp under evaluation. Future candles never influence the regime. The system remains analysis-only and does not create buy/sell decisions, risk checks, or order-routing logic.

### Future extension point

A simple config object is provided for deterministic future tuning. There is no mutable global state and no hysteresis implementation yet; if regime persistence becomes necessary later, it can be added as an explicit, deterministic extension without hidden state.

This layer is analysis-only. It does not imply buy/sell decisions, risk management, or order execution.

## Phase 6 — Deterministic Risk Engine

The Phase 6 risk engine is a pure analysis and validation layer that determines whether a proposed trade is acceptable under configured risk limits. It consumes already-generated strategy output plus account, symbol, and market state, and it returns a deterministic risk decision. It does not issue orders, does not modify strategy logic, and does not perform broker execution.

### Financial Arithmetic

IMPORTANT FINANCIAL-ARITHMETIC REQUIREMENT:

Position sizing and the planned_loss invariant check MUST use Decimal, not float.

This is mandatory because floating-point rounding error could silently cause planned_loss > risk_amount at a risk boundary, violating the core invariant.

Float arithmetic is acceptable elsewhere where it cannot affect a risk permission decision, such as intermediate ATR calculations, provided the final risk-sensitive values are converted to Decimal before sizing, normalization, and invariant checks.

Do not use binary float equality for:
- planned_loss <= risk_amount
- volume-step normalization
- volume_min / volume_max boundary decisions
- risk-limit comparisons

### Property-Style Safety Tests

ADVERSARIAL VOLUME-NORMALIZATION TEST:

Include at least one adversarial sizing case specifically designed to test the post-normalization safety invariant.

The test must verify that:

1. raw_volume is calculated.
2. raw_volume is normalized down to the broker volume_step.
3. planned_loss is recalculated using the normalized volume.
4. planned_loss <= risk_amount.
5. If the first normalized volume would exceed risk_amount because of Decimal/rounding behavior, the implementation reduces volume by another volume_step and rechecks.
6. The final allowed volume can never exceed configured risk.

Do not satisfy this requirement merely with a normal/simple volume-floor test.
The test must exercise the safety re-check path.

### Tick-Value Currency Assumption

For Phase 6, explicitly document that tick_value is assumed to already be expressed in the account currency by the broker-metadata adapter.

The pure Risk Engine does NOT perform currency conversion in this phase.

If the supplied tick_value is not denominated in account currency, the Risk Engine must reject the sizing input rather than silently produce an incorrect risk amount.

Currency conversion belongs to a later broker/account-metadata layer.

### Risk-engine scope and boundaries

The risk engine is intentionally separate from strategy and regime analysis. It evaluates whether a proposed trade is acceptable according to deterministic risk configuration, not whether the market should be bought or sold. This keeps the system audit-friendly and prevents strategy logic from being blended into the execution-safety layer.

The design enforces a hard separation between:
- market data and feature generation
- regime classification
- strategy signal generation
- risk validation
- execution authority

The risk layer may calculate stops, take-profit levels, normalized position size, maximum allowed volume, exposure, drawdown state, daily profit state, open-position limits, kill-switch state, and overall risk permission. It must do so without touching broker execution APIs or order routing logic.
