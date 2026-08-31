# TradeMind AI

TradeMind AI is a production-oriented Forex platform foundation designed to bring deterministic risk controls, monitoring, and a modular AI-assisted workflow to market analysis and execution planning.

## What TradeMind AI is

TradeMind AI is being built as a backend-first system that separates:

- market data ingestion
- technical analysis and indicator calculation
- strategy signal generation
- deterministic execution gating
- paper/demo execution flows
- future broker integration

The architecture intentionally keeps the LLM and AI components advisory rather than execution-authoritative. Deterministic Python code remains responsible for all risky operational decisions.

## Architecture

The current architecture follows this pipeline:

Market Data -> Feature/Indicator Engine -> Strategy Engine -> Signal -> News/Correlation/Risk Checks -> Deterministic Execution Gate -> Paper/Demo Execution -> Future Broker Adapter

This repository currently contains the initial backend foundation only. It includes safe configuration, a structured logging setup, FastAPI health routes, a minimal LangGraph workflow, and execution safety gates.

## Current scope

The current implementation includes:

- typed application settings with safe defaults
- structured logging with environment-aware output
- FastAPI application scaffold and health endpoint
- minimal LangGraph workflow skeleton
- execution safety gate that denies live trading unless explicitly enabled
- tests for configuration, health, and workflow compilation
- Docker and CI scaffolding

## Safety model

This project is designed for real-money Forex systems, but live execution is disabled by default.

The system must never allow live broker orders unless all required conditions are satisfied:

- `TRADING_MODE=live`
- `LIVE_TRADING_ENABLED=true`

No broker execution code, live order logic, or fake profitability claims are included in this initial foundation. This project is focused on measurement, safety, and engineering discipline.

## Local setup

1. Create a virtual environment:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy environment variables:
   ```bash
   copy .env.example .env
   ```
4. Adjust values as needed for your local environment.

## Environment variables

Key configuration values are defined in `.env.example` and include:

- `APP_NAME`
- `ENVIRONMENT`
- `LOG_LEVEL`
- `TRADING_MODE`
- `LIVE_TRADING_ENABLED`
- `RISK_PER_TRADE`
- `MAX_DAILY_DRAWDOWN`
- `MAX_DAILY_PROFIT`
- `MAX_OPEN_POSITIONS`
- `MAX_SPREAD_POINTS`
- `MAX_SLIPPAGE_POINTS`
- `REDIS_URL`
- `DATABASE_URL`

All values are intentionally safe by default and live trading remains disabled.

## How to run tests

```bash
pytest
```

## How to run FastAPI

```bash
uvicorn backend.app.main:app --reload
```

Then open:

```text
http://127.0.0.1:8000/health
```

## Current limitation

Live execution is intentionally disabled by default and is not implemented in this scope. The system is designed to support backtesting, paper/demo evaluation, and future broker integration without executing real orders.
