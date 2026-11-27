# JACAR 站点结构（实测于 2026-11-27）

> 站点改版时，用本文件与现场对比，能最快定位是哪一层选择器失效。

## 1. 检索页

`GET https://www.jacar.archives.go.jp/aj/search?fond={refcode}&page=N`

每页 20 条，翻页靠 `page` 参数。

| 内容 | 选择器 | 备注 |
| --- | --- | --- |
| 结果行 | `li.archive-result-table__body-item` | 每页 20 |
| 种别 | `.cmn-category-label__text` | `簿冊` / `件名` |
| 标题 | `.result-header__title` | 用 `get_text("", strip=True)`，空分隔符 |
| 階層 | `.result-tree__item-label a` | 逐级，href 里是 `BA…` |
| 摘要 | `.result-summary__item` 的 dt/dd | `規模` `所蔵館における請求番号` `組織歴/履歴` `言語` `内容` |
| refcode | `.result-info__item` 的 dt/dd | 键为 `レファレンスコード` |
| 勾选框值 | `input.csv-export[value]` | 形如 `aj11/B03051171300` |
| 阅览链接 | `.cell-image a.result-image-link[href]` | 簿冊行为空 |
| 总数 | `.archive-result-header__count-total .count-number[data-count]` | |
| 分页 | `nav.pager a.pager__num`、`a.pager__item--next` | |

注意：**簿冊行（第 1 条）没有阅览链接，`/das/image/{簿冊ref}` 返回 404**。它只用于取标题和导 CSV。

## 2. CSV 导出（三步）

```
1) GET  /aj/search?fond={fond}                      # 建立会话（JSESSIONID）
2) POST /aj/ajax/search/csv   data={isall:1, idaction:""}
   → ["aj11/B03051171200", "aj11/B03051171300", …]   # 本册全部 id
3) POST /aj/download          data={ids:"aj11/B03051171200,aj11/B03051171300,…"}
   → text/csv，UTF-8 BOM + 全字段双引号
```

返回 9 列：

```
レファレンスコード,標題,階層,所蔵館における請求番号,画像数,種別,作成年月日,作成者,組織歴
```

要点：

- **CSV 不含「内容」字段**，目錄.md 必须另抓详情页
- 行数 ≠ 条目数：含簿冊行、表紙行，且行数等于检索结果数
- 簿冊行 = 階層段数最少的那一行（比件名少一级），不是「不含 ＞」的行
- 原字节落盘即可保留 BOM 与引号风格

## 3. 详情页

`GET /das/meta/{refcode}`

静态 HTML。字段在 `dt` / `dd` 成对结构里：

```
件名標題 / レファレンスコード / 所蔵館における請求番号 / 言語 / 作成者名称
資料作成年月日 / 規模 / 組織歴/履歴 / 内容 / 引用例
```

解析要点：

- `dd` 内部有多个 `<span>`，用 `get_text("", strip=False)` 拼接，再把连续空白压成一个，否则「請求番号」会被拆成两行
- 页面有 **两个** `.archive-detail-section__data-copy-input` 复制框：第一个是 URI，第二个才是引用例；要遍历取含 `JACAR` 的那个
- 引用例是多个 span 拼成的完整句子，直接取 `p` 的文本即可，不要用空格分隔符

## 4. 阅览页与 PDF 直链

`GET /das/image/{refcode}`

页面内嵌：

```js
var najContentList = [{
  "id": "C200140606800",
  "contentName": "B03051171300.b10179.1-1224.00000005.pdf",
  "size": "26 KB",
  "type": "pdf",
  "path": "/content/item/aj12/C200140606800/raw/B03051171300.b10179.1-1224.00000005.pdf",
  "format": "application/pdf"
}];
```

直接 `GET https://www.jacar.archives.go.jp{path}` → `200 application/pdf`，前 4 字节 `%PDF`。

- 支持 Range（返回 206），可断点续传
- 官方文件名格式：`{refcode}.{barcode}.{reel}.{serial}.pdf`
- 若 `type` 不是 pdf，回退 IIIF 逐页下载再用 Pillow 合成

## 5. robots.txt

```
Crawl-delay: 30
Disallow: /contentDownload/*
Disallow: /aj/contentDownload/*
```

`/content/item/.../raw/` 不在禁止名单，但仍有 Crawl-delay。默认 3 秒，`--delay 30` 完全守规。
