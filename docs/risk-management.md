# Risk Management

Risk controls are designed to be deterministic and authoritative. The AI layer may explain, classify, or summarize market conditions, but it does not control live execution authority.

## Required safety controls

The execution engine must enforce all of the following before any order is permitted:

- position sizing
- maximum risk per trade
- stop loss placement
- take profit placement
- daily drawdown limits
- daily profit caps
- maximum open positions
- correlation exposure checks
- spread and slippage limits
- kill switch logic
- execution permission

## Policy model

TradeMind AI is built around the principle that risk policy is a code-level gate, not an option or suggestion. If execution conditions fail policy validation, the system must block or reject order placement.

## Current implementation

This repository includes a deterministic execution permission gate and configuration validation, but it intentionally stops short of live execution. This ensures that the safety mechanism exists before any broker integration is introduced.

## Volume and P&L units

`SymbolRiskMetadata.volume_*` values are trading lots. `tick_value` is the account-currency value of one `tick_size` move for one lot; for the built-in EURUSD metadata, a `0.0001` tick is worth USD 10 per lot. `contract_size` converts lots to underlying units for notional exposure, so `0.1` lots with a `100000` contract size represents `10000` units.

Risk sizing and backtest realized/unrealized P&L use the same monetary formula:

```
(price_difference / tick_size) * tick_value * volume
```

`contract_size` is not multiplied into this formula because `tick_value` already represents the monetary value per tick per lot.
