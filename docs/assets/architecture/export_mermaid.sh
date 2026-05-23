#!/usr/bin/env bash
# 从 README 与 docs/0*.md 提取关键 mermaid 代码块并渲染为 PNG + SVG。
# 用法：bash docs/assets/architecture/export_mermaid.sh
#
# 渲染清单：每条形如 "源文件:行号:输出名"，行号是 ```mermaid 标记所在行。

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
OUT="$ROOT/docs/assets/architecture"
mkdir -p "$OUT"
cd "$ROOT"

# 关键 mermaid 块清单
TARGETS=(
  "README.md:60:system_overview"
  "docs/02_需求分析与系统设计.md:17:usecase"
  "docs/02_需求分析与系统设计.md:165:overall_architecture"
  "docs/02_需求分析与系统设计.md:312:er_diagram"
  "docs/02_需求分析与系统设计.md:331:task_state_machine"
  "docs/02_需求分析与系统设计.md:358:search_pipeline"
)

extract_block() {
  # $1 = 文件，$2 = ```mermaid 起始行（含此行），写到 stdout
  awk -v start="$2" 'NR>=start { if (NR==start) {next}; if ($0=="```") exit; print }' "$1"
}

for t in "${TARGETS[@]}"; do
  IFS=':' read -r file line name <<< "$t"
  echo "[render] $name from $file:$line"
  mmd="$OUT/$name.mmd"
  png="$OUT/$name.png"
  svg="$OUT/$name.svg"
  extract_block "$file" "$line" > "$mmd"
  mmdc -i "$mmd" -o "$png" -w 1600 -b transparent --quiet 2>&1 | tail -2 || true
  mmdc -i "$mmd" -o "$svg" -b transparent --quiet 2>&1 | tail -2 || true
done

echo ""
echo "[done] outputs:"
ls -lh "$OUT" | grep -E '\.(png|svg|mmd)$'
