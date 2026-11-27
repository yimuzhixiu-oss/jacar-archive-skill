# jacar-archive-skill

批量下载日本亚洲历史资料中心（JACAR / アジア歴史資料センター）史料并归档的 Agent Skill。

给历史学研究者用：把「逐条点开 → 下载 → 改名 → 建目录 → 抄目录」压缩成一条命令。

## 适用场景

- 拿到一个 `https://www.jacar.archives.go.jp/aj/search?fond=B…` 链接，想取其中的全部资料
- 想按统一规则整理已下载的 JACAR 史料（命名不一致、缺元数据）
- 做近代日本对外关系史研究，需要某一簿冊的完整影像与著录信息

## 核心做法

全程纯 HTTP，不开浏览器、不抢占前台窗口：

| 环节 | 做法 |
| --- | --- |
| 检索页 | `GET /aj/search?fond={ref}&page=N`，解析静态 HTML |
| 材料清单 | 三步：建会话 → `/aj/ajax/search/csv` 取 id → `/aj/download` 拿 CSV |
| 著录信息 | `GET /das/meta/{ref}` |
| PDF | 阅览页内嵌 `najContentList` 给出官方直链，`GET` 直接返回 `application/pdf` |

## 安装

```bash
git clone https://github.com/yimuzhixiu-oss/jacar-archive-skill.git \
  ~/.workbuddy/skills/jacar-archive
```

Claude Code 等其他 agent 放到各自的 skills 目录即可，目录名保持 `jacar-archive`。

依赖：

```bash
pip install --user requests beautifulsoup4 Pillow
```

## 用法

```bash
S=~/.workbuddy/skills/jacar-archive/scripts/jacar.py

# 1. 体检（约 10 秒）
python3 $S probe B03051171200

# 2. 干跑，只出清单不动手
python3 $S plan <链接或fond号> --base "…/露國革命一件"

# 3. 确认后执行
python3 $S run --plan "…/.jacar/plan-B03051171200.json" --confirm

# 4. 只读检查
python3 $S verify --base "…"
```

`--base` 指向「件名」级目录，脚本只在它下面建簿冊与条目目录，不碰上级路径。

把已下载的 PDF 改成正文命名（只重命名，不重下）：

```bash
python3 $S rename-pdfs --book "…/帝国ノ態度/第一巻"            # 预演
python3 $S rename-pdfs --book "…/帝国ノ態度/第一巻" --confirm  # 执行
```

## 命名规则

两套命名，各司其职：

| | 示例 | 目的 |
| --- | --- | --- |
| 文件夹 | `1.1本邦／１ 大正７年２月１０日から…` | `1.1 → 1.2 → 2.1` 的阅读顺序 |
| PDF | `本邦／１ 大正７年２月１０日から….pdf` | 与 JACAR 页面逐字对照 |

另外三条：

- **父项补零**：同册有 `2.1–2.4` 时，父项写成 `2.0` 而非 `2.`
- **全巻一册**：件名标题与册名相同时，PDF 直接放册目录，不再套一层
- **按册 profile**：规则推不出的例外（人工重编号等）写进 `references/profiles/{fond}.json`，不污染全局规则

详见 [references/naming-rules.md](references/naming-rules.md)。

## 已实测

| 簿冊 | 条目 | 体积 |
| --- | --- | --- |
| 帝国ノ態度 第一巻 | 22 | 46 MB |
| 帝国ノ態度 第二巻 | 8 | 10 MB |
| 帝国ノ態度 第三巻 | 9 | 31 MB |
| 帝国ノ態度 第四巻 | 1 | 2.6 MB |

- 页数与页面「規模」逐条吻合
- 全库回归 149 条：`L0 130 / L1 15 / L2 4 / DIFF 0`
- 重跑幂等：0 下载、全部跳过

## 安全边界

- 不覆盖用户既有文件；冲突只报告
- 不删除任何已有内容
- 默认请求间隔 3 秒；`--delay 30` 完全遵守站点 `Crawl-delay`
- 私有史料库不会被上传，仓库只含代码与文档

## 许可

MIT，见 [LICENSE](LICENSE)。
