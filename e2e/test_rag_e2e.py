"""RAG 自然语言查询 e2e 流程。

走完一次最简的 RAG 对话：

1. 登录 demo；
2. 进入 ``/rag``，等待 ``RagChatPage`` 默认选中第一个 ready 数据集；
3. 输入 "找肝细胞" 提问；
4. 点击 "发送"，等待 AI 回复气泡里的 ``hits`` 表格出现；
5. 验证表格至少 1 行，且 ``answer`` 文本非空。

运行：

* ``cd backend && uv run pytest ../e2e/test_rag_e2e.py -vv``
* ``cd backend && uv run python ../e2e/test_rag_e2e.py``
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "e2e"))

# fmt: off
from conftest import BASE_URL, login_demo  # noqa: E402
# fmt: on


def test_rag_query_flow(page: Page) -> None:
    """从输入框输入 → 发送 → 等回复 → 验证 hits 表格行数 ≥ 1。"""
    login_demo(page)

    page.goto(f"{BASE_URL}/rag")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(2_000)

    ds_selection = page.locator(".ant-form-item .ant-select-selection-item").first
    expect(ds_selection).to_be_visible(timeout=10_000)
    selected_text = (ds_selection.text_content() or "").strip()
    assert selected_text, "RagChatPage 未自动选中 ready 数据集"
    print(f"[setup] 默认选中数据集: {selected_text}")

    textarea = page.get_by_placeholder("输入自然语言查询，Enter 发送，Shift+Enter 换行")
    expect(textarea).to_be_visible()
    textarea.fill("找肝细胞")
    page.wait_for_timeout(300)

    page.get_by_role("button", name=re.compile(r"发\s*送")).click()
    print("[step] 已点击 发送 按钮")

    last_item = page.locator(".ant-list-item").last
    ai_table = last_item.locator(".ant-table")
    expect(ai_table).to_be_visible(timeout=60_000)

    rows = ai_table.locator(".ant-table-tbody tr.ant-table-row")
    page.wait_for_function(
        """() => {
            const items = document.querySelectorAll('.ant-list-item');
            if (!items.length) return false;
            const last = items[items.length - 1];
            const tbodyRows = last.querySelectorAll('.ant-table-tbody tr.ant-table-row');
            return tbodyRows.length >= 1;
        }""",
        timeout=30_000,
    )
    n_rows = rows.count()
    assert n_rows >= 1, f"hits 表格应至少 1 行，实际 {n_rows}"
    print(f"[step] hits 表格行数 = {n_rows}")

    answer_text = (last_item.text_content() or "").strip()
    assert "找肝细胞" in answer_text, "未在最后一条对话气泡里看到用户问题"
    assert any(kw in answer_text for kw in ("为您找到", "排名第一", "相似")), (
        f"answer 文本里未匹配到 RAG 模板关键词：{answer_text[:200]}"
    )
    print(f"[done] RAG 回复片段：{answer_text[:120]}")


if __name__ == "__main__":
    sys.exit(pytest.main(["-vv", "--tb=short", "-s", __file__]))
