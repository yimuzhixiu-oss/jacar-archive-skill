"""命名规则：序号推导、簿冊标题切分、路径定位、字符净化。

原则：序号由原文决定，不补零、不去零、不截断；正文一个字节不动。
      仅在"比较是否为同一目录"时做内部归一，落盘一律用原文。
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

FW2HW = str.maketrans("０１２３４５６７８９．", "0123456789.")

# 新字体 → 旧字体（只用于比较，不回写）
VARIANT = str.maketrans({
    "国": "國", "巻": "卷", "録": "錄", "参": "參", "戦": "戰", "総": "總",
    "会": "會", "体": "體", "学": "學", "実": "實", "発": "發", "関": "關",
    "円": "圓", "塩": "鹽", "沢": "澤", "県": "縣", "竜": "龍", "桜": "櫻",
    "稲": "稻", "仏": "佛", "払": "拂", "訳": "譯", "駅": "驛", "険": "險",
    "検": "檢", "験": "驗", "権": "權", "顕": "顯", "芸": "藝", "広": "廣",
    "徳": "德", "恵": "惠", "穂": "穗", "浜": "濱", "庁": "廳", "労": "勞",
    "営": "營", "覚": "覺", "説": "說", "読": "讀", "亜": "亞", "欧": "歐",
})

# 主序号：1–2 位数字，可跟最多若干 .段；不吞空格（正文里的空格要保留）
PREFIX = re.compile(
    r"^([0-9０-９]{1,2}(?![0-9０-９])"
    r"(?:[.．][0-9０-９]{1,2}(?![0-9０-９年]))*)"
    r"[.．]?"
)
SUB = re.compile(r"^([0-9０-９]{1,2})(?![0-9０-９年])")
VOLUME = re.compile(r"^(.*?)[ \u3000]*(第[一二三四五六七八九十百0-9０-９]+[巻卷])$")

ZERO_MAP = {"表紙": "0.", "表纸": "0.", "目次": "0.1", "目錄": "0.1", "目録": "0.1"}

ILLEGAL = {
    "/": "／", "\\": "＼", ":": "：", "*": "＊", "?": "？",
    '"': "＂", "<": "＜", ">": "＞", "|": "｜",
}


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def to_half_digits(s: str) -> str:
    return s.translate(FW2HW)


def variant_key(s: str) -> str:
    """比较键：NFC → 新字体归旧 → 去空白。"""
    return nfc(s).translate(VARIANT).replace(" ", "").replace("\u3000", "")


# 层级目录名专用的补充映射（补 VARIANT 缺的字）
EXTRA_VARIANT = str.maketrans({"罗": "羅", "内": "內", "册": "冊", "会": "會"})

# 目录名里的标点与空白，比较时一律忽略
_PUNCT = re.compile(r"[ \u3000、，,．.·・（）()「」『』〔〕\[\]【】〜~/／\-‐—－:：;；]")

# 搜索时整棵跳过的目录名
SKIP_DIRS = {
    "_処理用", "_作業用", "_工作用", "_tmp", "trash", "Trash",
    "$RECYCLE.BIN", "node_modules", "__pycache__",
}


def hier_key(s: str) -> str:
    """层级目录名比较键：NFC → 全角数字转半角 → 新字体归旧 → 去空白与标点。

    用于把 JACAR 階層（`１門　政治`、`６類　諸外国内政`）与本地目录
    （`1門 政治`、`6類 駐外國內政`）拉到同一口径上比较。
    注意：`諸外` vs `駐外` 属于用词不同，本函数归一不了。
    """
    return _PUNCT.sub("", nfc(s).translate(FW2HW).translate(VARIANT).translate(EXTRA_VARIANT))


def is_noise_dir(name: str, extra: set[str] | None = None) -> bool:
    return name.startswith(".") or name in SKIP_DIRS or bool(extra and name in extra)


# ── 序号 ────────────────────────────────────────────────
def derive_numbering(
    title: str,
    *,
    mode: str = "append",
    zero_prefix: bool = True,
    taken: set[str] | None = None,
    tail_rule: bool = False,
) -> tuple[str, str]:
    """返回 (序号前缀, 正文原文)。

    mode="append"：主序号 + 每个「／N」追加为小数段（默认）
    mode="raw"   ：完全照抄原文序号，不追加
    """
    t = nfc(title).strip(" \u3000")
    taken = taken or set()
    m = PREFIX.match(t)

    if not m:
        if zero_prefix:
            for key, fixed in ZERO_MAP.items():
                if t.startswith(key):
                    return fixed, t
            n = 1
            while f"0.{n}" in taken:
                n += 1
            return f"0.{n}", t
        return "", t

    segs = [to_half_digits(x) for x in re.findall(r"[0-9０-９]{1,2}", m.group(1))]
    rest = t[m.end():]

    if mode == "append":
        subs: list[str] = []
        parts = rest.split("／")
        if len(parts) > 1:
            for prev, nxt in zip(parts, parts[1:]):
                # 优先取「／」前紧邻的数字：３ 〔…〕 ２／标题 → 子项 2
                m_prev = re.search(r"[ \u3000]([0-9０-９]{1,2})[ \u3000]*$", prev)
                if m_prev:
                    subs.append(to_half_digits(m_prev.group(1)))
                    continue
                m_next = SUB.match(nxt)
                subs.append(to_half_digits(m_next.group(1)) if m_next else "0")
        # 启发式：主序号已含多段时不再追加（避免 1.6.1 → 1.6.1.6.1）
        if len(segs) < 2:
            segs += subs

    # 可选：尾号并入（蒟蒻版那类「１ 露国政変概要 １」→ 1.1 + 露国政変概要 1）
    if tail_rule and len(segs) == 1:
        mt = re.search(r"[ \u3000]([0-9０-９])(?![0-9０-９年])$", rest)
        if mt:
            segs.append(to_half_digits(mt.group(1)))
            rest = rest.lstrip(" \u3000")
            rest = re.sub(r"([0-9０-９])$", lambda mo: to_half_digits(mo.group(1)), rest)

    num = ".".join(segs)
    # 序号后不留空格（已有库一律「1.大正…」而非「1. 大正…」）
    rest = rest.lstrip(" \u3000")
    return (num if "." in num or len(segs) > 1 else num + "."), rest


def compose_name(title: str, **kwargs) -> str:
    num, rest = derive_numbering(title, **kwargs)
    return f"{num}{rest}"


def compose_title_body(
    title: str,
    *,
    mode: str = "append",
    tail_rule: bool = False,
    zero_prefix: bool = True,
) -> str:
    """去掉序号前缀后的标题正文，专用于 PDF 文件名。

    与 compose_name 的区别：不拼序号，只留正文；因此 PDF 名不带层级编号。
    不走 profile 的 map —— map 存的是文件夹全名（含序号），这里必须从原文推导。
    """
    _, rest = derive_numbering(
        title, mode=mode, tail_rule=tail_rule, zero_prefix=zero_prefix
    )
    return rest


# ── 按册 profile ────────────────────────────────────────
def load_profile(profile_dir: Path | str | None, fond: str) -> dict:
    """读取 references/profiles/{fond}.json；不存在返回空 dict。"""
    if not profile_dir:
        return {}
    p = Path(profile_dir) / f"{fond}.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def compose_with_profile(
    title: str,
    refcode: str = "",
    profile: dict | None = None,
    taken: set[str] | None = None,
) -> str:
    """profile 里的显式映射优先，其余走默认规则。"""
    profile = profile or {}
    mapped = (profile.get("map") or {}).get(refcode)
    if mapped:
        return mapped
    return compose_name(
        title,
        mode=profile.get("numbering", "append"),
        tail_rule=bool(profile.get("tail_rule", False)),
        zero_prefix=bool(profile.get("zero_prefix", True)),
        taken=taken,
    )


def compose_book_names(items, profile: dict | None = None) -> list[str]:
    """按整册顺序生成目录名。

    无序号条目（表紙/目次/其他）需要跨条目占位，否则每条都会拿到 0.1。
    """
    seen: set[str] = set()
    out: list[str] = []
    for r in items:
        title = getattr(r, "title", "")
        refcode = getattr(r, "refcode", "")
        name = compose_with_profile(title, refcode, profile, taken=seen)
        out.append(name)
        m = PREFIX.match(name)
        if m:
            seen.add(m.group(1).translate(FW2HW).rstrip(".．"))
    return out


# ── 簿冊标题 → 目录段 ────────────────────────────────────
def book_segments(title: str) -> list[str]:
    t = nfc(title).strip(" \u3000")
    segs = [s.strip(" \u3000") for s in t.split("／")]
    out: list[str] = []
    for s in segs:
        if not s:
            continue
        m = VOLUME.match(s)
        if m and m.group(1).strip():
            out.extend([m.group(1).strip(" \u3000"), m.group(2)])
        else:
            out.append(s)
    return [x for x in out if x]


# ── 父项补零 ────────────────────────────────────────────
def apply_parent_padding(names: list[str]) -> list[str]:
    """同册内若存在 `X.N` 子项，则单段父项 `X.` 补成 `X.0`。

    例：同册同时有「2.出兵問題…」与「2.1…／１）在露」，父项应写成
    「2.0出兵問題…」，与旧库的 3.0.1 / 1.0独国 保持同一体例。
    """
    prefixes: list[str | None] = []
    for n in names:
        m = PREFIX.match(n)
        prefixes.append(m.group(1) if m else None)

    has_children: set[str] = set()
    for p in prefixes:
        if not p:
            continue
        core = p.rstrip(".．").translate(FW2HW)
        if "." in core:
            has_children.add(core.split(".")[0])

    out: list[str] = []
    for n, p in zip(names, prefixes):
        if p:
            core = p.rstrip(".．").translate(FW2HW)
            # core == "0" 是无序号条目的占位（0.表紙 / 0.1目次），不参与补零
            if core and core != "0" and "." not in core and core in has_children:
                rest = n[len(p):]
                rest = rest[1:] if rest[:1] in (".", "．") else rest
                out.append(f"{core}.0{rest}")
                continue
        out.append(n)
    return out


# ── 净化与长度 ──────────────────────────────────────────
def sanitize(name: str, *, collapse_spaces: bool = True) -> str:
    s = nfc(name)
    out = []
    for ch in s:
        if ch in ILLEGAL:
            out.append(ILLEGAL[ch])
        elif ord(ch) < 32:
            continue
        else:
            out.append(ch)
    s = "".join(out).strip(" \u3000")
    s = s.rstrip(".")
    if collapse_spaces:
        s = re.sub(r"[ \u3000]{2,}", " ", s)
    return s


def fit_name(name: str, max_bytes: int = 180) -> tuple[str, bool]:
    raw = name.encode("utf-8")
    if len(raw) <= max_bytes:
        return name, False
    cut = name
    while cut and len(cut.encode("utf-8")) > max_bytes - 3:
        cut = cut[:-1]
    return cut + "…", True


# ── 路径定位 ────────────────────────────────────────────
def side_dirs(path: Path, exclude_hidden: bool = True) -> list[Path]:
    if not path.is_dir():
        return []
    items = []
    for p in path.iterdir():
        if not p.is_dir():
            continue
        if exclude_hidden and p.name.startswith("."):
            continue
        items.append(p)
    return items


def match_existing(parent: Path, segment: str) -> Path | None:
    """在 parent 下找"异体字归一后同名"的目录；优先完全一致。"""
    key = variant_key(segment)
    exact = parent / segment
    if exact.is_dir():
        return exact
    for d in side_dirs(parent):
        if variant_key(d.name) == key:
            return d
    return None


def resolve_book_dir(
    base: Path,
    title: str,
    *,
    override: str | None = None,
    dry: bool = True,
) -> tuple[Path, list[str]]:
    """返回 (簿冊目录, 说明信息)。

    base 若已是「件名」级目录（末级与标题首段归一后相同），则丢弃首段。
    """
    notes: list[str] = []
    if override:
        return Path(override), ["按 --book-dir 指定"]

    segs = book_segments(title)
    if not segs:
        return base, ["标题为空，直接落在 base"]

    base_key = variant_key(base.name)
    if len(segs) >= 2 and variant_key(segs[0]) == base_key:
        notes.append(f"首段「{segs[0]}」与 base 同名，已跳过")
        segs = segs[1:]

    cur = base
    for seg in segs:
        hit = match_existing(cur, seg)
        if hit is not None:
            if hit.name != seg:
                notes.append(f"复用已有目录「{hit.name}」（原文为「{seg}」）")
            cur = hit
        else:
            cur = cur / seg
            if dry:
                notes.append(f"将新建「{seg}」")
    return cur, notes
