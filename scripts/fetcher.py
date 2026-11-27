"""下载与生成：CSV 三步导出、PDF 直链、目錄.md。"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from client import BASE, JacarClient
from parser import ViewerEntry, parse_meta, parse_viewer

SKIP_KEYS = {"階層", "URI", "引用例"}

META_ORDER = [
    "件名標題", "レファレンスコード", "所蔵館における請求番号", "言語",
    "作成者名称", "資料作成年月日", "規模", "組織歴/履歴", "内容",
]


# ── CSV ────────────────────────────────────────────────
def export_csv(client: JacarClient, fond: str, dest: Path) -> int:
    """三步导出整册材料清单；返回数据行数（不含表头）。"""
    from client import RateLimited  # 局部导入避免循环

    client.get(f"{BASE}/aj/search?fond={fond}")

    ids: list[str] = []
    try:
        resp = client.post(f"{BASE}/aj/ajax/search/csv", data={"isall": "1", "idaction": ""})
        ids = json.loads(resp.text)
    except (json.JSONDecodeError, ValueError, RateLimited):
        ids = []

    if not ids:
        # 回退：按页面上的 csv-export value 逐条提交
        ids = [f"aj11/{fond}"]

    resp = client.post(f"{BASE}/aj/download", data={"ids": ",".join(ids)})
    resp.raise_for_status()
    body = resp.content
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)          # 原字节落盘，保留 BOM 与引号风格

    try:
        text = body.decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(text)))
        return max(0, len(rows) - 1)
    except UnicodeDecodeError:
        return 0


# ── PDF ────────────────────────────────────────────────
def resolve_pdf_entry(client: JacarClient, refcode: str) -> list[ViewerEntry]:
    html = client.text(f"{BASE}/das/image/{refcode}")
    entries = parse_viewer(html)
    pdfs = [e for e in entries if e.kind == "pdf" and e.path]
    if pdfs:
        return pdfs
    return [e for e in entries if e.path]


def download_pdf(client: JacarClient, refcode: str, dest_dir: Path) -> tuple[Path, str, int]:
    """下载该 refcode 的 PDF。返回 (落盘路径, 官方文件名, 字节数)。

    阅览页偶发 404（后端瞬时故障），重试三次再放弃。
    """
    import time

    entries: list[ViewerEntry] = []
    last: Exception | None = None
    for attempt in range(3):
        try:
            entries = resolve_pdf_entry(client, refcode)
            if entries:
                break
        except Exception as exc:                          # noqa: BLE001
            last = exc
            time.sleep(5 * (attempt + 1))
    if not entries:
        raise last or RuntimeError("阅览页没有给出可下载内容（可能是簿冊或无影像）")

    entry = entries[0]
    url = entry.path if entry.path.startswith("http") else f"{BASE}{entry.path}"
    official = entry.name or f"{refcode}.pdf"
    tmp = dest_dir / f".{refcode}.part"
    size = client.download(url, tmp)

    with open(tmp, "rb") as fh:
        head = fh.read(5)
    if head[:4] != b"%PDF":
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"下载到的不是 PDF（前 5 字节 {head!r}）")

    final = dest_dir / official
    tmp.replace(final)
    return final, official, size


# ── 目錄.md ────────────────────────────────────────────
def fetch_meta(client: JacarClient, refcode: str) -> dict[str, str]:
    return parse_meta(client.text(f"{BASE}/das/meta/{refcode}"))


def build_meta_md(meta: dict[str, str], refcode: str, *, detailed: bool = False) -> str:
    blocks: list[str] = []

    if detailed and meta.get("階層"):
        blocks += ["階層", meta["階層"]]

    for key in META_ORDER:
        val = meta.get(key)
        if val:
            blocks += [key, val]

    blocks += ["URIをコピー", f"{BASE}/das/meta/{refcode}"]

    cite = meta.get("引用例")
    if cite:
        label = "論文などへの引用例" if detailed else "引用例をコピー"
        blocks += [label, cite]

    out: list[str] = []
    for i in range(0, len(blocks), 2):
        label, val = blocks[i], blocks[i + 1]
        out.append(f"{label}\n\n{val}\n")
    return "\n".join(out).rstrip() + "\n"


def write_meta_md(client: JacarClient, refcode: str, dest: Path) -> int:
    meta = fetch_meta(client, refcode)
    dest.write_text(build_meta_md(meta, refcode), encoding="utf-8")
    return len(meta.get("内容", ""))
