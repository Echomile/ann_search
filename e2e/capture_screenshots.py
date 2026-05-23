"""真实数据 UI 截图脚本。

不同于 demo_video（只走菜单），本脚本会**真实触发 UI 交互**：
    1. 数据集页：上传/选中（沿用已就绪数据集）
    2. 索引页：构建新索引并等到 ready
    3. 检索页：填入 cell_id 点击"发起检索" → 等待结果表格出现 → 截图
    4. 可视化页：选数据集 + cell_id → 截图带散点图
    5. 评测页：点击"运行评测" → 等待历史列表出现 → 截图
    6. RAG 页：输入提问 → 点击发送 → 等待 AI 回复出现 → 截图

输出：覆盖 docs/e2e_screenshots/ 下的 01-09 张高质量截图。

运行：
    cd backend && uv run python ../e2e/capture_screenshots.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SCREENSHOT_DIR = ROOT / "docs" / "e2e_screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

USERNAME = "demo"
PASSWORD = "demo1234"
BASE_URL = "http://localhost:5173"


def shot(page: Page, name: str, full_page: bool = False) -> None:
    """对当前页面截图。默认只截视口（避免无限长表格被全屏抓取）。"""
    path = SCREENSHOT_DIR / f"{name}.png"
    page.screenshot(path=str(path), full_page=full_page)
    print(f"[shot] {path.relative_to(ROOT)}")


def login(page: Page) -> None:
    page.goto(f"{BASE_URL}/login")
    page.get_by_placeholder("用户名").fill(USERNAME)
    page.get_by_placeholder("密码").fill(PASSWORD)
    page.get_by_role("button", name="登 录").click()
    page.wait_for_url(f"{BASE_URL}/datasets", timeout=10_000)
    page.wait_for_timeout(1500)


def select_first_dataset(page: Page) -> dict:
    """选中第一个 ready 数据集，并返回它的 id/cell_id 等。"""
    info = page.evaluate(
        """async () => {
            const token = localStorage.getItem('ann_search_token');
            const headers = {Authorization: `Bearer ${token}`};
            const list = await (await fetch('/api/v1/datasets', {headers})).json();
            const ready = list.find(d => d.status === 'ready');
            return ready;
        }"""
    )
    assert info, "需要至少一个 status=ready 的数据集"
    print(f"[info] using dataset id={info['id']} cells={info['cell_count']} dim={info['vector_dim']}")
    page.locator("table tbody tr").first.click()
    page.wait_for_timeout(800)
    shot(page, "04_dataset_ready")
    return info


def ensure_index(page: Page, ds_info: dict) -> dict:
    """跳到索引页，若没有 ready 索引则构建一个。返回 (id, backend)。"""
    page.get_by_role("menuitem", name="索引管理").click()
    page.wait_for_url(f"{BASE_URL}/indexes")
    page.wait_for_timeout(1500)
    shot(page, "05_index_page")

    existing = page.evaluate(
        """async (dsId) => {
            const token = localStorage.getItem('ann_search_token');
            const list = await (await fetch(`/api/v1/datasets/${dsId}/indexes`, {headers: {Authorization: `Bearer ${token}`}})).json();
            return list.find(x => x.status === 'ready') || null;
        }""",
        ds_info["id"],
    )
    if existing:
        print(f"[skip] index ready id={existing['id']} backend={existing['backend']}")
        return existing
    # 没现成的 -> 点击 "开始构建"
    page.get_by_role("button", name="开始构建").click()
    expect(page.get_by_text("索引构建任务已入队")).to_be_visible(timeout=30_000)
    # 轮询
    deadline = time.time() + 600
    while time.time() < deadline:
        info = page.evaluate(
            """async (dsId) => {
                const token = localStorage.getItem('ann_search_token');
                const list = await (await fetch(`/api/v1/datasets/${dsId}/indexes`, {headers: {Authorization: `Bearer ${token}`}})).json();
                const ready = list.find(x => x.status === 'ready');
                return ready || (list[0] || null);
            }""",
            ds_info["id"],
        )
        if info and info.get("status") == "ready":
            page.reload()
            page.wait_for_timeout(1500)
            shot(page, "06_index_ready")
            return info
        time.sleep(3)
    raise TimeoutError("等待索引 ready 超时")


def pick_cell_id(page: Page, ds_info: dict, idx_info: dict) -> str:
    """先随机检索一次，拿到一个真实存在的 cell_id 用于 by-id 检索。"""
    res = page.evaluate(
        """async ({dsId, idxId, dim}) => {
            const token = localStorage.getItem('ann_search_token');
            const vec = new Array(dim).fill(0).map(() => Math.random() - 0.5);
            const r = await fetch('/api/v1/search/by-vector', {
                method: 'POST',
                headers: {'Content-Type': 'application/json', Authorization: `Bearer ${token}`},
                body: JSON.stringify({dataset_id: dsId, index_id: idxId, vector: vec, top_k: 1})
            });
            const data = await r.json();
            return data.hits?.[0]?.cell_id;
        }""",
        {"dsId": ds_info["id"], "idxId": idx_info["id"], "dim": ds_info["vector_dim"]},
    )
    print(f"[info] pick cell_id={res}")
    return res


def search_demo(page: Page, cell_id: str) -> None:
    """检索页：输入 cell_id 点击发起检索 → 等结果表 → 截图。"""
    page.get_by_role("menuitem", name="检索").click()
    page.wait_for_url(f"{BASE_URL}/search")
    page.wait_for_timeout(1500)
    # 默认就是"按细胞 ID"tab
    page.get_by_placeholder("例如 AAACATACAACCAC-1").fill(cell_id)
    page.wait_for_timeout(500)
    page.get_by_role("button", name="发起检索").click()
    # 等结果表格出现
    page.wait_for_selector("table tbody tr", timeout=30_000)
    page.wait_for_timeout(1500)
    # 滚动到结果区域，让表格在视口里
    page.evaluate("document.querySelector('table')?.scrollIntoView({behavior: 'instant', block: 'center'})")
    page.wait_for_timeout(800)
    shot(page, "07_search_result")


def visualization_demo(page: Page, cell_id: str) -> None:
    """可视化页：填 cell_id → 点击渲染散点图 → 等 Plotly 图绘出 → 截图。"""
    page.get_by_role("menuitem", name="可视化").click()
    page.wait_for_url(f"{BASE_URL}/visualization")
    page.wait_for_timeout(2000)
    cell_input = page.get_by_placeholder("留空则只显示背景点")
    cell_input.fill(cell_id)
    page.wait_for_timeout(400)
    page.get_by_role("button", name="渲染散点图").click()
    # 等 Plotly SVG 内出现红色查询点（rank=1）
    try:
        page.wait_for_selector(".js-plotly-plot .scatterlayer .trace", timeout=20_000)
    except Exception as e:
        print(f"[warn] plotly 散点未检测到: {e}")
    page.wait_for_timeout(2500)
    shot(page, "10_visualization", full_page=True)


def evaluation_demo(page: Page, ds_info: dict, idx_info: dict) -> None:
    """评测页：选索引 → 点击运行评测 → 等历史列表出现 → 点查看详情 → 截图。"""
    page.get_by_role("menuitem", name="性能评测").click()
    page.wait_for_url(f"{BASE_URL}/evaluation")
    page.wait_for_timeout(2000)

    # 选索引（必填字段）
    try:
        idx_select = page.locator("input[placeholder*='ready 索引'], .ant-select-selector").nth(1)
        idx_select.click()
        page.wait_for_timeout(800)
        # 选第一个 option
        option = page.locator(".ant-select-item-option").first
        if option.count() > 0:
            option.click()
            page.wait_for_timeout(500)
    except Exception as e:
        print(f"[warn] 选索引失败: {e}")

    # 设置较小的查询数量加速
    try:
        num_input = page.locator('input[type="number"]').nth(0)
        num_input.fill("30")
        page.wait_for_timeout(300)
    except Exception:
        pass
    page.get_by_role("button", name="运行评测").click()
    page.wait_for_timeout(2000)
    # 评测可能进入异步队列（ARQ 失败时降级前台），轮询 GET /evaluation/results
    deadline = time.time() + 120
    while time.time() < deadline:
        res = page.evaluate(
            """async () => {
                const token = localStorage.getItem('ann_search_token');
                const r = await fetch('/api/v1/evaluation/results', {headers: {Authorization: `Bearer ${token}`}});
                if (!r.ok) return [];
                return await r.json();
            }"""
        )
        if isinstance(res, list) and len(res) > 0:
            print(f"[info] evaluation results count = {len(res)}")
            break
        time.sleep(3)
    # 触发"刷新历史"
    try:
        page.get_by_role("button", name="刷新历史").click()
        page.wait_for_timeout(2000)
        # 点击 "查看详情"
        page.get_by_role("button", name="查看详情").first.click()
        page.wait_for_timeout(3000)
    except Exception as e:
        print(f"[warn] 评测交互: {e}")
    shot(page, "08_evaluation", full_page=True)


def rag_demo(page: Page) -> None:
    """RAG 页：输入问题 → 点击发送 → 等 AI 回复 → 截图。"""
    page.get_by_role("menuitem", name="RAG").click()
    page.wait_for_url(f"{BASE_URL}/rag")
    page.wait_for_timeout(2000)
    textarea = page.get_by_placeholder("输入自然语言查询，Enter 发送，Shift+Enter 换行")
    textarea.fill("在肝脏中找出与 hepatocyte 类似的 5 个细胞")
    page.wait_for_timeout(400)
    page.get_by_role("button", name="发送").click()
    # 等 AI 回复（answer 出现）
    try:
        page.wait_for_function(
            """() => document.body.innerText.includes('为您找到') || document.body.innerText.includes('hits') || document.querySelectorAll('table tbody tr').length > 0""",
            timeout=30_000,
        )
    except Exception as e:
        print(f"[warn] RAG 等待超时: {e}")
    page.wait_for_timeout(2500)
    shot(page, "09_rag", full_page=True)


def datasets_demo(page: Page) -> None:
    """数据集页：导航 + 截图（含已就绪的 liver）。"""
    page.get_by_role("menuitem", name="数据集").click()
    page.wait_for_url(f"{BASE_URL}/datasets")
    page.wait_for_timeout(2000)
    shot(page, "03_uploaded")


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=False, args=["--no-sandbox"])
        try:
            ctx = browser.new_context(viewport={"width": 1440, "height": 1080})
            page = ctx.new_page()
            login(page)
            shot(page, "01_after_login")

            # 数据集页（带列表）
            datasets_demo(page)

            ds_info = select_first_dataset(page)
            idx_info = ensure_index(page, ds_info)
            cell_id = pick_cell_id(page, ds_info, idx_info)

            search_demo(page, cell_id)
            visualization_demo(page, cell_id)
            evaluation_demo(page, ds_info, idx_info)
            rag_demo(page)

            print("[done] 截图全部完成")
            return 0
        except Exception as exc:
            print(f"[FAIL] {exc.__class__.__name__}: {exc}")
            return 1
        finally:
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
