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

This repo is already configured for Render with [`render.yaml`](render.yaml).

1. Push the project to GitHub.
2. In Render, create a new Blueprint and connect the repo.
3. If this app lives inside a larger monorepo, set the Blueprint path or root directory to `india-terminal`.
4. Render will use:
   - Build: `pip install --upgrade pip && pip install -r requirements.txt`
   - Start: `uvicorn app.server:app --host 0.0.0.0 --port $PORT`
   - Health check: `/health`

The repo includes [`.python-version`](.python-version) to keep Render on Python `3.9` instead of inheriting Render's moving default interpreter version.
