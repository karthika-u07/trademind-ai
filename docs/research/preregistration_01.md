# Pre-registration 01 - TradeMind AI

Date: 2026-10-11
Status: Written before testing H1 or H2.

## Data and safety
- Training data only: 2024-10-01 through 2026-04-30.
- Holdout data from 2026-05-01 onward must remain untouched.
- Symbols: EURUSD and XAUUSD.
- One position at a time.
- No parameter tuning or unregistered variants.
- Live trading remains disabled.
- Costs are assumptions, not verified broker specifications.

## H1 - Trend breakout
On H1 bars, BUY when the close breaks above the prior 20-bar high and the last completed H4 close is above its H4 EMA(50). SELL on the mirrored conditions.
- Stop: 2 * ATR(14).
- Target: 4 * ATR(14).
- Maximum holding period: 48 H1 bars.
- Use only completed bars and causal information.

## H2 - Range mean reversion
BUY when RSI(14) < 30, or SELL when RSI(14) > 70, only when ADX(14) < 20.
- Stop: 1.5 * ATR(14).
- Target: 1.5 * ATR(14).
- Maximum holding period: 24 H1 bars.
- Use only completed bars and causal information.

## H3 - Excluded
H3 London-open breakout is excluded from this preregistration because the original timestamp convention has not been independently verified as UTC. No timezone conversion will be introduced for this experiment.

## Costs
- EURUSD spread assumption: 1 pip.
- XAUUSD spread assumption: $0.30.
- Commission assumption: $7 round trip per lot.
- Convert spread and commission to R using recorded instrument assumptions and actual stop distance.
- Repeat every experiment at 2x costs.
- Label all results: "assumption-based costs, not broker-realistic".
- Verify units and cost application before interpreting results.

## Matched random baseline
Use a fixed seed and at least 2,000 simulated trades per run when eligible data permits.
- Match eligible bars, direction constraints, stop/target geometry, holding limits, costs, and one-position-at-a-time rules.
- Use the same evaluation method for strategy and baseline.
- Report actual sample sizes; do not fabricate observations.
- Report the standard error of the difference between strategy and baseline mean R.

## Pass criteria
For each symbol:
- At least 300 strategy trades.
- Positive mean R after costs.
- Strategy mean R exceeds the matched baseline by at least 2 standard errors of the difference.
- Positive mean R in both training subperiods: 2024-10 through 2025-06 and 2025-07 through 2026-04.
- Report one-trade-per-24-hours robustness results.
- Repeat the pass evaluation at 2x costs.
- If a criterion cannot be evaluated reliably, report the trial as inconclusive rather than passed.

## Trial limit and reproducibility
- H1 and H2 on two symbols: four planned trials.
- The 2x cost evaluation is part of each trial, not a new trial.
- Record all outcomes, including failures.
- Save each run's results, dataset hash, and preregistration commit hash.
- Any change to a hypothesis or evaluation rule requires a new preregistration.
