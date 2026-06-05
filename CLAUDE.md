# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Home Service Hub is a polyglot microservices platform for personal life management. It consists of three backend services, a frontend dashboard, and a shared infrastructure layer (Docker Compose).

## Architecture

```
services/
  inventory-api/       # Java 21 / Spring Boot 4.0 — Gradle multi-module (item-service + common-library)
  accounting-service/  # Python 3.13 / FastAPI — financial transaction tracking
  stock-portfolio-service/  # Python 3.13 / FastAPI — Taiwan stock portfolio
frontend/              # Angular 21 + PrimeNG + Bootstrap 5
infra/                 # Docker Compose configs for observability stack
```

The Angular dev server proxies API calls via `frontend/proxy.conf.js`:
- `/api/items`, `/api/shopping-list` → inventory-api
- `/api/accounting` → accounting-service (path rewritten, strips `/api/accounting` prefix)
- `/api/portfolio` → stock-portfolio-service

All services connect to a shared PostgreSQL instance (separate databases per service). Observability is via OpenTelemetry → OTel Collector → Tempo/Loki/Prometheus/Grafana.

## Common Commands

### Infrastructure
```bash
cp .env.example .env          # first-time setup
docker compose up -d           # start Postgres, RabbitMQ, MinIO, LGTM stack
```

### Inventory API (Java/Spring Boot)
```bash
cd services/inventory-api
./gradlew :item-service:bootRun          # run the service
./gradlew :item-service:test             # run tests (uses H2 in-memory DB)
./gradlew :item-service:test --tests "com.inventory.item.SomeTest"  # single test
```

### Accounting Service (Python/FastAPI)
```bash
cd services/accounting-service
uvicorn app.main:app --port 8000         # run the service
pytest                                    # run all tests
pytest tests/unit/                        # unit tests only
pytest tests/integration/                 # integration tests only
pytest tests/unit/test_foo.py::test_name  # single test
```

### Stock Portfolio Service (Python/FastAPI)
```bash
cd services/stock-portfolio-service
uvicorn app.main:app --port 8001
pytest
pytest tests/unit/
pytest tests/integration/
```

Foreign holdings use the Phase 2 hybrid FX model: live market value is latest native close from `price_history` multiplied by the latest `fx_rates.rate_to_twd`, while cost basis stays frozen at the transaction/dividend row's `fx_rate_to_twd`. LSE `GBp` prices are stored as pence and divided by 100 on the read path before applying the GBP/TWD rate.

### Frontend (Angular)
```bash
cd frontend
npm install
npm start        # dev server (runs set-env.js then ng serve)
npm test         # tests via Vitest
npm run build    # production build
```

### All Services (PM2)
```bash
npx pm2 start ecosystem.config.js   # start all services at once
```

## Key Conventions

- **Environment**: All services read from the root `.env` file. The Gradle build auto-loads `.env` into `bootRun` and test tasks. The frontend's `proxy.conf.js` and `set-env.js` also read from root `.env`.
- **Python service structure**: Both Python services follow identical layout — `app/{main,database,tracing,models/,routers/,schemas/,services/}`.
- **Java structure**: Standard Spring layered architecture under `com.inventory.item` — controller → service → repository, with DTOs via MapStruct, Lombok for boilerplate.
- **Testing**: Java tests use H2 in-memory DB. Python tests are split into `unit/` and `integration/` directories.
- **Observability**: Each service has `tracing.py` or Spring auto-config for OpenTelemetry. Traces flow through the OTel Collector (ports 4317/4318).
