#!/usr/bin/env python3
"""JACAR 史料批量下载 —— 命令行入口。

    jacar.py probe  <URL或fond号>
    jacar.py plan   <URL或fond号> --base <目录>
    jacar.py run    --plan <plan.json> --confirm
    jacar.py verify --base <目录>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import report                                              # noqa: E402
from client import BASE, RateLimited, make_client          # noqa: E402
from fetcher import download_pdf, export_csv, write_meta_md  # noqa: E402
from locator import find_item_dir                          # noqa: E402
from naming import (                                       # noqa: E402
    apply_parent_padding, compose_book_names, compose_title_body,
    compose_with_profile, fit_name, load_profile, resolve_book_dir, sanitize,
    to_old_style, variant_key,
)
from parser import REF_RE, parse_search                    # noqa: E402
from store import AlreadyRunning, Manifest                 # noqa: E402

SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROFILES = SKILL_ROOT / "references" / "profiles"
DEFAULT_CSV = "選擇材料.csv"
DEFAULT_META = "目錄.md"


# ── 基础 ────────────────────────────────────────────────
def parse_target(target: str) -> str:
    m = re.search(r"[?&]fond=([A-Za-z0-9]+)", target)
    if m:
        return m.group(1)
    if REF_RE.fullmatch(target.strip()):
        return target.strip()
    raise SystemExit(f"看不懂这个地址，需要 fond 参数或 B 开头的 11 位编号：{target}")


def fetch_all(client, fond: str) -> tuple[list, int]:
    records, total, page = [], 0, 1
    while True:
        html = client.text(f"{BASE}/aj/search?fond={fond}&page={page}")
        sp = parse_search(html, page)
        records.extend(sp.records)
        total = sp.total
        if not sp.has_next or page >= sp.last_page:
            break
        page += 1
    return records, total


def split_book(records: list) -> tuple[object | None, list]:
    book = next((r for r in records if r.is_book), records[0] if records else None)
    items = [r for r in records if r is not book]
    return book, items


def existing_refcodes(d: Path) -> set[str]:
    if not d.is_dir():
        return set()
    out: set[str] = set()
    for p in d.rglob("*"):
        if p.is_file() and p.suffix.lower() == ".pdf":
            m = REF_RE.search(p.name)
            if m:
                out.add(m.group(0))
    return out


def dir_refcodes(d: Path, meta_name: str = DEFAULT_META, recursive: bool = True) -> set[str]:
    """目录里能认定的 refcode：先看 PDF 名，再看 目錄.md 里的字段。

    recursive=False 只看本层，用于「全巻一册」扁平存放的场合，
    避免把子目录里别的条目的 refcode 误算进来。
    """
    out: set[str] = set()
    if not d.is_dir():
        return out
    walker = d.rglob("*") if recursive else d.iterdir()
    for p in walker:
        if p.is_file() and p.suffix.lower() == ".pdf":
            m = REF_RE.search(p.name)
            if m:
                out.add(m.group(0))
    meta = d / meta_name
    if meta.is_file():
        try:
            txt = meta.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"レファレンスコード\s*\n+\s*(B\d{11})", txt)
            if m:
                out.add(m.group(1))
        except OSError:
            pass
    return out


# ── probe ───────────────────────────────────────────────
def cmd_probe(args) -> int:
    fond = parse_target(args.target)
    client = make_client(delay=args.delay)
    rows: list[tuple[str, bool, str]] = []

    records, total = fetch_all(client, fond)
    book, items = split_book(records)
    if not items:
        print("这个簿冊下面没有可下载的条目。")
        return 1
    first = items[0]

    html = client.text(f"{BASE}/das/meta/{first.refcode}")
    ok = "レファレンスコード" in html or first.refcode in html
    rows.append(("详情页", ok, "字段齐全" if ok else "读不到字段"))

    from fetcher import resolve_pdf_entry
    try:
        entries = resolve_pdf_entry(client, first.refcode)
        rows.append(("阅览页", bool(entries), f"{len(entries)} 个内容项"))
    except Exception as exc:                                # noqa: BLE001
        rows.append(("阅览页", False, str(exc)[:60]))
        entries = []

    if entries:
        e = entries[0]
        rows.append(("官方 PDF", e.kind == "pdf", f"{e.name}（{e.size or '?'}）"))
        url = e.path if e.path.startswith("http") else f"{BASE}{e.path}"
        try:
            r = client.get(url, headers={"Range": "bytes=0-1023"})
            is_pdf = r.status_code in (200, 206) and r.content[:4] == b"%PDF"
            rows.append(("直接下载", is_pdf, f"HTTP {r.status_code}，前 4 字节 {r.content[:4]!r}"))
            # 206 本身就证明服务器接受了 Range，即使没声明 accept-ranges
            ranges = r.headers.get("accept-ranges") or ""
            support = r.status_code == 206 or "bytes" in ranges.lower()
            rows.append(("断点续传", support, "支持（返回 206）" if r.status_code == 206 else (ranges or "不支持")))
        except Exception as exc:                            # noqa: BLE001
            rows.append(("直接下载", False, str(exc)[:60]))

    print(report.render_probe(rows, first.refcode, first.title))
    print(f"\n（这一册共 {total} 条，其中 {len(items)} 条要下载）")
    return 0


# ── locate（只读，不写任何文件）──────────────────────────
def cmd_locate(args) -> int:
    fond = parse_target(args.target)
    root = _resolve_root(args, load_config())
    if root is None:
        raise SystemExit(
            '没有库根目录。先设一次：jacar.py config --root "<史料库最外层目录>"'
        )

    client = make_client(delay=args.delay)
    records, _ = fetch_all(client, fond)
    book, _items = split_book(records)
    if book is None:
        raise SystemExit("检索页没有任何结果。")

    res = find_item_dir(root, book.title, [n.name for n in book.hierarchy])
    print(report.render_locate(res, root, fond=fond, book_title=book.title))
    return 0 if res.kind == "found" else 2


# ── plan ────────────────────────────────────────────────
def _resolve_root(args, cfg: dict) -> Path | None:
    raw = getattr(args, "root", None) or cfg.get("root")
    if not raw:
        return None
    p = Path(raw).expanduser().resolve()
    return p if p.is_dir() else None


def cmd_plan(args) -> int:
    fond = parse_target(args.target)
    cfg = load_config()
    root = _resolve_root(args, cfg)

    explicit_base: Path | None = None
    # 命令行 --base 优先；只有在没配 root 时才退回配置里的 base
    base_arg = args.base or (cfg.get("base") if root is None else None)
    if base_arg:
        explicit_base = Path(base_arg).expanduser().resolve()
        if not explicit_base.is_dir():
            raise SystemExit(f"base 目录不存在：{explicit_base}")

    if explicit_base is None and root is None:
        raise SystemExit(
            "没有指定 --base，本机也没配史料库根目录。二选一：\n"
            '  jacar.py config --root "<史料库最外层目录>"    # 以后自动定位\n'
            '  jacar.py plan <target> --base "<件名级目录>"'
        )

    client = make_client(delay=args.delay)
    records, total = fetch_all(client, fond)
    book, items = split_book(records)
    if book is None:
        raise SystemExit("检索页没有任何结果。")

    warnings: list[str] = []
    locate_info = None

    if explicit_base is not None:
        base = explicit_base
    else:
        # 自动定位：用簿冊标题第一段在库中找同名件名目录
        res = find_item_dir(root, book.title, [n.name for n in book.hierarchy])
        if res.kind == "uncertain" and not getattr(args, "accept", False):
            print(report.render_locate(res, root, fond=fond, book_title=book.title))
            return 2
        if res.kind == "ambiguous":
            print(report.render_locate(res, root, fond=fond, book_title=book.title))
            return 2
        if res.kind == "missing":
            allow_new = getattr(args, "create_missing", False)
            if not allow_new or res.suggested_parent is None:
                print(report.render_locate(res, root, fond=fond, book_title=book.title))
                return 2
            base = res.suggested_parent / sanitize(
                to_old_style(res.item_name) if cfg.get("oldstyle_item_names") else res.item_name
            )
            warnings.append(f"「{res.item_name}」目录不存在，按 --create-missing 新建")
        else:
            base = res.base
        locate_info = {
            "mode": "auto",
            "root": str(root),
            "item_name": res.item_name,
            "base": str(base),
            "why": ("，" + "；".join(res.notes)) if res.notes else "",
        }

    profile = load_profile(args.profiles or DEFAULT_PROFILES, fond)
    book_dir, notes = resolve_book_dir(base, book.title, override=args.book_dir)

    if not book_dir.exists():
        warnings.append(f"「{book_dir.name}」还不存在，会新建")

    scoped = items[: args.limit] if args.limit else items

    # 第一遍：只算目录名，为「父项补零」准备整册上下文
    base_names: list[str] = []
    for r, n in zip(scoped, compose_book_names(scoped, profile)):
        n = sanitize(n)
        n, truncated = fit_name(n, args.max_name_bytes)
        if truncated:
            warnings.append(f"名字超长已截断：{r.title[:30]}…")
        base_names.append(n)

    # 同册里存在 X.N 子项时，把 X. 补成 X.0
    names = apply_parent_padding(base_names)

    plans, taken = [], set()
    for r, name in zip(scoped, names):
        if name in taken:
            n = 2
            while f"{name}__{n}" in taken:
                n += 1
            warnings.append(f"同一册里出现重名，已加后缀 __{n}：{name[:24]}")
            name = f"{name}__{n}"
        taken.add(name)

        # PDF 名 = 去掉序号前缀的标题正文（与文件夹不同名）
        if args.pdf_name == "dir":
            pdf_name = f"{name}.pdf"
        else:
            body = compose_title_body(
                r.title,
                mode=profile.get("numbering", "append"),
                tail_rule=bool(profile.get("tail_rule", False)),
                zero_prefix=bool(profile.get("zero_prefix", True)),
            )
            pdf_name = sanitize(body) + ".pdf"
            pdf_name, pdf_truncated = fit_name(pdf_name, args.max_name_bytes)
            if pdf_truncated:
                warnings.append(f"PDF 名超长已截断：{r.title[:30]}…")
            if pdf_name == ".pdf":
                pdf_name = f"{r.refcode}.pdf"
                warnings.append(f"标题没有正文，PDF 改用编号命名：{r.refcode}")

        # 全巻一册：件名标题与簿冊同名时，不再多套一层子目录
        is_flat = variant_key(r.title) == variant_key(book.title)
        d = book_dir if is_flat else book_dir / name
        skip = d.is_dir() and (r.refcode in dir_refcodes(d, args.meta_name, not is_flat))
        plans.append({
            "refcode": r.refcode,
            "title": r.title,
            "dir_name": name,
            "dir": str(d),
            "flat": is_flat,
            "pdf_name": pdf_name,
            "pdf": str(d / pdf_name),
            "pages": r.summary.get("規模") or r.summary.get("画像数") or "",
            "content_chars": len(r.content),
            "skip": skip,
        })

    csv_path = book_dir / args.csv_name
    book_existing = existing_refcodes(book_dir)
    plan = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "schema": 2,
        "locate": locate_info,
        "source_url": f"{BASE}/aj/search?fond={fond}",
        "fond": fond,
        "book_title": book.title,
        "book_dir": str(book_dir),
        "book_dir_notes": notes,
        "notes": notes,
        "total": total,
        "csv_path": str(csv_path),
        "csv_name": args.csv_name,
        "csv_rows": total,
        "meta_name": args.meta_name,
        "existing_items": len({p.parent for p in book_dir.rglob("*.pdf")}) if book_dir.is_dir() else 0,
        "already_done": sum(1 for p in plans if p["skip"]),
        "warnings": warnings,
        "items": plans,
    }

    out = base / ".jacar"
    out.mkdir(parents=True, exist_ok=True)
    plan_file = out / f"plan-{fond}.json"
    plan_file.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    print(report.render_plan(plan, base))
    print(f"\n（清单已存到 {plan_file}）")
    return 0


# ── run ─────────────────────────────────────────────────
def cmd_run(args) -> int:
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    # plan 存在 {base}/.jacar/ 下，其父目录就是 base
    jacar_dir = Path(args.plan).resolve().parent
    root = jacar_dir.parent

    if not args.confirm:
        print("这是预演，什么都没动。真跑请加 --confirm。")
        for it in plan["items"]:
            print(f"  {it['dir_name']}")
        return 0

    client = make_client(delay=args.delay)
    book_dir = Path(plan["book_dir"])
    book_dir.mkdir(parents=True, exist_ok=True)

    # 以磁盘为准建立 refcode → 目录 映射：plan 只是快照，目录被改名过也能认出来
    ref_to_dir: dict[str, Path] = {}
    for d in book_dir.rglob("*"):
        if d.is_dir() and not d.name.startswith("."):
            for r in dir_refcodes(d, plan["meta_name"]):
                ref_to_dir.setdefault(r, d)

    stats = {"dirs": 0, "downloaded": 0, "pdf": 0, "csv": 0, "skipped": 0,
             "failed": [], "elapsed": ""}
    t0 = time.monotonic()

    try:
        with Manifest(root) as mf:
            mf.book(plan["fond"]).update({
                "title": plan["book_title"], "dir": str(book_dir), "source": plan["source_url"],
            })
            mf.save()

            # 1) CSV
            csv_path = Path(plan["csv_path"])
            if csv_path.is_file() and csv_path.stat().st_size > 0:
                stats["skipped"] += 1
                print(f"清单已有，跳过：{csv_path.name}")
            else:
                try:
                    rows = export_csv(client, plan["fond"], csv_path)
                    stats["csv"] = 1
                    stats["downloaded"] += 1
                    print(f"清单已导出：{csv_path.name}（{rows} 行）")
                except Exception as exc:                    # noqa: BLE001
                    stats["failed"].append((plan["fond"], "材料清单", str(exc)[:60]))

            # 2) 逐条
            total = len(plan["items"])
            for i, it in enumerate(plan["items"], 1):
                ref = it["refcode"]
                d = ref_to_dir.get(ref) or Path(it["dir"])
                tag = f"[{i}/{total}]"

                # 实时判幂等：目录里能认定该 refcode，且目錄.md 在，才跳过
                is_flat = bool(it.get("flat"))
                already = d.is_dir() and (
                    ref in dir_refcodes(d, plan["meta_name"], not is_flat)
                )
                if already:
                    stats["skipped"] += 1
                    print(f"{tag} {it['dir_name'][:36]}  已经有了，跳过")
                    continue

                try:
                    if not d.is_dir():
                        d.mkdir(parents=True, exist_ok=True)
                        stats["dirs"] += 1

                    pdf_path, official, size = download_pdf(client, ref, d)
                    pdf_name = it.get("pdf_name") or f"{it['dir_name']}.pdf"
                    target = d / pdf_name
                    if target.exists() and target.stat().st_size == size:
                        pdf_path.unlink(missing_ok=True)   # 同内容，保留已有
                    elif pdf_path != target:
                        if target.exists():
                            target.unlink()                # 同一条目重下，覆盖旧件
                        pdf_path.rename(target)
                    stats["pdf"] += 1
                    stats["downloaded"] += 1

                    meta_path = d / plan["meta_name"]
                    chars = write_meta_md(client, ref, meta_path)
                    print(f"{tag} {it['dir_name'][:36]}  {size // 1024} KB ✓  目錄 {chars} 字")

                    mf.mark_item(ref, fond=plan["fond"], dir=str(d), pdf=str(target),
                                 official=official, pdf_bytes=size, pages=it.get("pages"),
                                 meta=str(meta_path), title_raw=it["title"], state="done")
                except Exception as exc:                    # noqa: BLE001
                    stats["failed"].append((ref, it["title"], str(exc)[:80]))
                    mf.mark_item(ref, fond=plan["fond"], state="failed", error=str(exc)[:200])
                    print(f"{tag} {it['dir_name'][:36]}  失败：{str(exc)[:60]}")
    except AlreadyRunning as exc:
        print(str(exc))
        return 3

    elapsed = time.monotonic() - t0
    stats["elapsed"] = f"{int(elapsed // 60)} 分 {int(elapsed % 60)} 秒" if elapsed >= 60 else f"{int(elapsed)} 秒"
    print()
    print(report.render_run(stats))
    return 0 if not stats["failed"] else 1


# ── rename-pdfs ─────────────────────────────────────────
META_FILES = ("目錄.md", "目录.md", "目錄詳細情報.md")


def _find_entry_dirs(root: Path):
    for d in sorted(root.rglob("*")):
        if not d.is_dir() or d.name.startswith("."):
            continue
        if any((d / n).is_file() for n in META_FILES):
            yield d


def _find_profile_for_book(book: Path):
    """从 book 上层的 .jacar/plan-*.json 反查 fond，取对应 profile。"""
    for parent in book.parents:
        jd = parent / ".jacar"
        if not jd.is_dir():
            continue
        for pf in jd.glob("plan-*.json"):
            try:
                pj = json.loads(pf.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if Path(pj.get("book_dir", "")).resolve() == book:
                return pj.get("fond", ""), load_profile(DEFAULT_PROFILES, pj.get("fond", ""))
    return "", {}


def cmd_rename_pdfs(args) -> int:
    book = Path(args.book).expanduser().resolve()
    if not book.is_dir():
        raise SystemExit(f"目录不存在：{book}")

    fond, profile = _find_profile_for_book(book)
    if fond:
        print(f"按 {fond} 的 profile 处理（tail_rule={profile.get('tail_rule', False)}）")
    else:
        print("未找到对应的 profile，按默认规则处理")

    todo = []
    for d in _find_entry_dirs(book):
        meta = next((d / n for n in META_FILES if (d / n).is_file()), None)
        pdfs = sorted(p for p in d.glob("*.pdf"))
        if not meta or not pdfs:
            continue
        txt = meta.read_text(encoding="utf-8", errors="replace")
        mt = re.search(r"件名標題\s*\n+\s*(.+)", txt)
        mr = re.search(r"レファレンスコード\s*\n+\s*(B\d{11})", txt)
        if not (mt and mr):
            continue
        new = sanitize(compose_title_body(
            mt.group(1).strip(),
            mode=profile.get("numbering", "append"),
            tail_rule=bool(profile.get("tail_rule", False)),
            zero_prefix=bool(profile.get("zero_prefix", True)),
        )) + ".pdf"
        if new == ".pdf":
            new = f"{mr.group(1)}.pdf"
        new, _ = fit_name(new, 240)
        if len(pdfs) > 1:
            print(f"  跳过（目录内有 {len(pdfs)} 个 PDF）：{d.name[:40]}")
            continue
        old = pdfs[0]
        if old.name == new:
            continue
        todo.append((d, old, new))

    if not todo:
        print("\n没有需要改名的 PDF。")
        return 0

    print(f"\n{'将' if not args.confirm else ''}改名 {len(todo)} 个：\n")
    for d, old, new in todo:
        print(f"  目录：{d.name[:50]}")
        print(f"  旧名：{old.name}")
        print(f"  新名：{new}")
        if args.confirm:
            target = d / new
            if target.exists():
                print("  ! 目标已存在，跳过")
                continue
            old.rename(target)
            print("  ✓ 已改名")
        print()

    if not args.confirm:
        print("这是预演，什么都没动。确认无误就加 --confirm 执行。")
    else:
        print(f"完成：改名 {len(todo)} 个")
    return 0


# ── verify ──────────────────────────────────────────────
def cmd_verify(args) -> int:
    if not args.base:
        raise SystemExit("没有指定 --base，本机也还没存默认值（jacar.py config --base …）。")
    base = Path(args.base).expanduser().resolve()
    if not base.is_dir():
        raise SystemExit(f"目录不存在：{base}")

    items = pdf = meta = empty = junk = 0
    for d in base.rglob("*"):
        if not d.is_dir() or d.name.startswith(".") or ".jacar" in d.parts:
            continue
        files = [p for p in d.iterdir() if p.is_file()]
        pdfs = [p for p in files if p.suffix.lower() == ".pdf"]
        metasf = [p for p in files if p.name in ("目錄.md", "目录.md", "目錄詳細情報.md")]
        if pdfs or metasf:
            items += 1
            pdf += bool(pdfs)
            meta += bool(metasf)
        elif not files:
            empty += 1
    junk = sum(1 for _ in base.rglob(".DS_Store"))

    print(report.render_verify({"items": items, "pdf": pdf, "meta": meta,
                                "empty": empty, "junk": junk}))
    if args.regress:
        print("\n—— 附带跑一次命名回归 ——")
        import subprocess
        cmd = [sys.executable, str(Path(__file__).with_name("regress.py")), "--base", str(base)]
        subprocess.run(cmd, check=False)
    return 0


# ── 本机配置 ────────────────────────────────────────────
CONFIG_DIR = Path.home() / ".jacar"
CONFIG_PATH = CONFIG_DIR / "config.json"

CONFIG_KEYS = {
    "root": "史料库最外层目录（自动定位的起点）",
    "base": "固定件名级目录（设了就不自动定位，一般留空）",
    "oldstyle_item_names": "新建件名目录时转成旧字体（与库里既有命名一致）",
    "delay": "请求间隔秒数",
    "csv_name": "材料清单文件名",
    "meta_name": "目錄文件名",
    "pdf_name": "PDF 命名方式：body=标题正文 / dir=与文件夹同名",
    "max_name_bytes": "单个名字字节上限",
    "book_dir": "固定的簿冊目录（可空，空则按标题自动推导）",
}


def load_config() -> dict:
    if not CONFIG_PATH.is_file():
        return {}
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_PATH)


def cmd_config(args) -> int:
    cfg = load_config()

    if args.reset:
        if CONFIG_PATH.is_file():
            CONFIG_PATH.unlink()
        print("已清除本机配置，之后需要每次传参数。")
        return 0

    if getattr(args, "unset", None):
        key = args.unset
        if key in cfg:
            cfg.pop(key)
            save_config(cfg)
            print(f"已从配置里删掉 {key}。")
        else:
            print(f"配置里没有 {key}。")
        return 0

    changed = False
    for key in CONFIG_KEYS:
        val = getattr(args, key, None)
        if val is not None:
            cfg[key] = val
            changed = True
    if changed:
        # 首次保存时把其余默认值一并写入，方便查看和逐项修改
        for k, v in {
            "csv_name": DEFAULT_CSV,
            "meta_name": DEFAULT_META,
            "pdf_name": "body",
            "max_name_bytes": 240,
            "delay": 3.0,
        }.items():
            cfg.setdefault(k, v)
        save_config(cfg)

    if not cfg:
        print("还没存任何设置。可以这样设一次：\n")
        print('  jacar.py config --base "<簿冊所在的「件名」级目录>" --delay 2')
        return 0

    print(("已保存。\n\n" if changed else "") + f"配置文件：{CONFIG_PATH}\n")
    for k, desc in CONFIG_KEYS.items():
        if k in cfg:
            print(f"  {k:16s} = {cfg[k]}")
            print(f"  {'':16s}   {desc}")
    if cfg.get("root") and cfg.get("base"):
        print("\n注意：`base` 在的话会跳过自动定位；想用 root 自动找位置就 "
              "`config --unset base`。")
    return 0


# ── CLI ─────────────────────────────────────────────────
def main() -> int:
    cfg = load_config()

    ap = argparse.ArgumentParser(prog="jacar.py", description="JACAR 史料批量下载")
    ap.add_argument("--delay", type=float, default=float(cfg.get("delay", 3.0)),
                    help=f"每次请求间隔秒数，默认 {cfg.get('delay', 3.0)}")
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=60)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe", help="体检：一条链路通不通")
    p.add_argument("target")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("locate", help="只看会自动定位到哪，不写任何文件")
    p.add_argument("target")
    p.add_argument("--root", default=None, help="史料库最外层目录，覆盖配置")
    p.set_defaults(func=cmd_locate)

    p = sub.add_parser("plan", help="干跑：只算不动手，出清单")
    p.add_argument("target")
    p.add_argument("--base", default=None,
                   help="件名级目录；不传则按库根目录自动定位")
    p.add_argument("--root", default=None, help="史料库最外层目录，覆盖配置")
    p.add_argument("--create-missing", action="store_true",
                   help="件名目录不存在时，允许按建议位置新建")
    p.add_argument("--accept", action="store_true",
                   help="自动定位与階層有出入时，仍按定位结果继续")
    p.add_argument("--book-dir", default=cfg.get("book_dir"),
                   help="直接指定簿冊目录，覆盖自动推导")
    p.add_argument("--csv-name", default=cfg.get("csv_name", DEFAULT_CSV))
    p.add_argument("--meta-name", default=cfg.get("meta_name", DEFAULT_META))
    p.add_argument("--max-name-bytes", type=int,
                   default=int(cfg.get("max_name_bytes", 240)))
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--profiles", default=None)
    p.add_argument("--pdf-name", choices=["body", "dir"],
                   default=cfg.get("pdf_name", "body"),
                   help="PDF 文件名：body=标题正文（默认），dir=与文件夹同名")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("rename-pdfs", help="把已下载的 PDF 改成标题正文命名")
    p.add_argument("--book", required=True, help="簿冊目录（其下逐条递归）")
    p.add_argument("--confirm", action="store_true", help="真的执行，否则只预演")
    p.set_defaults(func=cmd_rename_pdfs)

    p = sub.add_parser("run", help="照单执行")
    p.add_argument("--plan", required=True)
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("verify", help="只读检查既有库")
    p.add_argument("--base", default=cfg.get("base"))
    p.add_argument("--regress", action="store_true")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("config", help="查看或修改本机默认设置")
    p.add_argument("--root", default=None, help="史料库最外层目录")
    p.add_argument("--base", default=None)
    p.add_argument("--delay", type=float, default=None)
    p.add_argument("--csv-name", default=None)
    p.add_argument("--meta-name", default=None)
    p.add_argument("--pdf-name", choices=["body", "dir"], default=None)
    p.add_argument("--max-name-bytes", type=int, default=None)
    p.add_argument("--book-dir", default=None)
    p.add_argument("--oldstyle-item-names", action="store_true", default=None,
                   help="新建件名目录时转成旧字体")
    p.add_argument("--unset", default=None, metavar="键名", help="删除某一项设置")
    p.add_argument("--reset", action="store_true", help="清除全部设置")
    p.set_defaults(func=cmd_config)

    args = ap.parse_args()
    try:
        return args.func(args)
    except RateLimited as exc:
        print(f"\n站点限流，已停止：{exc}")
        return 4
    except KeyboardInterrupt:
        print("\n已中断。重跑同一命令会接着做，已完成的不会重下。")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
