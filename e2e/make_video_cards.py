"""生成演示视频的片头 / 片尾卡片，并拼接到主视频。

工作流：
    1. 用 PIL 渲染 1440x900 的 intro / outro PNG 静态卡片；
    2. 通过 ffmpeg 将每张 PNG 转为 3 秒、静音、25fps 的 mp4；
    3. 使用 ffmpeg ``filter_complex`` 把 ``intro + 主视频 + outro`` 拼接为完整
       的 ``demo_final_full.mp4``，并给 intro / outro 自动补静音音轨（与主视频
       的音轨拼接为单条轨道）。

本脚本既可作为模块被 :mod:`e2e.demo_video` 调用（暴露 :func:`make_intro_outro`
与 :func:`concat_intro_main_outro`），也可作为独立 CLI 直接运行以快速调试卡片
样式：``cd backend && uv run python ../e2e/make_video_cards.py``。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
VIDEO_DIR = ROOT / "docs" / "video"

CARD_W = 1440
CARD_H = 900
BG_COLOR = "#f5f5f5"
PRIMARY = "#1677ff"
SUB_COLOR = "#1f1f1f"
HINT_COLOR = "#595959"

CN_FONT_CANDIDATES = (
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
)


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    """按候选列表加载首个可用字体。

    Args:
        size: 字号（像素）。

    Returns:
        PIL FreeType 字体对象。若所有候选不存在，则降级为 PIL 默认位图字体
        （仅作兜底，几乎不会触发）。
    """
    for path in CN_FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


@dataclass(frozen=True)
class TextLine:
    """卡片上的一行文本。"""

    text: str
    size: int
    color: str
    y: int


def _draw_card(path: Path, lines: Iterable[TextLine]) -> None:
    """渲染单张卡片到 ``path``。

    Args:
        path: 输出 PNG 路径。
        lines: 文本行集合，``y`` 为该行基线大致中心的纵坐标（像素）。
    """
    img = Image.new("RGB", (CARD_W, CARD_H), BG_COLOR)
    draw = ImageDraw.Draw(img)
    for line in lines:
        font = _load_font(line.size)
        bbox = draw.textbbox((0, 0), line.text, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        x = (CARD_W - w) // 2 - bbox[0]
        y = line.y - h // 2 - bbox[1]
        draw.text((x, y), line.text, font=font, fill=line.color)
    img.save(path, format="PNG")


def render_intro_png(path: Path) -> None:
    """渲染片头卡片。"""
    lines = [
        TextLine("单细胞 ANN 检索系统", 96, PRIMARY, 300),
        TextLine(
            "A Web-based ANN Retrieval Platform for Single-cell Data",
            34,
            SUB_COLOR,
            430,
        ),
        TextLine("软件工程大作业 · 课程答辩", 36, HINT_COLOR, 560),
        TextLine("2026 年 5 月", 28, HINT_COLOR, 820),
    ]
    _draw_card(path, lines)


def render_outro_png(path: Path) -> None:
    """渲染片尾卡片。"""
    lines = [
        TextLine("感谢观看", 120, PRIMARY, 320),
        TextLine("源代码 / 文档 / 演示请访问项目 GitHub 仓库", 36, SUB_COLOR, 480),
        TextLine("单细胞 ANN 检索系统 · Q&A", 30, HINT_COLOR, 820),
    ]
    _draw_card(path, lines)


def png_to_mp4(png: Path, mp4: Path, duration_s: int = 3) -> None:
    """用 ffmpeg 将单张 PNG 转换为 ``duration_s`` 秒、无音轨的 mp4。

    输出参数（25fps、libx264、yuv420p、1440x900）与主视频一致，确保后续
    ``concat`` filter 可以无缝拼接。
    """
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-loop", "1", "-i", str(png),
            "-c:v", "libx264",
            "-t", str(duration_s),
            "-pix_fmt", "yuv420p",
            "-vf", f"scale={CARD_W}:{CARD_H}",
            "-r", "25",
            str(mp4),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def make_intro_outro(video_dir: Path = VIDEO_DIR, duration_s: int = 3) -> tuple[Path, Path]:
    """生成 intro/outro 的 PNG 与 mp4。返回 (intro_mp4, outro_mp4)。"""
    video_dir.mkdir(parents=True, exist_ok=True)
    intro_png = video_dir / "intro.png"
    outro_png = video_dir / "outro.png"
    intro_mp4 = video_dir / "intro.mp4"
    outro_mp4 = video_dir / "outro.mp4"

    render_intro_png(intro_png)
    render_outro_png(outro_png)
    png_to_mp4(intro_png, intro_mp4, duration_s=duration_s)
    png_to_mp4(outro_png, outro_mp4, duration_s=duration_s)
    return intro_mp4, outro_mp4


def concat_intro_main_outro(
    intro_mp4: Path,
    main_mp4: Path,
    outro_mp4: Path,
    out_mp4: Path,
    pad_s: int = 3,
) -> None:
    """用 ffmpeg ``filter_complex`` 拼接 intro/主/outro 为一个完整视频。

    - intro/outro 视频本身无音轨，使用 ``aevalsrc=0`` 生成 ``pad_s`` 秒静音；
    - 主视频音轨原样保留；
    - 输出统一编码为 libx264 + AAC，pix_fmt=yuv420p，25fps。
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", str(intro_mp4),
        "-i", str(main_mp4),
        "-i", str(outro_mp4),
        "-filter_complex",
        (
            "[0:v]setpts=PTS-STARTPTS[v0];"
            "[1:v]setpts=PTS-STARTPTS[v1];"
            "[2:v]setpts=PTS-STARTPTS[v2];"
            "[v0][v1][v2]concat=n=3:v=1:a=0[vout];"
            f"aevalsrc=0:d={pad_s}[a0];"
            f"aevalsrc=0:d={pad_s}[a2];"
            "[a0][1:a][a2]concat=n=3:v=0:a=1[aout]"
        ),
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-c:a", "aac",
        "-pix_fmt", "yuv420p", "-r", "25",
        str(out_mp4),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wrap_with_intro_outro(
    main_mp4: Path,
    video_dir: Path = VIDEO_DIR,
    duration_s: int = 3,
) -> Path:
    """对主视频套上 intro / outro，并把结果**就地**覆盖回 ``main_mp4``。

    流程：
        1. 生成片头 / 片尾 PNG -> 3 秒 mp4；
        2. 拼接到中间产物 ``demo_final_full.mp4``；
        3. 用 ``shutil.move`` 把临时产物覆盖 ``main_mp4``。

    Returns:
        最终视频路径（即输入的 ``main_mp4``）。
    """
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg 未在 PATH 中，无法拼接片头/片尾")

    intro_mp4, outro_mp4 = make_intro_outro(video_dir, duration_s=duration_s)
    tmp_out = video_dir / "demo_final_full.mp4"
    concat_intro_main_outro(intro_mp4, main_mp4, outro_mp4, tmp_out, pad_s=duration_s)
    shutil.move(str(tmp_out), str(main_mp4))
    return main_mp4


def main() -> int:
    """CLI 入口：仅生成 PNG + 3s mp4，方便单独调试卡片样式。"""
    intro_mp4, outro_mp4 = make_intro_outro()
    print(f"[ok] intro -> {intro_mp4}")
    print(f"[ok] outro -> {outro_mp4}")
    main_mp4 = VIDEO_DIR / "demo_final.mp4"
    if main_mp4.exists() and "--concat" in sys.argv:
        wrap_with_intro_outro(main_mp4)
        print(f"[ok] wrapped -> {main_mp4}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
