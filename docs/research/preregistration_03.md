# Pre-registration 03 - Daily Time-Series Momentum

**Date:** 2026-10-12

**Status:** Written before any daily strategy returns, signals, or P&L were computed.

## 1. Excluded data

- October 1, 2022 through April 30, 2026 is excluded from strategy selection because it was used in earlier research.
- The holdout from May 1, 2026 onward remains sealed.
- The holdout may be evaluated once, only after all pass criteria are met and at least 12 months of holdout data are available.

## 2. Data and markets

Source: MetaQuotes-Demo daily bars, ending before October 1, 2022. Freeze the data to CSV files and record SHA-256 hashes and metadata before testing. The broker's daily candle boundaries and the bars' bid/mid price basis have not been independently verified. Preserve the source identification and record these limitations with the frozen-data metadata; do not claim either has been verified.

Fixed basket:

- EURUSD
- GBPUSD
- USDJPY
- AUDUSD
- USDCAD
- XAUUSD
- XAGUSD
- US500

Development period: January 1, 2006–December 31, 2014.

Validation period: January 1, 2015–September 30, 2022.

Each market becomes eligible at the close of its 120th available daily bar; a valid signal at that close is first executable at the next available open. Unequal market start dates will be documented. Use only the frozen CSV OHLC values. ATR(20) is the simple mean of the latest 20 true ranges, where true range is the maximum of high minus low, absolute high minus previous close, and absolute low minus previous close. For the first bar, when no previous close exists, true range is high minus low.

Daily candle boundaries and bid/mid price basis remain unverified; no inference about their exact timing or price basis is permitted.

## 3. Strategy

At each daily close:

- Long (+1) when close is above SMA(100).
- Short (-1) when close is below SMA(100).
- When close equals SMA(100), retain the existing direction; if no direction has yet been established, wait for the first close above or below SMA(100).
- Hold the signed position until the signal changes.
- A signal calculated at a close takes effect at the next available daily open. A change at that open closes the prior position and opens the new position at the same observed open.
- Start the measured strategy flat. Do not open or carry a position from before the development period. For each market, open the initial strategy position at the next available open within the development period after its first eligible signal close. No pre-development position or related opening costs or financing are included.
- A position held from open $O_t$ to the next available open $O_{t+1}$ earns simple open-to-open return $(O_{t+1}/O_t)-1$. Gross P&L for that interval is signed weight times the fixed equity base times that return. Short returns use the same formula with negative signed weight.
- Calculate the signal, SMA, ATR, and sizing inputs using data available at the signal close only. Set absolute weight when the position is opened or flipped, using `weight = 0.0025 / (ATR(20) / close)` with the ATR(20) and close from that signal bar. Apply the signal's direction to obtain signed weight. Keep that weight fixed until the next position change.

Weight is an exposure multiple of fixed equity, not a fraction capped at 1x. No leverage cap is imposed; leverage can exceed 1x. This is a research assumption, not a live-trading recommendation.

P&L is measured in price-return terms against a constant fixed equity base, without compounding or currency conversion. Daily portfolio P&L is the sum of market P&L and costs; markets with no P&L on a date contribute zero. Include every UTC calendar date in each reported period, including dates with zero aggregate portfolio P&L.

At the end of the available sample, do not use a price beyond the frozen data. Use the last observed daily open as the terminal liquidation open solely to close a position that is already open. Apply closing costs once to that existing position and do not open or reverse into a new position there. The final measured open-to-open return ends at this open; no return after it is included.

Do not flatten or reset positions at the development/validation boundary; carry the position and its fixed weight across it. For each market, form returns only between consecutive observed opens while a position is held. Assign each complete open-to-open return and its interval financing exactly once, to the development or validation period containing the ending open timestamp; assign execution costs to the period containing their execution timestamp. If an interval crosses a period boundary, assign the entire interval to the period of its ending open; do not split, duplicate, or omit it. The first measured return interval begins at the initial strategy opening in the development period and ends at the next available open; no return or position-related costs before that opening are included. The final interval ends at the last observed open as specified above; no interval after it is included. The pooled result is the sum of these same period-assigned daily P&L observations, not a separately counted set of intervals. Daily OHLC does not permit an exact split of a boundary-crossing interval; see the review item in Section 9.

