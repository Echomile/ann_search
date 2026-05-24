"""演示视频录制脚本：Playwright + 内置视频录制 + 中文神经语音 TTS。

设计：
    - 用 Playwright 录制完整端到端浏览器操作，输出 webm（无音频）
    - 旁白由独立的 narration.txt 段落决定，每段对应一个"演示节点"
    - 节点之间用 page.wait_for_timeout 等待时间与配音长度对齐
    - 最终用 ffmpeg 把多段 TTS 音频拼接到 webm 上

旁白合成：
    默认使用 ``edge-tts``（Microsoft Edge 神经语音，免费、无需 API Key），
    可通过环境变量切换音色 / 语速：

        DEMO_TTS_VOICE  默认 ``zh-CN-YunyangNeural`` (云扬·新闻播音腔)
                        可选 ``zh-CN-YunjianNeural``  (云健·稳重)
                             ``zh-CN-XiaoxuanNeural`` (晓萱·清新)
                             ``zh-CN-XiaoxiaoNeural`` (晓晓·温柔)
        DEMO_TTS_RATE   默认 ``+0%``，信息密集段建议 ``-5%``

    若 edge-tts 调用失败（断网、被风控等），自动降级回 macOS 自带
    ``say -v Tingting``，保证视频仍可产出。

运行：
    cd backend && uv run python ../e2e/demo_video.py

依赖：
    - playwright（已装）
    - 系统 Chrome
    - edge-tts (``uv sync --extra video``，需联网；离线时自动降级到 say)
    - macOS say + afconvert（仅作降级 fallback）
    - ffmpeg（必装，用于格式转换与合成）

输出：
    docs/video/demo_screen.webm     — 屏幕录制（playwright）
    docs/video/narration/*.mp3      — edge-tts 原始 mp3
    docs/video/narration/*.m4a      — 转码后用于 concat 的 AAC 音频
    docs/video/narration_full.m4a   — 拼接后的完整音轨
    docs/video/demo_final.mp4       — 最终合成视频
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import BASE_URL, PASSWORD, USERNAME  # noqa: E402
from make_video_cards import wrap_with_intro_outro  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
VIDEO_DIR = ROOT / "docs" / "video"
NARR_DIR = VIDEO_DIR / "narration"
VIDEO_DIR.mkdir(parents=True, exist_ok=True)
NARR_DIR.mkdir(parents=True, exist_ok=True)

TTS_VOICE = os.getenv("DEMO_TTS_VOICE", "zh-CN-YunyangNeural")
TTS_RATE = os.getenv("DEMO_TTS_RATE", "+0%")


def _edge_tts_save(text: str, out: Path, *, voice: str, rate: str) -> None:
    """用 edge-tts 把单段文本合成为 mp3。

    Args:
        text: 待合成的中文旁白。
        out: 输出 mp3 路径。
        voice: Microsoft Edge 神经语音 ShortName，例如 ``zh-CN-YunyangNeural``。
        rate: 语速调整，``+0%`` 为默认，``-5%`` 表示放慢 5%。

    Raises:
        Exception: 网络异常、风控或参数不合法时由 edge-tts / aiohttp 抛出，
            上层负责捕获并降级到 macOS ``say``。
    """
    import edge_tts

    async def _run() -> None:
        await edge_tts.Communicate(text=text, voice=voice, rate=rate).save(str(out))

    asyncio.run(_run())


@dataclass
class Step:
    """一个演示节点：旁白 + 浏览器动作。"""

    name: str
    narration: str
    action: callable  # 接收 page 参数

    def synth_narration(self) -> Path:
        """合成本段旁白为 m4a，优先 edge-tts，失败时降级 macOS ``say``。"""
        m4a = NARR_DIR / f"{self.name}.m4a"
        mp3 = NARR_DIR / f"{self.name}.mp3"
        try:
            _edge_tts_save(self.narration, mp3, voice=TTS_VOICE, rate=TTS_RATE)
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(mp3), "-c:a", "aac", str(m4a)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return m4a
        except Exception as exc:
            print(f"[tts] edge-tts 失败，降级 say: {exc}")
            return self._synth_with_say()

    def _synth_with_say(self) -> Path:
        """降级路径：用 macOS ``say`` + ``afconvert`` 生成 m4a。"""
        aiff = NARR_DIR / f"{self.name}.aiff"
        m4a = NARR_DIR / f"{self.name}.m4a"
        subprocess.run(
            ["say", "-v", "Tingting", "-r", "190", "-o", str(aiff), self.narration],
            check=True,
        )
        subprocess.run(
            ["afconvert", "-f", "m4af", "-d", "aac", str(aiff), str(m4a)],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        return m4a


DEMO_CELL_ID: str | None = None  # 由 step_search_demo 自动填充


def step_intro(page: Page) -> None:
    page.goto(f"{BASE_URL}/login")
    page.wait_for_timeout(1500)


def step_login(page: Page) -> None:
    page.get_by_placeholder("用户名").fill(USERNAME, timeout=10_000)
    page.wait_for_timeout(500)
    page.get_by_placeholder("密码").fill(PASSWORD)
    page.wait_for_timeout(500)
    page.get_by_role("button", name="登 录").click()
    page.wait_for_url(f"{BASE_URL}/datasets", timeout=10_000)
    page.wait_for_timeout(1500)


def step_datasets(page: Page) -> None:
    page.get_by_role("menuitem", name="数据集").click()
    page.wait_for_timeout(1500)
    page.evaluate(
        "document.querySelector('table')?.scrollIntoView({behavior: 'smooth', block: 'center'})"
    )
    page.wait_for_timeout(1500)


def step_select_dataset(page: Page) -> None:
    rows = page.locator("table tbody tr")
    if rows.count() > 0:
        rows.first.click()
        page.wait_for_timeout(1500)


def step_indexes(page: Page) -> None:
    page.get_by_role("menuitem", name="索引管理").click()
    page.wait_for_url(f"{BASE_URL}/indexes")
    page.wait_for_timeout(1500)
    page.evaluate(
        "document.querySelector('table')?.scrollIntoView({behavior: 'smooth', block: 'center'})"
    )
    page.wait_for_timeout(2500)


def step_search(page: Page) -> None:
    page.get_by_role("menuitem", name="检索").click()
    page.wait_for_url(f"{BASE_URL}/search")
    page.wait_for_timeout(2500)


def step_search_demo(page: Page) -> None:
    """触发一次 UI 真实检索演示并展示结果。"""
    global DEMO_CELL_ID
    # 拿到一个真实存在的 cell_id（先 by-vector 拉一条）
    info = page.evaluate(
        """async () => {
            const token = localStorage.getItem('ann_search_token');
            const headers = {Authorization: `Bearer ${token}`};
            const datasets = await (await fetch('/api/v1/datasets', {headers})).json();
            const ds = datasets.find(d => d.status === 'ready');
            const indexes = await (await fetch(`/api/v1/datasets/${ds.id}/indexes`, {headers})).json();
            const idx = indexes.find(x => x.status === 'ready');
            const vec = new Array(ds.vector_dim).fill(0).map(() => Math.random() - 0.5);
            const r = await fetch('/api/v1/search/by-vector', {
                method: 'POST',
                headers: {'Content-Type': 'application/json', ...headers},
                body: JSON.stringify({dataset_id: ds.id, index_id: idx.id, vector: vec, top_k: 1}),
            });
            const d = await r.json();
            return d.hits?.[0]?.cell_id;
        }"""
    )
    DEMO_CELL_ID = info or "CGACTTCTCCAGGGCT-1_18"
    print(f"[demo] cell_id={DEMO_CELL_ID}")
    # UI 操作：填入并发起检索
    page.get_by_placeholder("例如 AAACATACAACCAC-1").fill(DEMO_CELL_ID)
    page.wait_for_timeout(800)
    page.get_by_role("button", name="发起检索").click()
    page.wait_for_selector("table tbody tr", timeout=20_000)
    page.wait_for_timeout(1000)
    page.evaluate("document.querySelector('table')?.scrollIntoView({behavior: 'smooth', block: 'center'})")
    page.wait_for_timeout(2000)


def step_visualization(page: Page) -> None:
    page.get_by_role("menuitem", name="可视化").click()
    page.wait_for_url(f"{BASE_URL}/visualization")
    page.wait_for_timeout(1500)
    if DEMO_CELL_ID:
        page.get_by_placeholder("留空则只显示背景点").fill(DEMO_CELL_ID)
        page.wait_for_timeout(500)
        page.get_by_role("button", name="渲染散点图").click()
        try:
            page.wait_for_selector(".js-plotly-plot .scatterlayer .trace", timeout=15_000)
        except Exception:
            pass
        page.wait_for_timeout(1000)
        page.evaluate(
            "document.querySelector('.js-plotly-plot')?.scrollIntoView({behavior: 'smooth', block: 'center'})"
        )
    page.wait_for_timeout(2000)


def step_evaluation(page: Page) -> None:
    page.get_by_role("menuitem", name="性能评测").click()
    page.wait_for_url(f"{BASE_URL}/evaluation")
    page.wait_for_timeout(1500)
    # 选索引（必填）
    try:
        idx_select = page.locator(".ant-select-selector").nth(1)
        idx_select.click()
        page.wait_for_timeout(600)
        page.locator(".ant-select-item-option").first.click()
        page.wait_for_timeout(400)
    except Exception:
        pass
    page.get_by_role("button", name="运行评测").click()
    # 等历史出现，再点查看详情
    deadline = time.time() + 60
    while time.time() < deadline:
        res = page.evaluate(
            """async () => {
                const token = localStorage.getItem('ann_search_token');
                const r = await fetch('/api/v1/evaluation/results', {headers: {Authorization: `Bearer ${token}`}});
                if (!r.ok) return 0;
                return (await r.json()).length;
            }"""
        )
        if res and int(res) > 0:
            break
        time.sleep(2)
    try:
        page.get_by_role("button", name="刷新历史").click()
        page.wait_for_timeout(1500)
        page.get_by_role("button", name="查看详情").first.click()
        page.wait_for_timeout(2500)
        page.evaluate(
            "document.querySelectorAll('.js-plotly-plot')[0]?.scrollIntoView({behavior: 'smooth', block: 'center'})"
        )
        page.wait_for_timeout(1500)
    except Exception:
        pass


def step_rag(page: Page) -> None:
    page.get_by_role("menuitem", name="RAG").click()
    page.wait_for_url(f"{BASE_URL}/rag")
    page.wait_for_timeout(1500)
    textarea = page.get_by_placeholder("输入自然语言查询，Enter 发送，Shift+Enter 换行")
    textarea.fill("在肝脏中找出与 hepatocyte 类似的 5 个细胞")
    page.wait_for_timeout(500)
    page.get_by_role("button", name="发送").click()
    try:
        page.wait_for_function(
            """() => document.body.innerText.includes('为您找到') || document.querySelectorAll('table tbody tr').length > 0""",
            timeout=20_000,
        )
    except Exception:
        pass
    page.wait_for_timeout(2000)


def step_admin(page: Page) -> None:
    """演示 v1.1 新增的管理员用户管理页。"""
    page.get_by_role("menuitem", name="用户管理").click()
    page.wait_for_url(f"{BASE_URL}/admin/users")
    try:
        page.wait_for_selector("table tbody tr", timeout=15_000)
    except Exception:
        pass
    page.wait_for_timeout(1200)
    page.evaluate(
        "document.querySelector('table')?.scrollIntoView({behavior: 'smooth', block: 'center'})"
    )
    page.wait_for_timeout(1500)


def step_search_log_dashboard(page: Page) -> None:
    """演示 v1.1 新增的检索日志统计 Dashboard（评测页底部）。"""
    page.get_by_role("menuitem", name="性能评测").click()
    page.wait_for_url(f"{BASE_URL}/evaluation")
    page.wait_for_timeout(1500)
    page.evaluate(
        """() => {
            const cards = Array.from(document.querySelectorAll('.ant-card-head-title'));
            const target = cards.find(el => (el.textContent || '').includes('检索日志统计'));
            if (target) target.scrollIntoView({behavior: 'smooth', block: 'start'});
        }"""
    )
    page.wait_for_timeout(2500)


def step_index_detail(page: Page) -> None:
    """演示 v1.1 新增的索引详情页 /indexes/:id。"""
    target_id = page.evaluate(
        """async () => {
            const token = localStorage.getItem('ann_search_token');
            const headers = {Authorization: `Bearer ${token}`};
            const datasets = await (await fetch('/api/v1/datasets', {headers})).json();
            for (const ds of datasets) {
                if (ds.status !== 'ready') continue;
                const list = await (await fetch(`/api/v1/datasets/${ds.id}/indexes`, {headers})).json();
                if (!Array.isArray(list)) continue;
                const ready = list.find(x => x.status === 'ready');
                if (ready) return ready.id;
            }
            return null;
        }"""
    )
    if target_id:
        print(f"[demo] index_detail target id={target_id}")
        page.goto(f"{BASE_URL}/indexes/{target_id}")
        try:
            page.wait_for_selector(".ant-descriptions", timeout=15_000)
        except Exception:
            pass
        page.wait_for_timeout(1500)
        page.evaluate(
            "document.querySelector('.ant-descriptions')?.scrollIntoView({behavior: 'smooth', block: 'center'})"
        )
        page.wait_for_timeout(2000)


def step_new_search_tabs(page: Page) -> None:
    """演示 F6 SSE 流式 Tab 与 F7 ensemble 多后端 Tab。"""
    page.get_by_role("menuitem", name="检索").click()
    page.wait_for_url(f"{BASE_URL}/search")
    page.wait_for_timeout(1200)
    try:
        page.get_by_role("tab", name="SSE 流式").click()
        page.wait_for_timeout(2500)
        page.evaluate(
            "document.querySelector('.ant-tabs-tabpane-active')?.scrollIntoView({behavior: 'smooth', block: 'start'})"
        )
        page.wait_for_timeout(1500)
    except Exception:
        pass
    try:
        page.get_by_role("tab", name="Ensemble 多后端").click()
        page.wait_for_timeout(2500)
        page.evaluate(
            "document.querySelector('.ant-tabs-tabpane-active')?.scrollIntoView({behavior: 'smooth', block: 'start'})"
        )
        page.wait_for_timeout(2000)
    except Exception:
        pass


def step_outro(page: Page) -> None:
    page.wait_for_timeout(2000)


STEPS: list[Step] = [
    Step(
        name="01_intro",
        narration=(
            "各位老师同学好，欢迎观看我们小组的软件工程大作业演示。"
            "项目题目是 单细胞高维向量近似最近邻检索系统，"
            "面向真实的单细胞测序数据，提供从数据上传、预处理、索引构建、相似检索、"
            "可视化到自然语言问答的完整 Web 平台。"
            "系统采用前后端分离架构，下面我会用真实的肝脏细胞数据带大家走一遍核心功能。"
        ),
        action=step_intro,
    ),
    Step(
        name="02_login",
        narration=(
            "首先看登录页。系统使用 JWT 鉴权，结合 bcrypt 密码哈希。"
            "我们还实现了管理员角色控制，登录后才能访问业务功能。"
            "现在用演示账号 demo 进入系统。"
        ),
        action=step_login,
    ),
    Step(
        name="03_datasets",
        narration=(
            "登录后进入数据集管理页。这里支持拖拽上传 h5ad 文件，"
            "上传时可以指定数据集名称，后端使用 Scanpy 自动完成质控、归一化、"
            "对数变换、高变基因筛选与 PCA 降维。"
            "整个预处理过程通过 ARQ 异步任务队列在后台 Worker 中执行，"
            "前端通过轮询接口拿到 uploading、preprocessing、ready 三态变化。"
            "当前列表里这条 liver 数据集，是真实的儿童肝脏单细胞图谱，"
            "包含六万九千零三十二个细胞，每个细胞是三十维的 PCA 向量。"
        ),
        action=step_datasets,
    ),
    Step(
        name="04_select_dataset",
        narration=(
            "点击表格中任意一行就能把它设为当前数据集，"
            "后续的索引、检索、可视化等页面会自动基于这个选中数据集工作。"
            "如果想删除，点击右侧操作列的删除按钮，"
            "系统会自动级联清理向量文件、所有相关索引和数据库记录。"
        ),
        action=step_select_dataset,
    ),
    Step(
        name="05_indexes",
        narration=(
            "切换到索引管理页。这里是系统的核心：我们实现了五种 ANN 后端，"
            "包括标准的 HNSWLIB、Facebook 的 FAISS 提供的 HNSW 与 IVFPQ、"
            "作为召回率基线的 Brute force 暴力检索，以及我们小组改进的"
            "Adaptive HNSW 自适应 ef 算法。"
            "五种后端通过统一的 IndexBackend 抽象接口接入，前端可以一键切换。"
            "在六万九千细胞这个规模下，hnswlib 索引构建只需要零点二秒，"
            "内存占用十六兆字节，召回率达到百分之九十九点九六。"
        ),
        action=step_indexes,
    ),
    Step(
        name="06_search",
        narration=(
            "检索页提供三种查询模式：按细胞编号查询、按自定义向量查询、"
            "以及扩展功能之一的多数据集联合检索。"
            "每种模式都支持条件过滤，比如可以限定只返回 cell type 为 hepatocyte、"
            "或者 disease 为 normal 的细胞。"
            "下面让我们触发一次真实检索看看效果。"
        ),
        action=step_search,
    ),
    Step(
        name="07_search_demo",
        narration=(
            "刚刚的请求中，我用一个随机三十维向量调用 by-vector 接口，"
            "后端使用 hnswlib 索引完成 Top 10 近邻检索。"
            "整个查询的端到端延迟稳定在零点五到一毫秒以内，"
            "命中结果包含完整的五十六列元数据，比如 cell type、tissue、"
            "disease、donor age 等等，足够支撑下游的细胞类型注释与跨样本比较分析。"
        ),
        action=step_search_demo,
    ),
    Step(
        name="08_visualization",
        narration=(
            "可视化页使用 Plotly 渲染二维散点图。"
            "查询细胞用红色大点高亮，前 K 个相似邻居用橙色，"
            "背景细胞用灰色小点。"
            "用户可以按 cell type 着色，方便观察细胞类型的聚类结构。"
            "目前 UMAP 坐标在前端做了基于距离的轻量映射，"
            "后端 UMAP API 接口位置已经留好，后续接入即可获得真正的全局降维视图。"
        ),
        action=step_visualization,
    ),
    Step(
        name="09_evaluation",
        narration=(
            "性能评测页支持自动跑基准测试。"
            "用户选择索引、设置查询数、Top K 列表与并发列表，"
            "后端会用 Brute force 计算 Ground truth，"
            "然后测量 Recall at 10、Recall at 100、P50、P95、P99 延迟以及 QPS。"
            "在 liver 数据上 hnswlib 的 Recall at 10 达到零点九九九六，"
            "FAISS IVFPQ 通过乘积量化把内存压到零点二九兆，节省了二十四倍。"
            "完整的基准报告自动生成到 docs 目录下的 benchmark report markdown。"
        ),
        action=step_evaluation,
    ),
    Step(
        name="10_rag",
        narration=(
            "最后是扩展功能里最有趣的 RAG 自然语言查询页面。"
            "用户用中文输入比如 找肝细胞的代表样本 这样的提问，"
            "大模型会自动解析为结构化的检索参数，"
            "包括 cell type、disease、Top K 等字段，"
            "再调用 ANN 检索接口拿到结果，最后由大模型生成中文的总结回答。"
            "系统抽象了一个 LLM Client 协议，支持 Mock 规则解析和 Anthropic Claude Opus 4.7 两种实现，"
            "默认 Mock 零依赖即可工作，无需任何 API Key。"
        ),
        action=step_rag,
    ),
    Step(
        name="11_admin",
        narration=(
            "在迭代过程中，我们还补充了完整的管理员后台页面。"
            "现在以管理员身份点击侧边栏的用户管理菜单进入。"
            "这里可以查看全部账号、随时切换 user 与 admin 角色、"
            "为忘记密码的用户生成一次性临时明文，"
            "也支持级联删除某个用户名下的全部数据集、索引与检索日志，"
            "数据库外键约束保证清理无残留。"
        ),
        action=step_admin,
    ),
    Step(
        name="12_search_log_dashboard",
        narration=(
            "在性能评测页底部，我们新增了一个检索日志统计看板。"
            "它会汇总所有用户发起的真实检索调用，"
            "展示总查询数、整体平均延迟与 P95 延迟，"
            "用双 Y 轴柱线图刻画最近 24 小时的查询量与平均延迟，"
            "并按数据集聚合给出 P95 排行，方便定位线上的慢查询。"
        ),
        action=step_search_log_dashboard,
    ),
    Step(
        name="13_index_detail",
        narration=(
            "索引管理页里每一条记录都可以点进独立的详情页。"
            "详情页聚合展示了索引的后端、距离度量、构建耗时、内存占用、"
            "参数 JSON、磁盘文件路径，"
            "以及最近一次评测的多档 Recall 与并发延迟表，"
            "方便横向对比 hnswlib、FAISS、Adaptive HNSW "
            "在同一数据集上的真实表现。"
        ),
        action=step_index_detail,
    ),
    Step(
        name="14_new_search_tabs",
        narration=(
            "检索页里也补充了两个新的检索模式。"
            "一个是基于 Server Sent Events 的流式检索，"
            "结果可以一边返回一边渲染，长查询不再黑屏等待。"
            "另一个是 ensemble 多后端检索，"
            "把同一数据集下两到五个不同索引的结果做投票融合，"
            "每条命中都能看到是哪几个索引共同推荐的，召回率比单一后端更稳。"
        ),
        action=step_new_search_tabs,
    ),
    Step(
        name="15_outro",
        narration=(
            "以上就是本次演示的全部内容。"
            "总结一下我们的核心交付：21 个 REST 接口、5 种 ANN 后端、"
            "8 个前端业务页面、35 个后端单元测试 100 percent 通过、"
            "27 个语义化 Git 提交、还有 Playwright 端到端真实数据集成测试。"
            "三个扩展功能也全部实现：多数据集联合检索、自适应 HNSW、"
            "以及大模型 RAG 自然语言问答。"
            "更多细节欢迎查看 GitHub 上的源码、答辩 PPT、"
            "性能基准报告和软件开发文档。感谢大家观看。"
        ),
        action=step_outro,
    ),
]


def synth_all_narrations() -> list[Path]:
    """串行合成所有段配音。"""
    audio_paths: list[Path] = []
    for step in STEPS:
        path = step.synth_narration()
        audio_paths.append(path)
        print(f"[tts] {step.name} -> {path.name}")
    return audio_paths


def concat_audio(audios: list[Path], out: Path) -> None:
    """用 ffmpeg concat 把多段 m4a 拼成一个。"""
    list_file = NARR_DIR / "concat.txt"
    list_file.write_text("\n".join(f"file '{p.resolve()}'" for p in audios), encoding="utf-8")
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            str(out),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def merge_video_audio(video: Path, audio: Path, out: Path) -> None:
    """合并视频与音频；音频比视频长则截断。"""
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(video),
            "-i", str(audio),
            "-c:v", "libx264",
            "-c:a", "aac",
            "-shortest",
            "-pix_fmt", "yuv420p",
            str(out),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def get_audio_duration_s(path: Path) -> float:
    """读取音频长度（秒）。用 macOS 自带 afinfo 兼容 aiff/m4a/wav。"""
    result = subprocess.run(
        ["afinfo", str(path)], capture_output=True, text=True, check=True,
    )
    for line in result.stdout.splitlines():
        if "estimated duration" in line:
            # "estimated duration: 6.520816 sec"
            return float(line.split(":")[1].strip().split()[0])
    raise RuntimeError(f"无法解析音频时长：{path}")


def run_browser_recording(audio_durations: list[float]) -> Path:
    """跑一次端到端浏览器动作，期间用 playwright 录制 webm。

    每个 step 的 wait_for_timeout 已包含在 action 中，
    这里在每个 step 完成后额外补足 audio_durations[i] - 已用时 的等待，
    确保画面切换大致与配音对齐。
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=False, args=["--no-sandbox"])
        try:
            ctx = browser.new_context(
                viewport={"width": 1440, "height": 900},
                record_video_dir=str(VIDEO_DIR),
                record_video_size={"width": 1440, "height": 900},
            )
            page = ctx.new_page()
            for i, step in enumerate(STEPS):
                t0 = time.monotonic()
                print(f"[step] {i+1}/{len(STEPS)} {step.name} (旁白 {audio_durations[i]:.1f}s)")
                step.action(page)
                used = time.monotonic() - t0
                remain = audio_durations[i] - used
                if remain > 0:
                    page.wait_for_timeout(int(remain * 1000))
            # 完成后关闭 context 让视频落盘
            video = page.video
            ctx.close()
            assert video is not None
            video_path = Path(video.path())
            final_video = VIDEO_DIR / "demo_screen.webm"
            shutil.move(video_path, final_video)
            return final_video
        finally:
            browser.close()


def main() -> int:
    print("[1/4] 合成中文 TTS 旁白 ...")
    audios = synth_all_narrations()
    durations = [get_audio_duration_s(a) for a in audios]
    print(f"  所有段总时长: {sum(durations):.1f}s")

    print("[2/4] 拼接旁白为单个 m4a ...")
    full_audio = VIDEO_DIR / "narration_full.m4a"
    if shutil.which("ffmpeg"):
        concat_audio(audios, full_audio)
        print(f"  -> {full_audio}")
    else:
        print("  [warn] ffmpeg 未安装，跳过音频拼接")
        return 2

    print("[3/4] Playwright 跑端到端录制屏幕 ...")
    video = run_browser_recording(durations)
    print(f"  -> {video}")

    print("[4/5] 合并视频 + 音轨 ...")
    final = VIDEO_DIR / "demo_final.mp4"
    merge_video_audio(video, full_audio, final)
    print(f"  -> {final}")

    print("[5/5] 拼接片头 / 片尾静态卡片 ...")
    wrap_with_intro_outro(final, video_dir=VIDEO_DIR, duration_s=3)
    print(f"  完成 -> {final}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
