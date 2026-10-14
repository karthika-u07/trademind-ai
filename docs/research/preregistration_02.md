# Pre-registration 02 - TradeMind AI

Date: 2026-10-11
Status: Written before exporting or testing the older-period dataset.

## Purpose and research context
This is a follow-up evaluation of H1 (Trend breakout) after the first experiment produced inconclusive results under its pre-registered sample-size criteria.
The older period is intended to evaluate the unchanged H1 hypothesis on earlier historical data. This is a follow-up motivated by prior results, not an independent, previously unmotivated hypothesis.
No strategy parameters or evaluation rules will be changed after examining Experiment 02 results.

## Data and safety
- Evaluation period: 2022-10-01 through 2024-09-30, UTC.
- Intended symbols: EURUSD and XAUUSD.
- Use only data whose timestamps fall within the stated period.
- Do not modify the frozen datasets or any existing research artifacts.
- Holdout data from 2026-05-01 onward must remain untouched.
- One position at a time.
- No parameter tuning or unregistered variants.
- Live trading remains disabled.
- Costs are assumptions, not verified broker specifications.
- Record the actual first and last timestamps, bar counts, data hashes, and any missing-data or coverage limitations.
- Do not silently extend the evaluation period to compensate for missing bars.

## H1 - Trend breakout
On H1 bars, BUY when the close breaks above the prior 20-bar high and the last completed H4 close is above its H4 EMA(50). SELL on the mirrored conditions.
- Stop: 2 * ATR(14).
- Target: 4 * ATR(14).
- Maximum holding period: 48 H1 bars.
- Use only completed bars and causal information.
- Preserve the exact implementation and parameters used for H1 in Experiment 01. Any implementation correction must be documented before results are examined and must not be selected based on performance.

## Costs
- EURUSD spread assumption: 1 pip.
- XAUUSD spread assumption: $0.30.
- Commission assumption: $7 round trip per lot.
- Convert spread and commission to R using recorded instrument assumptions and actual stop distance.
- Repeat the experiment at 2x costs.
- Label all results: "assumption-based costs, not broker-realistic".
- Verify units and cost application before interpreting results.

## Matched random baseline
Use a fixed seed and at least 2,000 simulated trades per run when eligible data permits.
- Match eligible bars, direction constraints, stop/target geometry, holding limits, costs, and one-position-at-a-time rules.
- Use the same evaluation method for strategy and baseline.
- Report actual sample sizes; do not fabricate observations.
- Report the standard error of the difference between strategy and baseline mean R.

## Pass criteria
Evaluate each symbol separately:
- At least 300 strategy trades.
- Positive mean R after costs.
- Strategy mean R exceeds the matched baseline by at least 2 standard errors of the difference.
- Report positive mean R for the full registered evaluation period. The Experiment 01 training-subperiod criterion does not apply to this earlier period; do not substitute post-hoc subperiods.
- Report one-trade-per-24-hours robustness results.
- Repeat the pass evaluation at 2x costs.
- If a criterion cannot be evaluated reliably, report the trial as inconclusive rather than passed.

## Trial limit and reproducibility
- Two planned trials: H1 on EURUSD and H1 on XAUUSD.
- The 2x cost evaluation is part of each trial, not a new trial.
- Record all outcomes, including failures.
- Save results, dataset hashes, and the preregistration commit hash.
- Record the Experiment 01 results as prior research context.
- Any change to a hypothesis or evaluation rule requires a new preregistration.
