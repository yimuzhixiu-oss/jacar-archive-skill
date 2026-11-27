#!/bin/bash
# 打包 jacar-archive skill，输出到指定目录，供其他 agent / 机器安装。
#
# 用法：./pack.sh [输出目录]      默认 /Users/flash/Downloads/浏览器
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${1:-/Users/flash/Downloads/浏览器}"
VERSION="$(tr -d '[:space:]' < "$SRC/VERSION")"
ZIP="$OUT_DIR/jacar-archive-v${VERSION}.zip"

mkdir -p "$OUT_DIR"
rm -f "$ZIP"

cd "$(dirname "$SRC")"
zip -r -q "$ZIP" "$(basename "$SRC")" \
  -x "*/__pycache__/*" \
  -x "*.pyc" \
  -x "*/.DS_Store" \
  -x "*/.git/*" \
  -x "*/.jacar/*" \
  -x "*/downloads/*" \
  -x "*/tests/*"

echo "已生成：$ZIP"
echo "体积：$(du -h "$ZIP" | cut -f1)"
echo
echo "内容清单（前 30 项）："
unzip -l "$ZIP" | head -34

# 自检：顶层目录、关键文件、无垃圾
echo
if unzip -l "$ZIP" | grep -q "$(basename "$SRC")/SKILL.md"; then
  echo "✓ SKILL.md 在包内"
else
  echo "✗ 缺少 SKILL.md"; exit 1
fi
if unzip -l "$ZIP" | grep -q "__pycache__"; then
  echo "✗ 包内含 __pycache__"; exit 1
else
  echo "✓ 无 __pycache__"
fi
if unzip -l "$ZIP" | grep -q ".DS_Store"; then
  echo "✗ 包内含 .DS_Store"; exit 1
else
  echo "✓ 无 .DS_Store"
fi
