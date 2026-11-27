# 故障排查

| 现象 | 原因 | 处置 |
| --- | --- | --- |
| 解析出 0 条 | 站点改版，主选择器失效 | `parser.py` 会降级为全页扫 `B\d{11}` 并写 `STRUCTURE_DRIFT`；对照 `site-structure.md` 修选择器 |
| 检索结果总数读不到 | `.count-number[data-count]` 变了 | 回退读 `data-count` 属性；再不行以本页条数代替并报警 |
| `HTTP 429` | 请求过快 | 自动退避 30/60/120 秒，三次后整批停。改 `--delay 30` 再跑 |
| `HTTP 403` | 被拒绝 | 立即停止，不要重试刷。隔几小时或换网络 |
| 单条阅览页 `404` | 后端瞬时故障（同一 ref 前一次成功过） | `download_pdf` 已内置三次重试；仍失败则记入 manifest，稍后重跑 |
| 簿冊 `/das/image/` 404 | 簿冊本身没有影像 | 正常现象，脚本不把簿冊当条目下载 |
| PDF 前 4 字节不是 `%PDF` | 拿到的是错误页 | 脚本会删掉半成品并报错；检查该 ref 是否需要登录或已下线 |
| `目錄.md` 的請求番号被拆成两行 | `dd` 内有多个 span，用了空格分隔符 | 已改为空分隔符 + 压空白；若复发检查 `parse_meta` |
| 引用例带「URIをコピー」前缀 | 取了第一个复制框 | 页面有两个 `.archive-detail-section__data-copy-input`，要遍历取含 `JACAR` 的 |
| 目录建到新地方、没复用旧目录 | JACAR 用新字体（露国/第二巻），旧库用旧字体（露國/第二卷） | 加 `--book-dir` 直接指到旧目录；或确认异体字表覆盖了该字 |
| 同一册里两条重名 | 序号推导撞车 | 脚本自动加 `__2` 后缀并报 `NAME_CONFLICT`；根治靠 profile 的 `map` |
| 名字被截断成 `…` | 超过 `--max-name-bytes`（默认 240） | 调大参数；macOS 单名上限 255 字节 |
| 重跑又下载了一遍 | plan 是快照，磁盘被改名过 | v1.0.0 起 run 会先扫磁盘建 refcode→目录映射，已修复；若仍复发检查 `dir_refcodes` |
| `已有一个 jacar 任务在跑` | `.jacar/.lock` 被占用 | 关掉另一个终端；确认无进程后删 `.jacar/.lock` |
| 目录里出现 `__2` 后缀 | 同册重名 | 正常保护，不是 bug |
| CSV 行数比条目多 | CSV 含簿冊行、表紙行 | 正常，行数等于检索结果数 |

## 判断"是不是站点挂了"

```bash
curl -s -o /dev/null -w "%{http_code}\n" "https://www.jacar.archives.go.jp/aj/search?fond=B03051171200"
```

- 200：站点正常，问题在解析或参数
- 403/429：限流，等
- 5xx：站点故障，等

## 恢复手段

```bash
# 只看不动，检查既有库结构
$PY scripts/jacar.py verify --base "<base>"

# 重新生成清单（磁盘变了就重跑这个）
$PY scripts/jacar.py plan <fond> --base "<base>"

# 补下失败的条目（幂等，已完成的不动）
$PY scripts/jacar.py run --plan "<base>/.jacar/plan-<fond>.json" --confirm
```
