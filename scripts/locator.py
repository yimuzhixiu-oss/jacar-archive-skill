"""按簿冊标题在史料库中查找「件名级」目录。

不依赖館/系列/門/類/項 的逐级拼接——JACAR 階層与本地目录存在用词差异
（`６類　諸外国内政` vs `6類 駐外國內政`），逐级走必然断链。
改为：用簿冊标题第一段在库中找同名目录，階層只用于消歧与建议。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from naming import book_segments, hier_key, is_noise_dir


@dataclass(slots=True)
class LocateResult:
    kind: str                        # found | ambiguous | missing
    base: Path | None                # kind == found 时的件名级目录
    candidates: list[Path]           # ambiguous 时的全部候选
    item_name: str                   # 簿冊标题第一段
    notes: list[str] = field(default_factory=list)
    suggested_parent: Path | None = None   # missing 时建议的上级（通常是「項」级）


def _scan(root: Path, max_depth: int, extra_skip: set[str] | None) -> list[Path]:
    """一次遍历收集所有候选目录，跳过噪声目录并限制深度。"""
    root_depth = len(root.parts)
    out: list[Path] = []
    for dirpath, dirnames, _ in os.walk(root):
        cur = Path(dirpath)
        if len(cur.parts) - root_depth >= max_depth:
            dirnames[:] = []
            continue
        dirnames[:] = sorted(d for d in dirnames if not is_noise_dir(d, extra_skip))
        out.extend(cur / d for d in dirnames)
    return out


def _filter_by_hierarchy(cands: list[Path], hierarchy: list[str]) -> list[Path]:
    """多个同名时，用階層最后两级（類、項）挑父目录对得上的。"""
    hints = [hier_key(h) for h in hierarchy[-2:] if h and hier_key(h)]
    if not hints:
        return cands
    out: list[Path] = []
    for c in cands:
        parents = [hier_key(p.name) for p in c.parents]
        if any(h in pk or pk in h for h in hints for pk in parents if pk):
            out.append(c)
    return out


def find_item_dir(
    root: Path,
    title: str,
    hierarchy: list[str],
    *,
    extra_skip: set[str] | None = None,
    max_depth: int = 8,
) -> LocateResult:
    """在 root 下查找簿冊所属的件名级目录。"""
    segs = book_segments(title)
    item = segs[0] if segs else ""
    if not item:
        return LocateResult("missing", None, [], "", ["簿冊标题为空，无法定位"])

    dirs = _scan(root, max_depth, extra_skip)
    by_key: dict[str, list[Path]] = {}
    for d in dirs:
        by_key.setdefault(hier_key(d.name), []).append(d)

    cands = by_key.get(hier_key(item), [])
    if len(cands) == 1:
        return LocateResult("found", cands[0], cands, item,
                            [f"库里「{cands[0].name}」只此一处"])

    if len(cands) > 1:
        filtered = _filter_by_hierarchy(cands, hierarchy)
        if len(filtered) == 1:
            return LocateResult("found", filtered[0], cands, item,
                                [f"库里有 {len(cands)} 处同名，已按階層的類/項锁定"])
        return LocateResult("ambiguous", None, filtered or cands, item,
                            [f"库里有 {len(cands)} 处同名目录，无法自行判断"])

    # 一个都没有：用階層末级（項）给个建议上级
    parent = None
    for want in reversed(hierarchy):
        key = hier_key(want)
        if not key:
            continue
        hits = by_key.get(key, [])
        if len(hits) == 1:
            parent = hits[0]
            break

    note = f"库里还没有「{item}」这个件名目录"
    if parent:
        note += f"，最接近的上级是「{parent.name}」"
    return LocateResult("missing", None, [], item, [note], parent)
