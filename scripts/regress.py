"""回归：用本地已有条目校验命名规则，只读，不改任何文件。

用法：
    python regress.py --base "<史料根目录>" [--delay 2]

输出四级比对结果：L0 完全一致 / L1 仅全半角差异 / L2 仅空格差异 / DIFF 实质差异。
DIFF 不是失败，是需要人工确认的例外（可固化进 profiles/{fond}.json）。
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from client import make_client           # noqa: E402
from naming import (                                  # noqa: E402
    apply_parent_padding, compose_book_names, compose_name, compose_with_profile,
    fit_name, load_profile, nfc, sanitize, variant_key,
)
from parser import REF_RE, parse_search  # noqa: E402

DEFAULT_PROFILES = Path(__file__).resolve().parent.parent / "references" / "profiles"


class _Rec:
    """给 compose_book_names 用的轻量记录。"""

    __slots__ = ("refcode", "title")

    def __init__(self, refcode: str, title: str):
        self.refcode = refcode
        self.title = title

CSV_NAMES = ("選擇材料", "選擇資料", "選擇資料匯總", "所選材料", "選取材料")
META_NAMES = ("目錄.md", "目录.md", "目錄詳細情報.md", "目录詳細情報.md")


def find_csv(book_dir: Path) -> Path | None:
    for p in book_dir.iterdir():
        if p.is_file() and p.suffix.lower() == ".csv" and p.stem in CSV_NAMES:
            return p
    for p in book_dir.glob("*.csv"):
        return p
    return None


def read_book_refcode(csv_path: Path) -> str | None:
    """簿冊行 = 階層段数最少的那一行（簿冊比件名少一级）。"""
    try:
        with open(csv_path, newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
    except (OSError, UnicodeDecodeError):
        return None
    if not rows:
        return None

    def depth(r: dict) -> int:
        hier = r.get("階層") or ""
        return len([x for x in re.split(r"[＞>]", hier) if x.strip()])

    for row in sorted(rows, key=depth):
        ref = (row.get("レファレンスコード") or "").strip()
        if REF_RE.fullmatch(ref):
            return ref
    return None


def item_refcode(d: Path) -> str | None:
    for p in d.iterdir():
        if p.is_file() and p.suffix.lower() == ".pdf":
            m = REF_RE.search(p.name)
            if m:
                return m.group(0)
    for name in META_NAMES:
        f = d / name
        if f.is_file():
            try:
                txt = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            m = re.search(r"レファレンスコード\s*\n+\s*(B\d{11})", txt)
            if m:
                return m.group(1)
            m = REF_RE.search(txt)
            if m:
                return m.group(0)
    return None


def leaf_dirs(root: Path) -> list[Path]:
    out = []
    for p in root.rglob("*"):
        if not p.is_dir() or p.name.startswith("."):
            continue
        if item_refcode(p):
            out.append(p)
    return out


def classify(expected: str, actual: str) -> str:
    if nfc(expected) == nfc(actual):
        return "L0"
    fw = str.maketrans("０１２３４５６７８９", "0123456789")
    if nfc(expected).translate(fw).replace("．", ".") == nfc(actual).translate(fw).replace("．", "."):
        return "L1"
    strip = lambda s: re.sub(r"[ \u3000]", "", nfc(s).translate(fw).replace("．", "."))  # noqa: E731
    if strip(expected) == strip(actual):
        return "L2"
    return "DIFF"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--delay", type=float, default=2.0)
    ap.add_argument("--tail-rule", action="store_true", help="启用尾号并入规则")
    ap.add_argument("--show-l2", action="store_true", help="打印 L2（仅空格差异）样本")
    ap.add_argument("--profiles", default=None, help="profile 目录，默认 skill 自带")
    ap.add_argument("--no-profiles", action="store_true", help="忽略 profile，只看纯规则")
    args = ap.parse_args()

    base = Path(args.base).expanduser()
    if not base.is_dir():
        print(f"目录不存在：{base}")
        return 2

    client = make_client(delay=args.delay)
    profile_dir = None if args.no_profiles else (args.profiles or DEFAULT_PROFILES)
    counts = {"L0": 0, "L1": 0, "L2": 0, "DIFF": 0}
    diffs: list[tuple[str, str, str, str]] = []
    l2samples: list[tuple[str, str, str]] = []
    skipped: list[str] = []
    books_done = 0

    for book_dir in sorted(base.rglob("*")):
        if not book_dir.is_dir() or book_dir.name.startswith("."):
            continue
        csv_path = find_csv(book_dir)
        if not csv_path:
            continue
        fond = read_book_refcode(csv_path)
        if not fond:
            skipped.append(f"{book_dir}（CSV 里找不到簿冊行）")
            continue

        titles: dict[str, str] = {}
        book_ref = ""
        page_no = 1
        while True:
            html = client.text(f"https://www.jacar.archives.go.jp/aj/search?fond={fond}&page={page_no}")
            sp = parse_search(html, page_no)
            for r in sp.records:
                titles[r.refcode] = r.title
                if r.is_book and not book_ref:
                    book_ref = r.refcode
            if not sp.has_next or page_no >= sp.last_page:
                break
            page_no += 1

        books_done += 1
        profile = load_profile(profile_dir, fond) if profile_dir else {}

        # 按 JACAR 检索页顺序生成整册名字：无序号条目必须跨条目占位，
        # 逐条算会全部落到 0.1，与实际落盘不符。簿冊行不参与占位。
        ordered = [(ref, t) for ref, t in titles.items() if ref != book_ref]
        seq = [_Rec(ref, title) for ref, title in ordered]
        names = [fit_name(sanitize(n), 240)[0] for n in compose_book_names(seq, profile)]
        names = apply_parent_padding(names)
        name_by_ref = {ref: n for (ref, _), n in zip(ordered, names)}

        # 全巻一册：标题与册名相同时，实际就落在册目录本身
        book_title = titles.get(book_ref, "")
        for ref, title in ordered:
            if book_title and variant_key(title) == variant_key(book_title):
                name_by_ref[ref] = book_dir.name

        rows: list[tuple[Path, str, str]] = []
        for d in leaf_dirs(book_dir):
            ref = item_refcode(d)
            if not ref or ref not in name_by_ref:
                continue
            rows.append((d, ref, name_by_ref[ref]))

        for d, ref, expected in rows:
            level = classify(expected, d.name)
            counts[level] += 1
            if level == "DIFF":
                diffs.append((ref, titles[ref], expected, d.name))
            elif level == "L2" and args.show_l2 and len(l2samples) < 12:
                l2samples.append((ref, expected, d.name))

    total = sum(counts.values())
    print(f"\n扫描簿冊 {books_done} 个，可比条目 {total} 条\n")
    for k in ("L0", "L1", "L2", "DIFF"):
        pct = (counts[k] / total * 100) if total else 0
        print(f"  {k:4s} {counts[k]:4d} 条  {pct:5.1f}%")
    if skipped:
        print("\n跳过的簿冊：")
        for s in skipped:
            print("  -", s)
    if l2samples:
        print("\nL2 样本（仅空格/全半角差异）：")
        for ref, expected, actual in l2samples:
            print(f"  [{ref}]")
            print(f"    规则: {expected}")
            print(f"    实际: {actual}")
    if diffs:
        print(f"\n实质差异 {len(diffs)} 条（需人工确认，可写入 profiles/）：")
        for ref, title, expected, actual in diffs:
            print(f"\n  [{ref}]")
            print(f"    JACAR原文: {title}")
            print(f"    规则生成 : {expected}")
            print(f"    实际目录 : {actual}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
