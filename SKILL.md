---
name: jacar-archive
description: >-
  批量下载日本亚洲历史资料中心（JACAR / アジア歴史資料センター）某一「簿冊」下的全部「件名」史料：
  自动建多级目录（文件夹带序号）、导出選擇材料.csv、下载 PDF（文件用标题正文、不带序号）、
  生成目錄.md，支持翻页与断点续跑。
  当用户提到 JACAR、亚洲历史资料中心、アジア歴史資料センター、外務省外交史料館、
  国立公文書館、防卫研究所史料、或给出 jacar.archives.go.jp/aj/search?fond=… 链接并要求
  下载/整理/归档史料时使用。也用于修复既有史料库的命名与缺失元数据。
metadata:
  version: 1.0.0
  requires:
    binaries: [python3]
    python: [requests, beautifulsoup4, Pillow]
---

# JACAR 史料批量归档

把「逐条点开 → 下载 → 改名 → 建目录 → 抄目录」压缩成一条命令。
**默认只出清单不动手，用户点头后才下载。**

## 何时用

- 用户给了一个 `https://www.jacar.archives.go.jp/aj/search?fond=B…` 链接，想要里面的全部资料
- 用户要整理已下载的 JACAR 史料（命名不统一、缺 目錄.md）
- 用户在做近代日本对外关系史的研究，需要某一簿冊的完整影像与元数据

## 先跑这一步

```bash
PY=$(for p in /opt/homebrew/bin/python3 python3; do
  $p -c "import requests,bs4,PIL" 2>/dev/null && echo $p && break; done)
[ -z "$PY" ] && echo "缺依赖：pip install --user requests beautifulsoup4 Pillow"
```

脚本目录：`~/.workbuddy/skills/jacar-archive/scripts/`

## 四步流程

### 1. 体检（约 10 秒）

```bash
$PY scripts/jacar.py probe <链接或fond号>
```

确认详情页、阅览页、官方 PDF 直链都通。任一步失败就先停下，把输出给用户看。

### 2. 干跑出清单

```bash
$PY scripts/jacar.py plan <链接或fond号>
```

只要配过一次库根目录（`config --root`），这一步不用再传路径——脚本会按簿冊标题
第一段在库里找对应的「件名」目录，并把"依据什么、定到哪"打在清单开头。

找不到唯一结果时它会停下来问你，不会自己猜：

| 情况 | 表现 |
| --- | --- |
| 找到唯一一个，且与階層对得上 | 正常出清单，开头写明定位依据 |
| 找到唯一，但与階層有出入 | 列出对不上的地方，等你拍板；确认无误加 `--accept` 继续 |
| 找到多个同名 | 列出候选 + 可复制的 `--base` 命令，退出码 2 |
| 库里没有这个件名 | 给出建议位置，提示加 `--create-missing` 或自己建好再用 `--base` |

**原则：任何对不上的地方都交给你决定，脚本不替你拍板。**

想先看看会定到哪、不产生任何文件：`$PY scripts/jacar.py locate <链接或fond号>`

把输出的中文清单**原样转述给用户**，等他确认。

### 2b. 手动指定（跳过自动定位）

```bash
$PY scripts/jacar.py plan <链接或fond号> --base "<件名级目录>"
```

`--base` 指向簿冊的上级「件名」目录（如 `…/露國革命一件`）。给了它就不扫盘，
行为与旧版完全一致。库结构特殊或自动定位判错时用这条。

### 3. 用户确认后执行

```bash
$PY scripts/jacar.py run --plan "<base>/.jacar/plan-<fond>.json" --confirm
```

完成后把结果摘要给用户：新建几个目录、下载几个、跳过几个、失败哪几条。

### 4. 校验

```bash
$PY scripts/jacar.py verify --base "<base>"
```

## 本机默认设置

常用参数可以存一次，之后省略。配置写在 `~/.jacar/config.json`，不进版本库。

