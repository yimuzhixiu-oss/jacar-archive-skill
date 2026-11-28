"""中文输出：干跑清单、执行报告、体检结果。

用户不熟悉 dry-run / idempotent 这类术语，所有面向人的文本都用大白话。
"""

from __future__ import annotations

from pathlib import Path


def _rel(p: Path, base: Path) -> str:
    try:
        return str(p.relative_to(base))
    except ValueError:
        return str(p)


def render_locate(res, root: Path, *, fond: str = "", book_title: str = "") -> str:
    """定位结果：找到 / 多个 / 没找到，三种都说清下一步怎么走。"""
    L: list[str] = []
    if book_title:
        L.append(f"这一「册」的名字：{book_title}")
    L.append(f"史料库根目录：{root}")
    L.append(f"要找的文件夹：簿冊标题第一段「{res.item_name}」")
    L.append("")

    if res.kind == "found":
        L.append(f"放置位置：{res.base}")
        for n in res.notes:
            L.append(f"  · {n}")
        return "\n".join(L)

    if res.kind == "ambiguous":
        L.append(f"找到 {len(res.candidates)} 个同名目录，我不替你决定：")
        for c in res.candidates[:10]:
            L.append(f"  · {c}")
        if len(res.candidates) > 10:
            L.append(f"  …… 还有 {len(res.candidates) - 10} 个")
        L.append("")
        L.append("选一个，重跑：")
        for c in res.candidates[:3]:
            L.append(f'  jacar.py plan {fond} --base "{c}"')
        return "\n".join(L)

    L.append("没找到对应的件名目录。")
    for n in res.notes:
        L.append(f"  · {n}")
    if res.suggested_parent:
        target = res.suggested_parent / res.item_name
        L.append("")
        L.append(f"如果就在这儿建，位置会是：{target}")
        L.append("确认后加 --create-missing 重跑：")
        L.append(f"  jacar.py plan {fond} --create-missing")
        L.append("或者先自己把目录建好，再用 --base 指过来。")
    else:
        L.append("")
        L.append("可以用 --base 直接指定位置：")
        L.append(f'  jacar.py plan {fond} --base "<件名级目录>"')
    return "\n".join(L)


def render_plan(plan: dict, base: Path) -> str:
    L: list[str] = []
    L.append("我先把打算做的事念给你听，你点头我才动手。\n")
    L.append(f"你给的地址：{plan['source_url']}")
    L.append(f"这一「册」的名字：{plan['book_title']}")

    loc = plan.get("locate")
    if loc:
        L.append("")
        L.append("放置位置是这么定的：")
        L.append(f"  库根目录：{loc['root']}")
        L.append(f"  依据：簿冊标题第一段「{loc['item_name']}」{loc.get('why', '')}")
        L.append(f"  命中：{loc['base']}")
        L.append("")

    n_items = len(plan["items"])
    L.append(f"材料总数：{plan['total']} 条（第 1 条是这册书本身，只用来起名字和导清单，不能下载）\n")

    book_dir = Path(plan["book_dir"])
    L.append("我会建在这里：")
    L.append(f"  {book_dir}")
    for note in plan.get("notes", []):
        L.append(f"  · {note}")
    existing = plan.get("existing_items", 0)
    if existing:
        L.append(f"  该目录下已有 {existing} 个条目，其中 {plan.get('already_done', 0)} 个与这次要下的是同一个")
    L.append("")

    L.append("逐条这样办（文件夹带序号，PDF 用标题正文）：")
    for it in plan["items"][:40]:
        mark = "已有，跳过" if it.get("skip") else f"下载（{it.get('pages', '?')} 页）"
        pdf_name = it.get("pdf_name") or f"{it['dir_name']}.pdf"
        if it.get("flat"):
            L.append("  📁 （本册只有一个件名且与册名相同，直接放本册目录）")
        else:
            L.append(f"  📁 {it['dir_name'][:46]}")
        L.append(f"     📄 {pdf_name[:46]}")
        L.append(f"        → {mark}")
    if n_items > 40:
        L.append(f"  …… 还有 {n_items - 40} 条")
    L.append("")
    L.append(f"另外导 1 个材料清单：{plan['csv_name']}（{plan['csv_rows']} 行）")
    L.append("")
    if plan.get("warnings"):
        L.append("需要你留意：")
        for w in plan["warnings"]:
            L.append(f"  ⚠ {w}")
        L.append("")
    todo = sum(1 for it in plan["items"] if not it.get("skip"))
    L.append(f"合计：下载 {todo} 个 PDF，跳过 {n_items - todo} 个。")
    L.append('要我开工就说「开始」；哪里不对就告诉我。')
    return "\n".join(L)


def render_run(stats: dict) -> str:
    L: list[str] = []
    L.append("干完了。\n")
    L.append(f"  新建目录 {stats['dirs']} 个")
    L.append(f"  下载文件 {stats['downloaded']} 个（PDF {stats['pdf']} + 清单 {stats['csv']}）")
    L.append(f"  跳过 {stats['skipped']} 个（原本就有）")
    if stats["failed"]:
        L.append(f"  失败 {len(stats['failed'])} 个：")
        for ref, title, why in stats["failed"]:
            L.append(f"    - {title[:40]}（{ref}）：{why}")
    L.append(f"  用时 {stats['elapsed']}")
    if stats["failed"]:
        L.append("\n失败的那几条重跑同一命令就行，已经好的不会重下。")
    return "\n".join(L)


def render_verify(summary: dict) -> str:
    L: list[str] = ["检查结果（只看不改）：\n"]
    L.append(f"  条目目录   {summary['items']:4d} 个")
    L.append(f"  有 PDF     {summary['pdf']:4d} 个")
    L.append(f"  有 目錄.md {summary['meta']:4d} 个")
    if summary.get("empty"):
        L.append(f"  空目录     {summary['empty']:4d} 个")
    if summary.get("junk"):
        L.append(f"  .DS_Store  {summary['junk']:4d} 个（不影响，已忽略）")
    return "\n".join(L)


def render_probe(rows: list[tuple[str, bool, str]], refcode: str, title: str) -> str:
    L = [f"体检对象：{refcode}（{title[:50]}）\n"]
    for i, (label, ok, detail) in enumerate(rows, 1):
        flag = "正常" if ok else "失败"
        L.append(f"  {i}. {label:<12} {flag}：{detail}")
    ok_all = all(r[1] for r in rows)
    L.append("")
    L.append("结论：可以纯后台批量下载，不需要开浏览器。" if ok_all
             else "结论：有环节不通，先别批量跑，把上面失败的那步发我。")
    return "\n".join(L)
