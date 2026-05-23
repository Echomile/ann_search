"""管理员-用户管理页 e2e 流程。

测试步骤：

1. demo 登录后侧边栏可见 "用户管理" 入口；
2. 进入 ``/admin/users`` 表格中能看到自己（``demo``，``admin`` 角色）；
3. 通过后端 ``/auth/register`` API 注册一个临时用户，再点 "刷新" 看到该行；
4. 切换该用户角色 ``user → admin → user``；
5. 点击 "重置密码" 弹出 Modal，验证 ``temp_password`` 字符串非空且长度合法；
6. 关闭 Modal；
7. 点击 "删除" + Popconfirm 确认，验证行从表格消失。

两种运行方式：

* ``cd backend && uv run pytest ../e2e/test_admin_e2e.py -vv``
* ``cd backend && uv run python ../e2e/test_admin_e2e.py``
"""

from __future__ import annotations

import os
import re
import secrets
import sys
from pathlib import Path

import pytest
import requests
from playwright.sync_api import Page, expect

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "e2e"))

# fmt: off
from conftest import BACKEND_URL, BASE_URL, login_demo  # noqa: E402
# fmt: on

SCREENSHOT_DIR = ROOT / "docs" / "e2e_screenshots"


def _register_temp_user(username: str, password: str = "TempPass1234") -> int:
    """通过后端 API 注册一个临时用户，返回其 ``user_id``。

    Args:
        username: 临时用户名，已含随机后缀避免重名。
        password: 注册时的初始明文密码。

    Returns:
        int: 新建用户的数据库主键。

    Raises:
        AssertionError: 注册接口返回非 201。
    """
    resp = requests.post(
        f"{BACKEND_URL}/api/v1/auth/register",
        json={"username": username, "password": password},
        timeout=10,
    )
    assert resp.status_code == 201, f"注册临时用户失败: {resp.status_code} {resp.text}"
    return int(resp.json()["id"])


def _delete_user_by_api(token: str, user_id: int) -> None:
    """兜底通过后端 API 删除用户（清理用，失败不抛错）。"""
    try:
        requests.delete(
            f"{BACKEND_URL}/api/v1/admin/users/{user_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[cleanup] 删除用户 {user_id} 失败: {exc}")


def test_admin_users_flow(page: Page) -> None:
    """完整跑一遍 admin 用户管理 CRUD 流程。"""
    from conftest import backend_login_token

    login_demo(page)

    expect(page.get_by_role("menuitem", name="用户管理")).to_be_visible()

    page.get_by_role("menuitem", name="用户管理").click()
    page.wait_for_url(f"{BASE_URL}/admin/users", timeout=10_000)
    page.wait_for_selector("table tbody tr", timeout=10_000)

    self_row = page.locator("table tbody tr").filter(has_text="demo").first
    expect(self_row).to_be_visible()
    expect(self_row).to_contain_text("admin")
    print("[step] /admin/users 加载完成，可见 demo 行")

    tmp_username = f"testuser_e2e_{secrets.token_hex(3)}"
    tmp_user_id = _register_temp_user(tmp_username)
    print(f"[step] 已通过 API 注册临时用户 #{tmp_user_id} {tmp_username}")

    admin_token = backend_login_token()
    try:
        page.get_by_role("button", name=re.compile(r"刷\s*新")).first.click()
        page.wait_for_timeout(800)

        row = page.locator("table tbody tr").filter(has_text=tmp_username).first
        expect(row).to_be_visible(timeout=10_000)
        expect(row).to_contain_text("user")
        print("[step] 临时用户行可见，角色 user")

        row.locator(".ant-select-selector").first.click()
        page.wait_for_timeout(400)
        page.locator(".ant-select-dropdown:visible .ant-select-item-option").filter(
            has_text=re.compile(r"^admin$")
        ).first.click()
        page.wait_for_timeout(800)
        expect(row).to_contain_text("admin")
        print("[step] 临时用户角色切换为 admin")

        row.locator(".ant-select-selector").first.click()
        page.wait_for_timeout(400)
        page.locator(".ant-select-dropdown:visible .ant-select-item-option").filter(
            has_text=re.compile(r"^user$")
        ).first.click()
        page.wait_for_timeout(800)
        expect(row).to_contain_text("user")
        print("[step] 临时用户角色切换回 user")

        row.get_by_role("button", name=re.compile(r"重置密码")).click()
        expect(page.get_by_text("一次性临时密码")).to_be_visible(timeout=5_000)
        modal = page.locator(".ant-modal-content").last
        code = modal.locator("code").first
        pwd = (code.text_content() or "").strip()
        assert len(pwd) >= 8, f"temp_password 应至少 8 字符，实际 {pwd!r}"
        print(f"[step] 重置密码 Modal 显示 temp_password={pwd[:4]}***（长度 {len(pwd)}）")

        if os.environ.get("E2E_SCREENSHOT", "1") not in {"0", "false", "no"}:
            try:
                SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
                shot = SCREENSHOT_DIR / "12_admin_users.png"
                page.screenshot(path=str(shot), full_page=True)
                print(f"[shot] {shot.relative_to(ROOT)}")
            except Exception as exc:  # noqa: BLE001
                print(f"[shot] 截图失败（忽略）: {exc}")

        modal.get_by_role("button", name=re.compile(r"我已记下")).click()
        page.wait_for_timeout(500)

        row.get_by_role("button", name=re.compile(r"^删\s*除$")).click()
        page.locator(".ant-popover:visible").get_by_role(
            "button", name=re.compile(r"确\s*定")
        ).click()
        page.wait_for_timeout(1500)

        gone = page.locator("table tbody tr").filter(has_text=tmp_username)
        expect(gone).to_have_count(0)
        print("[done] 临时用户已被删除，表格行消失")
    finally:
        _delete_user_by_api(admin_token, tmp_user_id)


if __name__ == "__main__":
    sys.exit(pytest.main(["-vv", "--tb=short", "-s", __file__]))
