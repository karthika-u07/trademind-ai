# Pre-registration 03 - Daily Time-Series Momentum

**Date:** 2026-10-12

**Status:** Written before any daily strategy returns, signals, or P&L were computed.

## 1. Excluded data

- October 1, 2022 through April 30, 2026 is excluded from strategy selection because it was used in earlier research.
- The holdout from May 1, 2026 onward remains sealed.
- The holdout may be evaluated once, only after all pass criteria are met and at least 12 months of holdout data are available.

## 2. Data and markets

Source: MetaQuotes-Demo daily bars, ending before October 1, 2022. Freeze the data to CSV files and record SHA-256 hashes and metadata before testing.

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

Each market becomes eligible after 120 daily bars are available. Unequal market start dates will be documented. The portfolio P&L is the sum across active markets; Sharpe is calculated on that sum, with no rescaling for the number of active markets.

Daily close timing and bid/mid price basis remain unverified.

## 3. Strategy

At each daily close:

- Long (+1) when close is above SMA(100).
- Short (-1) when close is below SMA(100).
- Hold until the signal changes.
- A signal calculated at day t's close takes effect at day t+1's open.
- P&L is measured open-to-open.

ATR(20) is the simple mean of true range.

Position weight is set when opened or flipped:

`weight = 0.0025 / (ATR(20) / price)`

Weights remain fixed until the next position change. P&L is measured against a constant equity base, without compounding or currency conversion.

Portfolio Sharpe is calculated as mean daily portfolio P&L divided by its standard deviation, multiplied by the square root of 252.

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

- Spread is charged on each execution.
- Slippage is assumed to be 0.5 times the specified spread per execution.
- Commission is assumed to be 0.7 basis points of notional per round trip.
- Financing is assumed to be 1% per year of notional, accrued by calendar days held.
- Current demo-server swap settings are not used as historical financing costs.
- The 2x scenario doubles spread, slippage, commission, and financing.

## 5. Random baseline

Run 1,000 fixed-seed random-signal replicates. Each market starts with a random side and flips with a probability matching the strategy's observed flip frequency. Apply the same sizing and cost assumptions.

The baseline is the mean and standard deviation of the replicates' pooled portfolio net Sharpe ratios.

## 6. Pass criteria

All conditions must hold at both 1x and 2x costs:

1. Pooled development-plus-validation net Sharpe is positive and at least two baseline standard deviations above the baseline mean.
2. Net Sharpe is positive in development and validation separately.
3. At least 300 completed position legs are pooled across markets.
4. No single market contributes more than 40% of total net profit.

Report maximum drawdown, per-market results, and per-period results.

If fewer than 300 position legs are completed, status is INCONCLUSIVE. Otherwise, if all criteria are not met, status is FAIL. A PASS only makes the strategy eligible for forward demo testing; it does not establish future profitability.

## 7. Data-quality safeguards

Before calculating strategy returns:

- Report each market's date range, bar count, duplicate timestamps, gaps, and invalid OHLC prices.
- Report the largest daily price moves for each market.
- Flag any daily move exceeding 8 times ATR(20) for data-quality review.
- Do not inspect strategy performance during data-quality review.
- Record dataset hashes before running the strategy.

## 8. Trial policy

This is one rule and one parameter set. Any change to the rule, lookback, basket, periods, cost assumptions, or pass criteria requires a new preregistration.

A failed test means no edge was demonstrated; it does not prove that no edge exists.