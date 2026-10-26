import json

import httpx
import pytest

from webapp.backend.warehouse import databricks
from webapp.backend.warehouse.databricks import DatabricksClient, WarehouseError, WarehouseNotConfigured, normalize_host

pytestmark = pytest.mark.anyio


def succeeded(rows, columns=("member_count",), **extra):
    return {"statement_id": "s1", "status": {"state": "SUCCEEDED"},
            "manifest": {"schema": {"columns": [{"name": c} for c in columns]}, "truncated": False},
            "result": {"data_array": rows, **extra}}


def warehouse(handler, **kw):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    args = {"host": "adb-1.azuredatabricks.net", "token": "dapi-secret", "warehouse_id": "wh1", **kw}
    return http, DatabricksClient(http, **args)


def test_host_gets_https_and_loses_the_trailing_slash():
    assert normalize_host("adb-1.azuredatabricks.net/") == "https://adb-1.azuredatabricks.net"
    assert normalize_host("https://x.cloud.databricks.com") == "https://x.cloud.databricks.com"
    assert normalize_host("") is None


async def test_a_statement_that_finishes_right_away():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=succeeded([["42"]]))

    http, wh = warehouse(handler, catalog="main", schema="claims")
    async with http:
        result = await wh.query("SELECT 1", row_limit=10)
    assert result["columns"] == ["member_count"] and result["rows"] == [["42"]] and result["statement_id"] == "s1"
    request = seen[0]
    body = json.loads(request.content)
    assert str(request.url) == "https://adb-1.azuredatabricks.net/api/2.0/sql/statements"
    assert request.headers["Authorization"] == "Bearer dapi-secret"
    assert body["warehouse_id"] == "wh1" and body["statement"] == "SELECT 1" and body["row_limit"] == 10
    assert body["catalog"] == "main" and body["schema"] == "claims" and body["disposition"] == "INLINE"


async def test_a_running_statement_is_polled_until_it_succeeds(monkeypatch):
    monkeypatch.setattr(databricks, "POLL_SECONDS", 0)
    states = iter([{"statement_id": "s1", "status": {"state": "PENDING"}},
                   {"statement_id": "s1", "status": {"state": "RUNNING"}},
                   succeeded([["7"]])])
    methods = []

    def handler(request):
        methods.append((request.method, request.url.path))
        return httpx.Response(200, json=next(states))

    http, wh = warehouse(handler)
    async with http:
        assert (await wh.query("SELECT 1"))["rows"] == [["7"]]
    assert methods == [("POST", "/api/2.0/sql/statements"), ("GET", "/api/2.0/sql/statements/s1"),
                       ("GET", "/api/2.0/sql/statements/s1")]


async def test_a_slow_statement_is_cancelled(monkeypatch):
    monkeypatch.setattr(databricks, "POLL_SECONDS", 0)
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={"statement_id": "s1", "status": {"state": "RUNNING"}})

    http, wh = warehouse(handler, timeout_seconds=0)
    async with http:
        with pytest.raises(WarehouseError, match="cancelled"):
            await wh.query("SELECT 1")
    assert paths[-1] == "/api/2.0/sql/statements/s1/cancel"


async def test_a_failed_statement_reports_the_databricks_message():
    failed = {"statement_id": "s1", "status": {"state": "FAILED", "error": {
        "message": "[TABLE_OR_VIEW_NOT_FOUND] The table or view `rx_claims` cannot be found."}}}
    http, wh = warehouse(lambda r: httpx.Response(200, json=failed))
    async with http:
        with pytest.raises(WarehouseError, match="TABLE_OR_VIEW_NOT_FOUND"):
            await wh.query("SELECT 1")


async def test_a_rejected_token():
    http, wh = warehouse(lambda r: httpx.Response(403, json={"message": "Invalid access token"}))
    async with http:
        with pytest.raises(WarehouseError, match="rejected the token"):
            await wh.query("SELECT 1")


async def test_results_in_several_chunks_are_followed_up_to_the_limit():
    def handler(request):
        if request.url.path.endswith("/chunks/1"):
            return httpx.Response(200, json={"data_array": [["3"], ["4"]], "next_chunk_internal_link": "/x/chunks/2"})
        return httpx.Response(200, json=succeeded([["1"], ["2"]], columns=("member_id",),
                                                  next_chunk_internal_link="/api/2.0/sql/statements/s1/result/chunks/1"))

    http, wh = warehouse(handler)
    async with http:
        result = await wh.query("SELECT 1", row_limit=4)
    assert result["rows"] == [["1"], ["2"], ["3"], ["4"]]  # chunk 2 isn't fetched: the limit is reached


async def test_missing_settings_are_named():
    http, wh = warehouse(lambda r: httpx.Response(200), token=None, warehouse_id=None)
    async with http:
        assert not wh.configured
        with pytest.raises(WarehouseNotConfigured, match="DATABRICKS_TOKEN, DATABRICKS_WAREHOUSE_ID or DATABRICKS_HTTP_PATH"):
            await wh.query("SELECT 1")