Calculate portfolio Sharpe from daily aggregate portfolio net P&L in fixed-equity units, with no rescaling for the number of active markets. Include every UTC calendar date in the period, including zero-P&L dates. Use the sample standard deviation (`ddof=1`) and annualize mean daily net P&L divided by that standard deviation by `sqrt(252)`. If standard deviation is zero or there are insufficient observations to calculate it, Sharpe is undefined and cannot satisfy a PASS criterion.

## 4. Costs

These are research assumptions, not verified historical costs.

| Market | Assumed spread in price units |
|---|---:|
| EURUSD | 0.00010 |
| GBPUSD | 0.00015 |
| USDJPY | 0.012 |
| AUDUSD | 0.00015 |
| USDCAD | 0.00020 |
| XAUUSD | 0.40 |
| XAGUSD | 0.03 |
| US500 | 1.0 |

- For every opening or closing execution, let `s` be the listed spread in price units, `P_exec` the observed execution open, `E` the fixed equity, `|w|` the absolute exposure multiple, and `k` the cost multiplier (1 or 2). The execution cost in fixed-equity P&L units is `-E * |w| * k * (s/2 + s/2) / P_exec`, where the first half-spread is the spread charge and the second is slippage. At a flip, use the outgoing position's absolute exposure for closing spread/slippage costs and the incoming position's absolute exposure for opening spread/slippage costs. This converts the price-unit charge to a fraction of the observed execution price, consistent with percentage-return P&L. No currency conversion is applied, and no unobserved execution price is used.
- Charge commission of 0.7 basis points of notional per completed round trip, split equally: 0.35 basis points at opening and 0.35 basis points at closing. Notional is the absolute exposure multiple times fixed equity. At a flip, close the old position and open the new one at the same observed open, applying closing commission to the old notional and opening commission to the new notional.
- Accrue financing at 1% per year of notional for elapsed calendar days held divided by 365, including weekends. Calculate elapsed days as elapsed UTC seconds between the observed opening and closing timestamps divided by 86,400. Use the position's fixed notional for the interval; attribute the accrued amount at the interval's ending open or terminal liquidation open.
- Current demo-server swap settings are not used as historical financing costs.
- These are research assumptions, not verified historical costs.
- In the 1x scenario, use the listed spread and the cost rates above. In the 2x scenario, double every cost component: the spread charge, slippage, commission, and financing. Do not double gross P&L or position sizing.

## 5. Random baseline

Run 1,000 random-signal replicates using NumPy's `Generator(PCG64)` and fixed seed `20261012`; record the seed and NumPy version with the results. For each market, estimate flip probability only from eligible signal days in the combined development-plus-validation sample: count strategy direction changes between consecutive eligible signal days and divide by the number of such consecutive-day transitions (eligible signal days after the first eligible signal day in that sample). Do not count the first signal as a flip.

For each replicate and market, initialize the random side with equal probability at the first available open within the development period after an eligible signal close. Do not initialize or carry a random position from before the development period. The first random-baseline return interval begins at that initialization open and ends at the next available open. At each subsequent eligible signal close, independently draw a Bernoulli trial with that market's estimated flip probability; if it succeeds, reverse the random side at the next available open, otherwise retain the side. Do not reset the random position at the development/validation boundary. Use the same frozen data, signal-day eligibility, sizing price and ATR timing, execution timing, terminal liquidation, financing, and 1x/2x cost rules as the strategy.

For each cost scenario, calculate each replicate's pooled development-plus-validation net Sharpe using the portfolio Sharpe definition above. The baseline mean is the arithmetic mean of the 1,000 replicate Sharpes; the baseline standard deviation is their sample standard deviation (`ddof=1`). The comparison threshold is baseline mean plus two baseline standard deviations. If any replicate Sharpe is undefined, do not drop or impute it and do not calculate a baseline threshold or make a PASS determination. The baseline comparison is blocked pending a new preregistered rule.

## 6. Pass criteria

All conditions must hold at both 1x and 2x costs:

