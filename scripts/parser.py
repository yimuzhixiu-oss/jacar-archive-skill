"""页面解析：检索页 → Book/Record；详情页 → 字段字典；阅览页 → 内容列表。

所有解析都遵循"主选择器 → 结构漂移回退 → 明确报错"三层，不静默返回空。
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

REF_RE = re.compile(r"B\d{11}")
DATE_RE = re.compile(r"data-count=\"(\d+)\"")


# ── 数据结构 ────────────────────────────────────────────
@dataclass(slots=True)
class HierNode:
    name: str
    meta_id: str | None = None


@dataclass(slots=True)
class Record:
    refcode: str
    kind: str                      # 簿冊 / 件名 / unknown
    title: str
    hierarchy: list[HierNode]
    summary: dict[str, str]        # 規模 / 所蔵館における請求番号 / 内容 …
    csv_id: str                    # aj11/B03…
    viewer_url: str | None
    page_no: int = 1
    row_no: int = 0

    @property
    def is_book(self) -> bool:
        return self.kind == "簿冊"

    @property
    def content(self) -> str:
        return self.summary.get("内容", "")


@dataclass(slots=True)
class SearchPage:
    records: list[Record]
    total: int
    page_no: int
    has_next: bool
    last_page: int
    drift: list[str] = field(default_factory=list)   # 结构漂移告警


@dataclass(slots=True)
class ViewerEntry:
    content_id: str
    name: str          # 官方文件名
    kind: str          # pdf / image …
    path: str          # /content/item/…
    size: str = ""
    fmt: str = ""


# ── 工具 ────────────────────────────────────────────────
def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def clean(s: str) -> str:
    return nfc(s).strip(" \u3000\n\t\r")


def _label(raw: str) -> str:
    return clean(raw).strip("[]［］ ")


# ── 检索页 ──────────────────────────────────────────────
def parse_search(html: str, page_no: int = 1) -> SearchPage:
    soup = BeautifulSoup(html, "html.parser")
    drift: list[str] = []

    rows = soup.select("li.archive-result-table__body-item")
    records: list[Record] = []

    if rows:
        for idx, row in enumerate(rows, 1):
            kind_el = row.select_one(".cmn-category-label__text")
            title_el = row.select_one(".result-header__title")
            if title_el is None:
                continue
            title = clean(title_el.get_text("", strip=True))

            info = {}
            for it in row.select(".result-info__item"):
                dt, dd = it.select_one("dt"), it.select_one("dd")
                if dt and dd:
                    info[_label(dt.get_text())] = clean(dd.get_text("", strip=True))
            refcode = info.get("レファレンスコード", "")
            if not refcode:
                m = REF_RE.search(row.get_text(" ", strip=True))
                refcode = m.group(0) if m else ""
            if not refcode:
                continue

            summary = {}
            for it in row.select(".result-summary__item"):
                dt, dd = it.select_one("dt"), it.select_one("dd")
                if dt and dd:
                    summary[_label(dt.get_text())] = clean(dd.get_text("", strip=True))

            tree = []
            for a in row.select(".result-tree__item-label a"):
                href = a.get("href", "")
                mid = REF_RE.findall(href)
                tree.append(HierNode(name=clean(a.get_text("", strip=True)),
                                     meta_id=mid[0] if mid else None))

            csv_input = row.select_one("input.csv-export")
            img = row.select_one(".cell-image a.result-image-link")

            records.append(
                Record(
                    refcode=refcode,
                    kind=clean(kind_el.get_text()) if kind_el else "unknown",
                    title=title,
                    hierarchy=tree,
                    summary=summary,
                    csv_id=csv_input.get("value", "") if csv_input else "",
                    viewer_url=img.get("href") if img else None,
                    page_no=page_no,
                    row_no=idx,
                )
            )
    else:
        # 回退：整页扫 refcode，就近取标题
        drift.append("主选择器 0 命中，已降级为全页扫描")
        seen = set()
        for m in REF_RE.finditer(html):
            ref = m.group(0)
            if ref in seen:
                continue
            seen.add(ref)
            snippet = BeautifulSoup(html[max(0, m.start() - 4000): m.start() + 400], "html.parser")
            h3 = snippet.find("h3")
            records.append(
                Record(
                    refcode=ref,
                    kind="unknown",
                    title=clean(h3.get_text("", strip=True)) if h3 else "",
                    hierarchy=[],
                    summary={},
                    csv_id=f"aj11/{ref}",
                    viewer_url=None,
                    page_no=page_no,
                )
            )

    # 总数与分页
    total = 0
    cnt = soup.select_one(".archive-result-header__count-total .count-number")
    if cnt and cnt.get("data-count"):
        total = int(cnt["data-count"])
    else:
        m = DATE_RE.search(html)
        if m:
            total = int(m.group(1))
        else:
            total = len(records)
            drift.append("未能读到检索结果总数，以本页条数代替")

    nums = [int(a.get_text(strip=True)) for a in soup.select("nav.pager a.pager__num")
            if a.get_text(strip=True).isdigit()]
    last_page = max(nums) if nums else 1
    has_next = bool(soup.select_one("nav.pager a.pager__item--next")) or page_no < last_page

    return SearchPage(records=records, total=total, page_no=page_no,
                      has_next=has_next, last_page=last_page, drift=drift)


# ── 详情页 ──────────────────────────────────────────────
FIELD_ALIASES = {
    "件名": "件名標題",
    "階層": "階層",
    "レファレンスコード": "レファレンスコード",
    "所蔵館における請求番号": "所蔵館における請求番号",
    "言語": "言語",
    "作成者名称": "作成者名称",
    "作成者": "作成者名称",
    "資料作成年月日": "資料作成年月日",
    "作成年月日": "資料作成年月日",
    "規模": "規模",
    "画像数": "規模",
    "組織歴/履歴": "組織歴/履歴",
    "内容": "内容",
    "URI": "URI",
}


def parse_meta(html: str) -> dict[str, str]:
    """详情页字段。用 dl/dt→dd 通用扫描，不绑定具体 class。"""
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, str] = {}

    for dt in soup.find_all("dt"):
        key_raw = _label(dt.get_text(" ", strip=True))
        key = FIELD_ALIASES.get(key_raw, key_raw)
        dd = dt.find_next_sibling("dd")
        if not dd:
            parent = dt.parent
            dd = parent.find_next_sibling("dd") if parent else None
        if not dd:
            continue
        # 用空分隔符拼接，避免把 span 边界变成空格；再统一压空白
        raw = dd.get_text("", strip=False)
        val = re.sub(r"\s+", " ", raw).strip()
        if key and val and key not in out:
            out[key] = val

    # 引用例：站点把整句拆成多个 span；页面有两个复制框（URI 在前、引用例在后）
    for box in soup.select(".archive-detail-section__data-copy-input"):
        for p in box.find_all("p"):
            txt = re.sub(r"\s+", " ", p.get_text("", strip=False)).strip()
            if "JACAR" in txt:
                out["引用例"] = txt
                break
        if "引用例" in out:
            break
    if "引用例" not in out:
        m = re.search(r"「[^」]{1,300}」JACAR\(アジア歴史資料センター\)Ref\.[A-Z]\d{11}[^<\n]{0,300}", html)
        if m:
            out["引用例"] = clean(re.sub(r"\s+", " ", m.group(0)))

    return out


# ── 阅览页 ──────────────────────────────────────────────
def _extract_js_array(html: str, varname: str) -> list | None:
    """按括号配平从 `var name = [` 起截取完整 JSON 数组。"""
    idx = html.find(f"var {varname}")
    if idx < 0:
        idx = html.find(varname)
        if idx < 0:
            return None
    start = html.find("[", idx)
    if start < 0:
        return None

    depth = 0
    in_str = False
    quote = ""
    esc = False
    for i in range(start, len(html)):
        ch = html[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
            continue
        if ch in "\"'":
            in_str, quote = True, ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[start: i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def parse_viewer(html: str) -> list[ViewerEntry]:
    data = _extract_js_array(html, "najContentList")
    if data is None:
        return []

    out: list[ViewerEntry] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        path = ""
        source = item.get("source") or {}
        if isinstance(source, dict):
            items = source.get("items") or []
            if items and isinstance(items[0], dict):
                path = (items[0].get("source") or {}).get("id") or ""
        path = path or item.get("path") or ""

        cid = str(item.get("id") or item.get("contentId") or "")
        name = item.get("contentName") or item.get("name") or ""
        kind = (item.get("type") or item.get("format") or "").lower()
        if "pdf" in kind:
            kind = "pdf"
        elif "image" in kind or kind.endswith(("jpg", "jpeg", "png", "tif", "tiff")):
            kind = "image"

        if path or name:
            out.append(ViewerEntry(content_id=cid, name=name, kind=kind,
                                   path=path, size=str(item.get("size") or ""),
                                   fmt=str(item.get("format") or "")))
    return out
