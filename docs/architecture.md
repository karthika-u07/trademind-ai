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

This repository currently includes the scaffold only:

- typed configuration
- structured logging
- FastAPI health endpoint
- minimal LangGraph workflow shell
- safety gate abstraction
- tests and CI setup

Future work will add actual market data, technical indicators, risk models, and execution adapters without violating the safety model.