```bash
$PY scripts/jacar.py config --root "<史料库最外层目录>"   # 必须配这个，才能自动定位
$PY scripts/jacar.py config                              # 查看当前
$PY scripts/jacar.py config --delay 3                    # 改单项
$PY scripts/jacar.py config --unset base                 # 删某项
$PY scripts/jacar.py config --reset                      # 全部清除
```

可存的键：`root`、`base`、`delay`、`csv_name`、`meta_name`、`pdf_name`、`max_name_bytes`、`book_dir`。

`root` 是史料库的最外层目录（如 `/path/to/史料`），skill 代码里不含任何具体路径，换机器只改这里。

优先级：`--base` > 自动定位 > 配置里的 `base`。配了 `root` 之后，`base` 会被忽略；
想回到"固定位置"模式，`config --base "<目录>"` 并把 `root` 删掉。

## 自动定位的边界

用簿冊标题**第一段**在库里找同名目录（`露国革命一件` ↔ `露國革命一件`），
比较时归一繁体、全角数字与空格标点。

**不用階層逐级拼路径**——本地目录名未必与階層逐字一致（整理时可能改过名、
手打有误差、或用字习惯不同），逐级拼容易在中途断掉。階層只用于两件事：
多个同名时消歧、以及事后核对有没有出入。

核对只看前两级（館、系列）和层级深浅；門/類/項 的本地命名常被整理者改过，
差异属正常，不当作问题上报。发现出入即停下来问你，不自动接受。

搜索会跳过 `_処理用`、`trash`、`.` 开头等噪声目录；深度上限 8 层。

## 硬约束

- **两套命名**：文件夹带序号（`1.1本邦／１ 大正７年…`），PDF 用去掉序号的标题正文（`本邦／１ 大正７年….pdf`）
- **不抢前台**：全程纯 HTTP，不开浏览器、不置顶窗口
- **礼貌抓取**：默认间隔 3 秒；`--delay 30` 完全遵守 robots.txt 的 Crawl-delay
- **幂等**：以 refcode 为主键，重跑跳过已完成项；中断后重跑接着做
- **不覆盖**：目标已存在且内容不同时另存后缀，绝不静默覆盖
- **不删旧数据**：不修改用户既有条目；冲突只报告

## 关键参数

| 参数 | 作用 |
| --- | --- |
| `--base` | 件名级目录；不传则按 `root` 自动定位 |
| `--root` | 临时指定史料库根目录，覆盖配置 |
| `--create-missing` | 件名目录不存在时，允许按建议位置新建 |
| `--book-dir` | 直接指定簿冊目录，覆盖自动推导；繁简不一致时用 |
| `--numbering append\|raw` | append＝主序号＋「／N」追加；raw＝全照原文 |
| `--pdf-name body\|dir` | body＝PDF 用标题正文（默认）；dir＝与文件夹同名 |
| `--csv-name` | 默认 `選擇材料.csv` |
| `--meta-name` | 默认 `目錄.md` |
| `--limit N` | 只做前 N 条，用于试跑 |
| `--delay` / `--retries` | 请求间隔与重试次数 |
| `--max-name-bytes` | 单个名字字节上限，默认 240 |

## 把已下载的 PDF 改成正文命名

```bash
$PY scripts/jacar.py rename-pdfs --book "<簿冊目录>"            # 预演，只列出
$PY scripts/jacar.py rename-pdfs --book "<簿冊目录>" --confirm  # 真改
```

只重命名，不重新下载；目标已存在则跳过，不覆盖。

## 遇到问题

- 目录建到了新地方而不是复用旧目录 → 加 `--book-dir` 指到旧目录
- 序号对本册不适用 → 在 `references/profiles/<fond>.json` 里写 `map`（refcode→名字）或 `tail_rule`
- 站点改版解析失败 → 读 `references/troubleshooting.md`，并对比 `references/site-structure.md` 里存的快照

## 参考

- `references/site-structure.md`：三个接口的实测选择器与请求样例
- `references/naming-rules.md`：序号推导机制、回归基线、已知人工例外
- `references/meta-template.md`：目錄.md 逐字模板
- `references/troubleshooting.md`：现象 → 处置
