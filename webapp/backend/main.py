"""Trial search web app.

Run from the repo root:  make web   (or: uv run fastapi dev webapp/backend/main.py)
Dashboard at http://127.0.0.1:8000/, API docs at /docs.
"""

from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from webapp.backend import config
from webapp.backend.api.routes import router
from webapp.backend.sources import build_sources
from webapp.backend.agents.llm import make_criteria_llm
from webapp.backend.terminology.rxnorm import RxNormClient
from webapp.backend.terminology.umls import UmlsClient
from webapp.backend.warehouse.databricks import DatabricksClient

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One HTTP client for the app's lifetime, so connections are reused across requests
    async with httpx.AsyncClient(timeout=config.HTTP_TIMEOUT_SECONDS,
                                 headers={"accept": "application/json"}) as client:
        app.state.sources = build_sources(client)
        app.state.umls = UmlsClient(client, config.UMLS_API_KEY)
        app.state.rxnorm = RxNormClient(client)
        app.state.criteria_llm_factory = make_criteria_llm
        app.state.warehouse = DatabricksClient(client, config.DATABRICKS_HOST, config.DATABRICKS_TOKEN,
                                               config.DATABRICKS_WAREHOUSE_ID, config.DATABRICKS_CATALOG,
                                               config.DATABRICKS_SCHEMA, config.DATABRICKS_TIMEOUT_SECONDS)
        yield


app = FastAPI(title="Trial Atlas", lifespan=lifespan)
app.include_router(router, prefix="/api")


@app.middleware("http")
async def revalidate_frontend(request: Request, call_next):
    # Without this, browsers may keep running an old script against a newer page after a change;
    # no-cache makes them check the ETag on every load (a cheap 304 when nothing changed)
    response = await call_next(request)
    if not request.url.path.startswith("/api"):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


@app.get("/health", include_in_schema=False)
def health() -> dict:
    return {"status": "ok"}


# Clean page URLs; the pages read the condition / trial ID from the URL themselves
@app.get("/search", include_in_schema=False)
def search_page() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "search.html")


@app.get("/trials/{trial_id}", include_in_schema=False)
def trial_page(trial_id: str) -> FileResponse:
    return FileResponse(FRONTEND_DIR / "trial.html")


@app.get("/trials/{trial_id}/eligibility", include_in_schema=False)
def eligibility_page(trial_id: str) -> FileResponse:
    return FileResponse(FRONTEND_DIR / "eligibility.html")


@app.get("/data-schema", include_in_schema=False)
def data_schema_page() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "data-schema.html")


# Mounted last so /api and /docs take precedence; html=True serves index.html at /
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
