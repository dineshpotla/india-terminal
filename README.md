# India Market Terminal

FastAPI-based market dashboard for Indian equities, indices, news, and watchlists.

## Local run

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/uvicorn app.server:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000`.

## Render deployment

This repo is configured for Render with [`render.yaml`](render.yaml) (Blueprint). [`.python-version`](.python-version) pins Python for Render’s native runtime (override with `PYTHON_VERSION` in the dashboard if needed).

1. Push the project to GitHub.
2. In Render: **New → Blueprint** → connect the repo → apply.
3. Monorepo: set the service **root directory** to `india-terminal` (or the folder containing `render.yaml`).
4. The blueprint provisions **Web (Oregon)** + **Postgres 16 (Oregon)** and wires `DATABASE_URL`.
5. Build / start / health:
   - Build: upgraded `pip` + `setuptools` + `wheel`, then `pip install --no-cache-dir -r requirements.txt`
   - Start: `uvicorn` with `--proxy-headers` and `--forwarded-allow-ips='*'` (TLS termination–friendly), `--timeout-keep-alive 75`
   - Health: `GET /health`
6. Logs: `PYTHONUNBUFFERED=1` is set so stdout/stderr appear immediately in the Render log stream.

Optional env vars (set in the service **Environment** tab): `NV_API_KEY`, `TWELVE_DATA_API_KEY`, `WATCHLIST_SEED_SYMBOLS`, `WATCHLIST_DB_PATH` (SQLite only; Postgres uses `DATABASE_URL`).

Optional LLM tuning vars: `NV_FAST_MODEL`, `NV_BATCH_THRESHOLD`, `NV_BATCH_SIZE`, `NV_BATCH_MAX_TOKENS`.

## Watchlist persistence

The app stores the shared watchlist in a database when `DATABASE_URL` is set.

- The current `render.yaml` provisions a Render Postgres database for zero-setup deploys.
- For a permanent watchlist, point `DATABASE_URL` at a non-expiring Postgres database.
  Examples: a paid Render Postgres instance, or any external hosted Postgres.
- Free Render Postgres expires after 30 days, so it is not a permanent store.
- You can also set `WATCHLIST_SEED_SYMBOLS=RELIANCE,TCS,INFY` to repopulate a default watchlist if the database is ever replaced or reset.

## Free Render idle workaround

Render free web services still spin down after roughly 15 minutes without inbound traffic. WebSocket messages now count, but if your laptop sleeps there is no client traffic, so the service can still go cold.

- This repo includes [`.github/workflows/render-keepalive.yml`](.github/workflows/render-keepalive.yml), which pings the deployed app every 5 minutes from GitHub Actions.
- By default it hits `https://india-market-terminal.onrender.com/health`.
- To reuse the workflow for a different deploy URL, add a GitHub repository variable named `RENDER_KEEPALIVE_URL`.
- GitHub cron is best-effort, not a hard uptime SLA, but it is the practical workaround for free Render without upgrading the service plan.
