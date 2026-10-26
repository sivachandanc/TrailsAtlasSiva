"""Runs SQL on a Databricks SQL warehouse through the SQL Statement Execution API (REST, no driver).

Only the step 3 query runs here, rebuilt on the server from checked rules: the browser never sends
SQL. Results go back to the caller only; nothing is stored, cached or logged.
"""

import asyncio
import time

import httpx

STATEMENTS = "/api/2.0/sql/statements"
WAIT = "30s"  # how long the first request waits before returning a statement that's still running
POLL_SECONDS = 1.0
DONE = {"SUCCEEDED", "FAILED", "CANCELED", "CLOSED"}


class WarehouseNotConfigured(Exception):
    """DATABRICKS_HOST, DATABRICKS_TOKEN or the warehouse isn't set."""


class WarehouseError(Exception):
    """Databricks couldn't be reached, rejected the token, or the statement failed."""


def normalize_host(host: str | None) -> str | None:
    if not host:
        return None
    host = host.strip().rstrip("/")
    return host if host.startswith(("https://", "http://")) else f"https://{host}"


class DatabricksClient:
    def __init__(self, client: httpx.AsyncClient, host: str | None, token: str | None, warehouse_id: str | None,
                 catalog: str | None = None, schema: str | None = None, timeout_seconds: float = 120):
        self.client = client
        self.host = normalize_host(host)
        self.token = token
        self.warehouse_id = warehouse_id
        self.catalog = catalog
        self.schema = schema
        self.timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self.host and self.token and self.warehouse_id)

    def missing(self) -> list[str]:
        return [name for name, value in (("DATABRICKS_HOST", self.host), ("DATABRICKS_TOKEN", self.token),
                                         ("DATABRICKS_WAREHOUSE_ID or DATABRICKS_HTTP_PATH", self.warehouse_id))
                if not value]

    async def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        try:
            response = await self.client.request(method, f"{self.host}{path}", json=body,
                                                 headers={"Authorization": f"Bearer {self.token}"})
        except httpx.HTTPError as exc:
            raise WarehouseError(f"Databricks couldn't be reached: {exc.__class__.__name__}") from exc
        if response.status_code in (401, 403):
            raise WarehouseError("Databricks rejected the token. Check DATABRICKS_TOKEN in the .env file, "
                                 "and that it can use this warehouse.")
        if response.status_code == 404:
            raise WarehouseError("Databricks couldn't find the SQL Statement API at this host. Check DATABRICKS_HOST.")
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code >= 400:
            raise WarehouseError(f"Databricks returned an error (HTTP {response.status_code}): "
                                 f"{data.get('message') or data.get('error_code') or 'no details'}")
        return data

    async def query(self, statement: str, row_limit: int | None = None) -> dict:
        """Run one statement and wait for it. Returns {columns, rows, truncated, statement_id, elapsed_ms};
        every value comes back as a string (or None), as Databricks sends it."""
        if not self.configured:
            raise WarehouseNotConfigured("Running the query needs a Databricks SQL warehouse. Add "
                                         + ", ".join(self.missing()) + " to the .env file and restart the app.")
        started = time.monotonic()
        body = {"warehouse_id": self.warehouse_id, "statement": statement, "wait_timeout": WAIT,
                "on_wait_timeout": "CONTINUE", "format": "JSON_ARRAY", "disposition": "INLINE"}
        if row_limit is not None:
            body["row_limit"] = row_limit
        if self.catalog:
            body["catalog"] = self.catalog
        if self.schema:
            body["schema"] = self.schema
        data = await self._call("POST", STATEMENTS, body)
        statement_id = data.get("statement_id")

        # Still running after the first wait (e.g. the warehouse is starting): poll until done or timed out
        while (data.get("status") or {}).get("state") not in DONE:
            if time.monotonic() - started > self.timeout_seconds:
                await self._call("POST", f"{STATEMENTS}/{statement_id}/cancel")
                raise WarehouseError(f"The query took longer than {int(self.timeout_seconds)} seconds, so it was "
                                     "cancelled. The warehouse may be starting up; try again in a minute.")
            await asyncio.sleep(POLL_SECONDS)
            data = await self._call("GET", f"{STATEMENTS}/{statement_id}")

        status = data.get("status") or {}
        if status.get("state") != "SUCCEEDED":
            error = status.get("error") or {}
            raise WarehouseError(f"The query {status.get('state', 'failed').lower()} on Databricks: "
                                 f"{error.get('message') or error.get('error_code') or 'no details'}")

        manifest = data.get("manifest") or {}
        columns = [c["name"] for c in (manifest.get("schema") or {}).get("columns", [])]
        result = data.get("result") or {}
        rows = list(result.get("data_array") or [])
        # Large results arrive in chunks; follow them until the row limit is reached
        while result.get("next_chunk_internal_link") and (row_limit is None or len(rows) < row_limit):
            result = await self._call("GET", result["next_chunk_internal_link"])
            rows += result.get("data_array") or []
        return {"columns": columns, "rows": rows, "truncated": bool(manifest.get("truncated")),
                "statement_id": statement_id, "elapsed_ms": int((time.monotonic() - started) * 1000)}