1. Pooled development-plus-validation net Sharpe is positive and at least two baseline standard deviations above the baseline mean.
2. Net Sharpe is positive in development and validation separately.
3. At least 300 completed position legs are pooled across markets. A completed leg is one opened position, with its fixed direction and weight, that is subsequently closed by a signal flip or terminal liquidation.
4. No single market contributes more than 40% of positive net P&L contribution. For each market, take its positive net P&L contribution (zero if its net P&L is nonpositive), and divide by the sum of positive net P&L contributions across all markets. The largest resulting share must be no more than 40%. If no market has positive net P&L, this criterion fails.

Report maximum drawdown, per-market results, and per-period results. Use the same positive-contribution concentration definition in the report. Calculate maximum drawdown from the cumulative net P&L equity curve starting at the fixed equity base, without compounding.

All conditions must hold separately at both 1x and 2x costs. If fewer than 300 completed position legs are pooled across markets, status is INCONCLUSIVE. Otherwise, if any criterion is not met, including an undefined Sharpe, status is FAIL; an unresolved required calculation prevents PASS and must be reported for review. A PASS only permits consideration of forward demo testing; it does not establish profitability.

## 7. Data-quality safeguards

Before calculating strategy returns:

- Report each market's observed date range, bar count, duplicate timestamps, invalid OHLC values, and missing-bar/gap status.
- Set missing-bar/gap status to UNASSESSABLE unless a versioned, source-documented expected-session calendar is available for the relevant market and dates. Do not infer missing bars from weekends, weekdays, holidays, observed date ranges, or bar counts alone. Without such a calendar, UNASSESSABLE is not PASS and is not evidence of a data-feed error.
- Any expected-session calendar used for a future audit must document its source and version, expected UTC timestamps, broker/instrument schedules, timezone and daylight-saving rules, holidays, and historical schedule changes; record its own SHA-256 hash.
- Report the largest daily price moves for each market, including dates and price changes.
- Flag any daily move exceeding 8 times ATR(20) for data-quality review, but do not automatically remove it.
- Do not inspect strategy performance during data-quality review.
- Preserve the existing frozen CSV files and their hashes. Keep any future expected-session calendar and audit outputs separate from the frozen CSV files and their existing hashes. Record and verify dataset hashes before performance calculations.
- Exclude a bar only when independent evidence supports a data-feed error. Document the supporting evidence, timestamp, reason for exclusion, original dataset hash, and resulting dataset hash. Do not overwrite or silently alter the frozen CSV; any corrected or filtered analysis dataset must be a separately identified derivative with its own hash.

## 8. Trial policy

This is one rule and one parameter set. Any change to the rule, lookback, basket, periods, cost assumptions, or pass criteria requires a new preregistration.

A failed test means no edge was demonstrated; it does not prove that no edge exists. Keep the holdout sealed until every PASS condition is met and at least 12 months of holdout data are available; it may then be evaluated once. A PASS only permits consideration of forward demo testing and does not establish future profitability.

## 9. Unresolved items for review

- A daily open-to-open interval can straddle the development/validation boundary. The registered convention assigns the full interval to the period containing its ending open, and uses the first development-period executable open and last observed open as the sample endpoints. Each interval is assigned once; exact intrainterval splitting is unavailable in daily OHLC. Report this boundary convention when presenting separate-period results.
- Undefined random-baseline Sharpe is a blocking item. The protocol specifies a mean and sample standard deviation across all 1,000 replicate Sharpes, but those statistics are not defined if any replicate Sharpe is undefined. Dropping undefined replicates changes the specified replicate distribution, while imputing a value introduces an unregistered statistical assumption. No scientifically defensible choice among these options is made here; if this case occurs, stop the baseline comparison and preregister a resolution before making a PASS determination.
- The repository and frozen dataset do not provide a defensible historical expected-session calendar. Missing-bar/gap status is therefore UNASSESSABLE for all eight markets unless a qualifying calendar is supplied for the relevant market and dates; this status is neither PASS nor evidence of a data-feed error.
- Daily candle boundaries and bid/mid price basis remain independently unverified, as noted in Section 2.