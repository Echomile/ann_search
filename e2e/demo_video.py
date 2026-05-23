"""演示视频录制脚本：Playwright + 内置视频录制 + 中文 TTS。

设计：
    - 用 Playwright 录制完整端到端浏览器操作，输出 webm（无音频）
    - 旁白由独立的 narration.txt 段落决定，每段对应一个"演示节点"
    - 节点之间用 page.wait_for_timeout 等待时间与配音长度对齐
    - 最终用 ffmpeg 把多段 TTS 音频拼接到 webm 上

运行：
    cd backend && uv run python ../e2e/demo_video.py

依赖：
    - playwright（已装）
    - 系统 Chrome
    - macOS say + afconvert（无需 brew 安装）
    - ffmpeg（可选，用于合成最终 mp4）

输出：
    docs/video/demo_screen.webm     — 屏幕录制（playwright）
    docs/video/narration/*.aiff     — 每段配音
    docs/video/narration_full.m4a   — 拼接后的完整音轨
    docs/video/demo_final.mp4       — 最终合成视频（需要 ffmpeg）
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
VIDEO_DIR = ROOT / "docs" / "video"
NARR_DIR = VIDEO_DIR / "narration"
VIDEO_DIR.mkdir(parents=True, exist_ok=True)
NARR_DIR.mkdir(parents=True, exist_ok=True)

USERNAME = "demo"
PASSWORD = "demo1234"
BASE_URL = "http://localhost:5173"


@dataclass
class Step:
    """一个演示节点：旁白 + 浏览器动作。"""

    name: str
    narration: str
    action: callable  # 接收 page 参数

    def synth_narration(self) -> Path:
        """用 macOS say + afconvert 生成 m4a 音频。"""
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
    page.wait_for_timeout(2000)
    # 滚动展示
    page.mouse.wheel(0, 600)
    page.wait_for_timeout(2000)
    page.mouse.wheel(0, -600)
    page.wait_for_timeout(1000)


def step_select_dataset(page: Page) -> None:
    # 点击第一行选中
    rows = page.locator("table tbody tr")
    if rows.count() > 0:
        rows.first.click()
        page.wait_for_timeout(1500)


def step_indexes(page: Page) -> None:
    page.get_by_role("menuitem", name="索引管理").click()
    page.wait_for_url(f"{BASE_URL}/indexes")
    page.wait_for_timeout(2500)


def step_search(page: Page) -> None:
    page.get_by_role("menuitem", name="检索").click()
    page.wait_for_url(f"{BASE_URL}/search")
    page.wait_for_timeout(2500)


def step_search_demo(page: Page) -> None:
    """触发一次真实检索演示。"""
    info = page.evaluate(
        """async () => {
            const token = localStorage.getItem('ann_search_token');
            const headers = {Authorization: `Bearer ${token}`};
            const datasets = await (await fetch('/api/v1/datasets', {headers})).json();
            const ds = datasets.find(d => d.status === 'ready');
            if (!ds) return {error: 'no-dataset'};
            const indexes = await (await fetch(`/api/v1/datasets/${ds.id}/indexes`, {headers})).json();
            const idx = indexes.find(x => x.status === 'ready');
            if (!idx) return {error: 'no-index'};
            const vec = new Array(ds.vector_dim).fill(0).map(() => Math.random() - 0.5);
            const r = await fetch('/api/v1/search/by-vector', {
                method: 'POST',
                headers: {'Content-Type': 'application/json', ...headers},
                body: JSON.stringify({dataset_id: ds.id, index_id: idx.id, vector: vec, top_k: 10}),
            });
            const data = await r.json();
            return {hits: data.hits?.length, latency: data.latency_ms, first_cell: data.hits?.[0]?.cell_id};
        }"""
    )
    print(f"[demo] 检索：{info}")
    page.wait_for_timeout(2000)


def step_visualization(page: Page) -> None:
    page.get_by_role("menuitem", name="可视化").click()
    page.wait_for_url(f"{BASE_URL}/visualization")
    page.wait_for_timeout(3000)


def step_evaluation(page: Page) -> None:
    page.get_by_role("menuitem", name="性能评测").click()
    page.wait_for_url(f"{BASE_URL}/evaluation")
    page.wait_for_timeout(3000)


def step_rag(page: Page) -> None:
    page.get_by_role("menuitem", name="RAG").click()
    page.wait_for_url(f"{BASE_URL}/rag")
    page.wait_for_timeout(3000)


def step_outro(page: Page) -> None:
    page.wait_for_timeout(2000)


STEPS: list[Step] = [
    Step(
        name="01_intro",
        narration=(
            "大家好。这是我们小组的软件工程大作业演示，"
            "项目是面向单细胞高维向量数据的近似最近邻检索系统。"
            "整个系统采用前后端分离架构。"
        ),
        action=step_intro,
    ),
    Step(
        name="02_login",
        narration=(
            "首先看登录页。我们实现了完整的注册与登录功能，"
            "使用 JWT 鉴权与 bcrypt 密码哈希。"
            "我用演示账号登录系统。"
        ),
        action=step_login,
    ),
    Step(
        name="03_datasets",
        narration=(
            "登录后进入数据集管理页面。"
            "系统支持上传 h5ad 文件，后端使用 Scanpy 自动完成质控、归一化与 PCA 降维。"
            "目前已上传一个真实数据集，包含六万九千个肝脏细胞，每个细胞是三十维 PCA 向量。"
        ),
        action=step_datasets,
    ),
    Step(
        name="04_select_dataset",
        narration=(
            "点击表格选中数据集，后续的索引、检索、可视化等页面会基于这个选中数据集工作。"
        ),
        action=step_select_dataset,
    ),
    Step(
        name="05_indexes",
        narration=(
            "进入索引管理页。我们实现了五种 ANN 后端，包括 HNSWLIB、FAISS-HNSW、"
            "FAISS-IVFPQ、暴力检索基线，以及我们改进的自适应 HNSW。"
            "六万九千细胞规模下，hnswlib 索引构建仅需零点六八秒，内存占用十六兆。"
        ),
        action=step_indexes,
    ),
    Step(
        name="06_search",
        narration=(
            "检索页支持三种查询方式：按细胞编号、按自定义向量、以及多数据集联合检索。"
            "同时支持条件过滤，例如按 cell type 或 disease 限定返回结果。"
        ),
        action=step_search,
    ),
    Step(
        name="07_search_demo",
        narration=(
            "我们触发一次真实检索。后端使用 hnswlib 索引，"
            "单次查询延迟稳定在零点五毫秒以下，命中结果包含完整的五十六列元数据。"
        ),
        action=step_search_demo,
    ),
    Step(
        name="08_visualization",
        narration=(
            "可视化页面使用 Plotly 渲染二维散点图，"
            "查询细胞用红色高亮，前 K 个相似邻居用橙色，背景细胞用灰色。"
            "可以按细胞类型着色，方便观察聚类结构。"
        ),
        action=step_visualization,
    ),
    Step(
        name="09_evaluation",
        narration=(
            "性能评测页支持自动跑基准测试，"
            "测量五种后端在不同并发与不同 top K 下的召回率、P50、P95、P99 延迟以及 QPS。"
            "在 liver 数据上 hnswlib 的 Recall at 10 达到 99.96%。"
        ),
        action=step_evaluation,
    ),
    Step(
        name="10_rag",
        narration=(
            "最后是加分项 RAG 自然语言查询页面。"
            "用户用中文提问，大模型自动解析为结构化检索参数，"
            "调用 ANN 检索后再用自然语言总结结果。"
            "支持 Mock、通义千问、OpenAI 三种后端，默认 Mock 零依赖运行。"
        ),
        action=step_rag,
    ),
    Step(
        name="11_outro",
        narration=(
            "感谢观看。项目源码已托管在 GitHub，"
            "完整文档与性能基准报告见 docs 目录。"
            "谢谢！"
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

    print("[4/4] 合并视频 + 音轨 ...")
    final = VIDEO_DIR / "demo_final.mp4"
    merge_video_audio(video, full_audio, final)
    print(f"  完成 -> {final}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
