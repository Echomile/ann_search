"""SearchLog Dashboard e2e 流程。

EvaluationPage 底部的 "检索日志统计" 区聚合当前用户的 :class:`SearchLog`。
评测本身不写 SearchLog，所以脚本会先用后端 API 触发一次真实 search
保证至少存在一条日志，再通过 UI 跑一次微型评测验证整页能交互，
最后断言 "总查询数 > 0"。

步骤：

1. 登录 demo；
2. 通过 page.evaluate 调一次 ``POST /search/by-vector`` 写入 SearchLog；
3. 进入 ``/evaluation``，根据上一步拿到的 ``dataset_id`` / ``index_id``
   填表并点击 "运行评测"；
4. 等评测完成（或最多等 ``EVAL_TIMEOUT`` 秒），允许 ARQ 队列模式异步落盘；
5. 点击 "检索日志统计" 区的 "刷新" 按钮，断言 "总查询数 > 0"。

运行：

* ``cd backend && uv run pytest ../e2e/test_stats_e2e.py -vv``
* ``cd backend && uv run python ../e2e/test_stats_e2e.py``
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "e2e"))

# fmt: off
from conftest import BASE_URL, login_demo  # noqa: E402
# fmt: on

EVAL_TIMEOUT_S = 180


def _seed_search_log(page: Page) -> dict:
    """通过浏览器 fetch 调一次 by-vector 检索，写一条 SearchLog。

    Args:
        page: 已登录的 Playwright 页面。

    Returns:
        dict: ``{"dataset_id", "index_id", "dataset_name", "hits"}``。
    """
    seed = page.evaluate(
        """async () => {
            const t = localStorage.getItem('ann_search_token');
            const headers = {'Content-Type': 'application/json', Authorization: `Bearer ${t}`};
            const dsList = await (await fetch('/api/v1/datasets', {headers})).json();
            const ds = dsList.find(d => d.status === 'ready');
            if (!ds) return null;
            const idxList = await (await fetch(`/api/v1/datasets/${ds.id}/indexes`, {headers})).json();
            const idx = idxList.find(x => x.status === 'ready');
            if (!idx) return null;
            const vec = new Array(ds.vector_dim).fill(0).map(() => Math.random() - 0.5);
            const r = await fetch('/api/v1/search/by-vector', {
                method: 'POST',
                headers,
                body: JSON.stringify({dataset_id: ds.id, index_id: idx.id, vector: vec, top_k: 5})
            });
            const data = await r.json();
            return {
                dataset_id: ds.id,
                index_id: idx.id,
                dataset_name: ds.name,
                hits: (data.hits || []).length,
            };
        }"""
    )
    assert seed, "未找到 ready 数据集 / 索引，无法 seed 检索日志"
    assert seed["hits"] == 5, f"by-vector 检索应返回 5 个结果，实际 {seed}"
    return seed


def _read_total_queries(page: Page) -> int:
    """通过后端 stats API 直接读取当前用户的 ``total_queries``。"""
    return page.evaluate(
        """async () => {
            const t = localStorage.getItem('ann_search_token');
            const r = await fetch('/api/v1/stats/search', {headers: {Authorization: `Bearer ${t}`}});
            if (!r.ok) return -1;
            const data = await r.json();
            return data.total_queries ?? 0;
        }"""
    )


def test_stats_dashboard_flow(page: Page) -> None:
    """评测 + 检索日志统计端到端流程。"""
    login_demo(page)

    before = _read_total_queries(page)
    print(f"[setup] before total_queries = {before}")

    seed = _seed_search_log(page)
    print(f"[seed] dataset_id={seed['dataset_id']} index_id={seed['index_id']} hits={seed['hits']}")

    # 先在 /datasets 选中目标行，让 datasetStore.currentDataset 写入 localStorage，
    # 这样 EvaluationPage 的 useEffect 会自动预填 top_k_list / concurrency_list 等必填字段。
    page.goto(f"{BASE_URL}/datasets")
    page.wait_for_selector("table tbody tr", timeout=10_000)
    page.locator("table tbody tr").filter(has_text=seed["dataset_name"]).first.click()
    page.wait_for_timeout(500)

    page.goto(f"{BASE_URL}/evaluation")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1500)

    ds_form = page.locator(".ant-form-item").filter(
        has=page.locator(".ant-form-item-label label", has_text=re.compile(r"^\s*数据集\s*$"))
    ).first
    ds_form.locator(".ant-select-selector").click()
    page.wait_for_timeout(500)
    page.locator(".ant-select-dropdown:visible .ant-select-item-option").filter(
        has_text=f"#{seed['dataset_id']}"
    ).first.click()
    page.wait_for_timeout(800)
    print("[step] 已选数据集")

    idx_form = page.locator(".ant-form-item").filter(
        has=page.locator(".ant-form-item-label label", has_text=re.compile(r"^\s*索引\s*$"))
    ).first
    idx_form.locator(".ant-select-selector").click()
    page.wait_for_timeout(500)
    page.locator(".ant-select-dropdown:visible .ant-select-item-option").filter(
        has_text=f"#{seed['index_id']}"
    ).first.click()
    page.wait_for_timeout(500)
    print("[step] 已选索引")

    num_form = page.locator(".ant-form-item").filter(
        has=page.locator(".ant-form-item-label label", has_text=re.compile(r"^num_queries$"))
    ).first
    num_form.locator(".ant-input-number-input").fill("5")
    page.wait_for_timeout(300)
    print("[step] num_queries=5")

    page.get_by_role("button", name=re.compile(r"运行评测")).click()
    page.wait_for_timeout(2000)
    print("[step] 已提交评测，等待结果")

    deadline = time.time() + EVAL_TIMEOUT_S
    completed = False
    while time.time() < deadline:
        results = page.evaluate(
            """async () => {
                const t = localStorage.getItem('ann_search_token');
                const r = await fetch('/api/v1/evaluation/results', {headers: {Authorization: `Bearer ${t}`}});
                return r.ok ? await r.json() : [];
            }"""
        )
        if isinstance(results, list) and len(results) > 0:
            completed = True
            print(f"[step] 评测结果已落盘 count={len(results)}")
            break
        time.sleep(2)
    if not completed:
        print(f"[warn] 评测未在 {EVAL_TIMEOUT_S}s 内完成，继续验证 stats 区块")

    stats_card = page.locator(".ant-card").filter(
        has=page.locator(".ant-card-head-title", has_text="检索日志统计")
    ).first
    expect(stats_card).to_be_visible()
    refresh_btn = stats_card.locator(".ant-card-extra").get_by_role(
        "button", name=re.compile(r"刷\s*新")
    ).first
    refresh_btn.click()
    page.wait_for_timeout(1500)

    total_stat = stats_card.locator(".ant-statistic").filter(has_text="总查询数").first
    expect(total_stat).to_be_visible(timeout=5_000)
    value_text = (total_stat.locator(".ant-statistic-content-value").first.text_content() or "").strip()
    cleaned = value_text.replace(",", "").replace(" ", "")
    assert cleaned.isdigit(), f"总查询数文本非整数: {value_text!r}"
    total = int(cleaned)
    print(f"[done] 检索日志统计 总查询数 = {total}")
    assert total > 0, f"总查询数应 > 0，实际 {total}"
    assert total > before, f"总查询数应大于前置值 {before}，实际 {total}"


if __name__ == "__main__":
    sys.exit(pytest.main(["-vv", "--tb=short", "-s", __file__]))
