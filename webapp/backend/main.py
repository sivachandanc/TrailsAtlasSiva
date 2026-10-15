"""Trial search web app.

Run from the repo root:  make web   (or: uv run fastapi dev webapp/backend/main.py)
Dashboard at http://127.0.0.1:8000/, API docs at /docs.
"""

from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from webapp.backend import config
from webapp.backend.api.routes import router
from webapp.backend.sources import build_sources

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One HTTP client for the app's lifetime, so connections are reused across requests
    async with httpx.AsyncClient(timeout=config.HTTP_TIMEOUT_SECONDS,
                                 headers={"accept": "application/json"}) as client:
        app.state.sources = build_sources(client)
        yield


app = FastAPI(title="Trial Search", lifespan=lifespan)
app.include_router(router, prefix="/api")


@app.get("/health", include_in_schema=False)
def health() -> dict:
    return {"status": "ok"}


# Mounted last so /api and /docs take precedence; html=True serves index.html at /
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
