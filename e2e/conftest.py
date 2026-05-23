"""e2e Playwright 测试共享 fixture 与 helper。

向 ``pytest`` 提供：

* :data:`BASE_URL` / :data:`BACKEND_URL` / :data:`USERNAME` / :data:`PASSWORD`
  四个常量，统一后端与前端入口；
* ``playwright_browser`` —— session 级 Chromium 浏览器（默认 ``channel="chrome"``，
  可通过 ``E2E_HEADED=1`` 切到有头模式调试）；
* ``browser_context`` / ``page`` —— function 级浏览器上下文与页面，统一 1440x1080
  视口避免布局抖动；
* :func:`login_demo` —— 直接复用的登录函数（既可在 fixture 内调用，也可在
  ``standalone`` 脚本中复用）。

约定：
    所有测试既支持 ``cd backend && uv run pytest ../e2e/`` 一并运行，也支持
    ``cd backend && uv run python ../e2e/test_xxx_e2e.py`` 单文件执行。单文件
    运行时各 ``test_*`` 文件会在 ``__main__`` 中调用 :func:`pytest.main` 复用
    本 ``conftest`` 提供的 fixture，行为与 pytest 直接发现一致。
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

BASE_URL = os.environ.get("E2E_FRONTEND_URL", "http://localhost:5173")
BACKEND_URL = os.environ.get("E2E_BACKEND_URL", "http://localhost:8000")
USERNAME = os.environ.get("E2E_USERNAME", "demo")
PASSWORD = os.environ.get("E2E_PASSWORD", "demo1234")

VIEWPORT = {"width": 1440, "height": 1080}


def _headless() -> bool:
    """根据环境变量 ``E2E_HEADED`` 决定是否以有头模式启动浏览器。

    Returns:
        bool: 默认 ``True``（headless）；``E2E_HEADED`` 设为 ``1`` / ``true`` /
        ``yes`` 时返回 ``False``，便于本地肉眼调试。
    """
    flag = os.environ.get("E2E_HEADED", "").lower()
    return flag not in {"1", "true", "yes"}


@pytest.fixture(scope="session")
def playwright_browser() -> Iterator[Browser]:
    """启动 session 级 Chromium（系统 Chrome 通道）浏览器。

    Yields:
        Browser: Playwright Chromium 实例，session 结束时统一关闭。
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            headless=_headless(),
            args=["--no-sandbox"],
        )
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture
def browser_context(playwright_browser: Browser) -> Iterator[BrowserContext]:
    """提供 function 级浏览器上下文，统一视口 1440x1080。

    Yields:
        BrowserContext: 每个测试函数独立的上下文，结束后自动关闭，避免会话残留。
    """
    ctx = playwright_browser.new_context(viewport=VIEWPORT)
    try:
        yield ctx
    finally:
        ctx.close()


@pytest.fixture
def page(browser_context: BrowserContext) -> Iterator[Page]:
    """从 ``browser_context`` 派生 function 级 ``Page``。

    Yields:
        Page: 新建空白页，结束时自动关闭。
    """
    page = browser_context.new_page()
    try:
        yield page
    finally:
        page.close()


def login_demo(page: Page) -> None:
    """登录 demo 账号并等待跳转到 ``/datasets``。

    Args:
        page: Playwright 页面对象。
    """
    page.goto(f"{BASE_URL}/login")
    page.get_by_placeholder("用户名").fill(USERNAME)
    page.get_by_placeholder("密码").fill(PASSWORD)
    page.get_by_role("button", name="登 录").click()
    page.wait_for_url(f"{BASE_URL}/datasets", timeout=15_000)


def backend_login_token(
    username: str = USERNAME,
    password: str = PASSWORD,
    *,
    backend_url: str = BACKEND_URL,
) -> str:
    """直接调后端 ``/auth/login`` 拿 access_token。

    用于在 e2e 测试中跳过 UI、直接构造数据（例如注册临时用户、提前查询
    数据集列表）。

    Args:
        username: 用户名，默认 demo。
        password: 明文密码，默认 demo1234。
        backend_url: 后端 base URL，默认 ``http://localhost:8000``。

    Returns:
        str: JWT access_token。
    """
    import requests

    resp = requests.post(
        f"{backend_url}/api/v1/auth/login",
        data={"username": username, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]
