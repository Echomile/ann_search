"""健康检查与 HTTP 压缩中间件测试。"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_ok(client: AsyncClient) -> None:
    """``GET /health`` 应返回 ``{"status": "ok"}``。"""
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_health_returns_compressed_when_accepted(client: AsyncClient) -> None:
    """P4 压缩中间件：对大响应（``/openapi.json``）声明 ``Accept-Encoding`` 时应返回 ``Content-Encoding``。

    ``/health`` 响应体过小（< minimum_size=512B），不会触发压缩；改用 OpenAPI JSON
    （通常 > 100 KB）验证 brotli/gzip 中间件确实在编码响应体。
    """
    resp = await client.get(
        "/openapi.json",
        headers={"Accept-Encoding": "br, gzip"},
    )
    assert resp.status_code == 200
    encoding = resp.headers.get("content-encoding", "").lower()
    assert encoding in {"br", "gzip"}, (
        f"期望 content-encoding ∈ {{'br','gzip'}}，实际为 {encoding!r}"
    )
