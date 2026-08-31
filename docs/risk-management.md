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
