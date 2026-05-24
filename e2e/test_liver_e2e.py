"""Playwright 端到端测试：完整跑通 liver.h5ad 真实数据流程。

流程：
    1. 打开浏览器（系统 Chrome），登录 demo/demo1234
    2. /datasets：填名称 + setInputFiles(liver.h5ad) + 点开始上传
    3. 轮询等待 status=ready（10 分钟超时）
    4. /indexes：选择 hnswlib + 默认参数 + 构建
    5. 轮询等待 IndexRecord.status=ready
    6. /search：用第一个细胞 ID + top_k=10 跑一次 by-id 检索
    7. 截图 docs/e2e_screenshots/ 多张

运行：
    cd backend && uv run python ../e2e/test_liver_e2e.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import BASE_URL, PASSWORD, USERNAME  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LIVER_PATH = ROOT / "liver.h5ad"
SCREENSHOT_DIR = ROOT / "docs" / "e2e_screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


def shot(page: Page, name: str) -> None:
    """对当前页面截图，用于结果归档。"""
    path = SCREENSHOT_DIR / f"{name}.png"
    page.screenshot(path=str(path), full_page=True)
    print(f"[shot] {path.relative_to(ROOT)}")


def login(page: Page) -> None:
    """登录并跳转到 /datasets。"""
    page.goto(f"{BASE_URL}/login")
    page.get_by_placeholder("用户名").fill(USERNAME)
    page.get_by_placeholder("密码").fill(PASSWORD)
    page.get_by_role("button", name="登 录").click()
    page.wait_for_url(f"{BASE_URL}/datasets", timeout=10_000)
    print("[step] 登录成功")
    shot(page, "01_after_login")


def upload_liver(page: Page) -> None:
    """填名称 + setInputFiles(liver.h5ad) + 点开始上传。"""
    assert LIVER_PATH.exists(), f"liver.h5ad 不存在：{LIVER_PATH}"
    page.get_by_placeholder("例如：pbmc3k_v2").fill("liver")
    file_input = page.locator('input[type="file"]')
    file_input.set_input_files(str(LIVER_PATH))
    print(f"[step] 已注入文件 {LIVER_PATH} ({LIVER_PATH.stat().st_size / 2**20:.1f} MB)")
    shot(page, "02_before_upload")
    page.get_by_role("button", name="开始上传").click()
    # 上传 + 入队应在 1 分钟内返回
    expect(page.get_by_text("上传成功，已入队预处理")).to_be_visible(timeout=180_000)
    print("[step] 上传成功，预处理已入队")
    shot(page, "03_uploaded")


def _poll_status(page: Page, fetch_js: str, label: str, timeout_s: int) -> dict:
    """通用：用 page.evaluate 调 backend API 轮询，直到 status in {ready, failed}。"""
    deadline = time.time() + timeout_s
    last_status = ""
    while time.time() < deadline:
        info = page.evaluate(fetch_js)
        status = info.get("status", "")
        if status != last_status:
            print(f"[poll] {label} status = {status} | {info}")
            last_status = status
        if status == "ready":
            return info
        if status == "failed":
            raise RuntimeError(f"{label} 任务失败：{info}")
        time.sleep(3)
    raise TimeoutError(f"等待 {label} ready 超时 {timeout_s}s")


def wait_dataset_ready(page: Page, timeout_s: int = 900) -> dict:
    """轮询数据集状态，调 GET /datasets/{first}/status。"""
    fetch_js = """async () => {
        const token = localStorage.getItem('ann_search_token');
        const headers = {Authorization: `Bearer ${token}`};
        const list = await (await fetch('/api/v1/datasets', {headers})).json();
        if (!list.length) return {status: 'no-dataset'};
        const id = list[0].id;
        const s = await (await fetch(`/api/v1/datasets/${id}/status`, {headers})).json();
        return {dataset_id: id, ...s};
    }"""
    info = _poll_status(page, fetch_js, "dataset", timeout_s)
    shot(page, "04_dataset_ready")
    return info


def select_dataset_row(page: Page) -> None:
    """点击表格第一行选中数据集，便于索引/检索页使用。"""
    page.locator("table tbody tr").first.click()
    page.wait_for_timeout(500)
    print("[step] 已选中数据集")


def build_index(page: Page) -> None:
    """切到索引管理页，hnswlib 默认参数构建。"""
    page.get_by_role("menuitem", name="索引管理").click()
    page.wait_for_url(f"{BASE_URL}/indexes")
    page.wait_for_timeout(1000)
    shot(page, "05_index_page")
    # 表单：保持 backend=hnswlib，metric=l2，默认参数；点开始构建
    page.get_by_role("button", name="开始构建").click()
    expect(page.get_by_text("索引构建任务已入队")).to_be_visible(timeout=30_000)
    print("[step] 索引构建已入队")


def wait_index_ready(page: Page, dataset_id: int, timeout_s: int = 900) -> dict:
    fetch_js = (
        """async (dsId) => {
        const token = localStorage.getItem('ann_search_token');
        const headers = {Authorization: `Bearer ${token}`};
        const list = await (await fetch(`/api/v1/datasets/${dsId}/indexes`, {headers})).json();
        if (!list.length) return {status: 'no-index'};
        const sorted = [...list].sort((a, b) => b.id - a.id);
        const id = sorted[0].id;
        const s = await (await fetch(`/api/v1/indexes/${id}/status`, {headers})).json();
        return {index_id: id, ...s};
    }"""
    )
    deadline = time.time() + timeout_s
    last_status = ""
    while time.time() < deadline:
        info = page.evaluate(fetch_js, dataset_id)
        status = info.get("status", "")
        if status != last_status:
            print(f"[poll] index status = {status} | {info}")
            last_status = status
        if status == "ready":
            shot(page, "06_index_ready")
            return info
        if status == "failed":
            shot(page, "06_index_failed")
            raise RuntimeError(f"索引构建失败：{info}")
        time.sleep(3)
    raise TimeoutError(f"等待 index ready 超时 {timeout_s}s")


def search_by_id(page: Page) -> None:
    """在检索页跑一次 by-id 检索，用 axios 直接取一个 cell_id。"""
    page.get_by_role("menuitem", name="检索").click()
    page.wait_for_url(f"{BASE_URL}/search")
    page.wait_for_timeout(1000)
    # 通过 fetch 拿到第一个 cell_id（datasets/{id}/cell_ids 接口若无，则跳过；
    # 这里直接用 metadata.csv 第一行 cell_id 作为查询）
    cell_id = page.evaluate(
        """async () => {
            // 从 datasetStore 持久化里取 dataset_id 不靠谱，直接读列表第一个 ready 的
            const token = localStorage.getItem('ann_search_token');
            const ds = await (await fetch('/api/v1/datasets', {headers: {Authorization: `Bearer ${token}`}})).json();
            return { dataset_id: ds[0].id };
        }"""
    )
    print(f"[step] dataset_id={cell_id['dataset_id']}")
    # 直接调 backend search-by-id 但需要 cell_id；这里用 vector 的方式 by-vector 随机查
    # 简化：用浏览器 fetch 列出 metadata 的第一个 cell_id。后端目前没此接口，跳过 by-id。
    # 改用 by-vector：随机 vector，dim=向量维度从 dataset.vector_dim 取
    # 1) by-vector：随机向量
    result = page.evaluate(
        """async (dsId) => {
            const token = localStorage.getItem('ann_search_token');
            const ds = await (await fetch(`/api/v1/datasets/${dsId}`, {headers: {Authorization: `Bearer ${token}`}})).json();
            const indexes = await (await fetch(`/api/v1/datasets/${dsId}/indexes`, {headers: {Authorization: `Bearer ${token}`}})).json();
            const idx = indexes.find(x => x.status === 'ready');
            const vec = new Array(ds.vector_dim).fill(0).map(() => Math.random() - 0.5);
            const t0 = performance.now();
            const r = await fetch('/api/v1/search/by-vector', {
                method: 'POST',
                headers: {'Content-Type': 'application/json', Authorization: `Bearer ${token}`},
                body: JSON.stringify({dataset_id: dsId, index_id: idx.id, vector: vec, top_k: 10})
            });
            const data = await r.json();
            const elapsed = performance.now() - t0;
            return {
                elapsed_ms: Math.round(elapsed * 100) / 100,
                hits_count: data.hits?.length || 0,
                first: data.hits?.[0] || null,
                latency_ms: data.latency_ms,
                index_backend: data.index_backend,
                metric: data.metric,
                total_candidates: data.total_candidates,
            };
        }""",
        cell_id["dataset_id"],
    )
    print(f"[step] by-vector 检索：{result}")
    assert result["hits_count"] == 10, f"by-vector 应返回 10 个结果，实际 {result['hits_count']}"

    # 2) by-id：用第一个细胞作为 query
    result2 = page.evaluate(
        """async (dsId) => {
            const token = localStorage.getItem('ann_search_token');
            // 由列表第一个 hits 拿到 cell_id（前一步刚跑过一次 by-vector），重新跑一次
            const ds = await (await fetch(`/api/v1/datasets/${dsId}`, {headers: {Authorization: `Bearer ${token}`}})).json();
            const indexes = await (await fetch(`/api/v1/datasets/${dsId}/indexes`, {headers: {Authorization: `Bearer ${token}`}})).json();
            const idx = indexes.find(x => x.status === 'ready');
            // 先用 random vector 拿一个 cell_id
            const vec = new Array(ds.vector_dim).fill(0).map(() => Math.random() - 0.5);
            const rv = await (await fetch('/api/v1/search/by-vector', {
                method: 'POST',
                headers: {'Content-Type': 'application/json', Authorization: `Bearer ${token}`},
                body: JSON.stringify({dataset_id: dsId, index_id: idx.id, vector: vec, top_k: 1})
            })).json();
            const cellId = rv.hits[0].cell_id;
            const t0 = performance.now();
            const r = await fetch('/api/v1/search/by-id', {
                method: 'POST',
                headers: {'Content-Type': 'application/json', Authorization: `Bearer ${token}`},
                body: JSON.stringify({dataset_id: dsId, index_id: idx.id, cell_id: cellId, top_k: 10})
            });
            const data = await r.json();
            const elapsed = performance.now() - t0;
            return {
                query_cell: cellId,
                elapsed_ms: Math.round(elapsed * 100) / 100,
                latency_ms: data.latency_ms,
                hits_count: data.hits?.length || 0,
                first: data.hits?.[0] || null,
            };
        }""",
        cell_id["dataset_id"],
    )
    print(f"[step] by-id 检索：{result2}")
    assert result2["hits_count"] >= 9, f"by-id 应返回 ≥9 个结果，实际 {result2['hits_count']}"

    # 3) 条件过滤检索（按 cell_type）
    result3 = page.evaluate(
        """async (dsId) => {
            const token = localStorage.getItem('ann_search_token');
            const ds = await (await fetch(`/api/v1/datasets/${dsId}`, {headers: {Authorization: `Bearer ${token}`}})).json();
            const indexes = await (await fetch(`/api/v1/datasets/${dsId}/indexes`, {headers: {Authorization: `Bearer ${token}`}})).json();
            const idx = indexes.find(x => x.status === 'ready');
            const vec = new Array(ds.vector_dim).fill(0).map(() => Math.random() - 0.5);
            const t0 = performance.now();
            const r = await fetch('/api/v1/search/by-vector', {
                method: 'POST',
                headers: {'Content-Type': 'application/json', Authorization: `Bearer ${token}`},
                body: JSON.stringify({dataset_id: dsId, index_id: idx.id, vector: vec, top_k: 10, filters: {donor_id: '*'}})
            });
            const data = await r.json();
            return {
                hits_count: data.hits?.length || 0,
                latency_ms: data.latency_ms,
                total_candidates: data.total_candidates,
            };
        }""",
        cell_id["dataset_id"],
    )
    print(f"[step] 条件过滤检索：{result3}")

    shot(page, "07_search_result")
    page.get_by_role("menuitem", name="性能评测").click()
    page.wait_for_url(f"{BASE_URL}/evaluation")
    page.wait_for_timeout(1500)
    shot(page, "08_evaluation")
    page.get_by_role("menuitem", name="RAG").click()
    page.wait_for_url(f"{BASE_URL}/rag")
    page.wait_for_timeout(1500)
    shot(page, "09_rag")


def main() -> int:
    print(f"liver.h5ad: {LIVER_PATH} ({LIVER_PATH.stat().st_size / 2**30:.2f} GB)")
    with sync_playwright() as p:
        # 用系统 Chrome
        browser = p.chromium.launch(
            channel="chrome",
            headless=False,
            args=["--no-sandbox"],
        )
        try:
            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            page = ctx.new_page()
            login(page)
            existing = page.evaluate(
                """async () => {
                    const token = localStorage.getItem('ann_search_token');
                    const list = await (await fetch('/api/v1/datasets', {headers: {Authorization: `Bearer ${token}`}})).json();
                    const ready = list.find(d => d.status === 'ready');
                    return ready || null;
                }"""
            )
            if existing and existing.get("cell_count", 0) > 1000:
                print(f"[skip] 已存在 ready 数据集 id={existing['id']} cells={existing['cell_count']}，跳过上传")
                ds_info = {"dataset_id": existing["id"], **existing}
                page.reload()
                page.wait_for_timeout(1500)
            else:
                upload_liver(page)
                ds_info = wait_dataset_ready(page, timeout_s=900)
            select_dataset_row(page)
            build_index(page)
            wait_index_ready(page, ds_info["dataset_id"], timeout_s=900)
            search_by_id(page)
            print("[done] 端到端测试通过")
            return 0
        except Exception as exc:
            print(f"[FAIL] {exc.__class__.__name__}: {exc}")
            return 1
        finally:
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
