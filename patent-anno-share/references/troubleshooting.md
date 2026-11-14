# 故障对照

## 页面能打开，但高亮/备注刷新后消失

**症状**：加了高亮、写了备注，刷新页面全没了。

**排查顺序**：

1. 看顶栏右侧的同步状态文字
   - `未配置云端，仅本页可见` → `build_html.py` 没传 `--cloud-endpoint/--cloud-key`，页面是纯本地版
   - `同步失败，仅本地可见` → 云端配置传了，但请求被拒
2. 打开浏览器控制台看报错
   - `requires oauthRelayBaseUrl` → 初始化时漏了该字段（本 skill 的页面不需要登录，一般不会遇到）
   - `CORS` / `Origin` 相关 → 发布域名与云服务预留域名不一致
   - `relation "annotations" does not exist` → 表没建

**常见根因**：先 `activate` 但没建表。云服务只负责分配环境，**表要自己建**。

## 高亮位置错乱（标到别的字上）

**根因**：偏移基准不一致。常见三种：

| 情况 | 表现 | 处理 |
|---|---|---|
| 用了 HTML 偏移 | 新增一条高亮后，后面的老高亮全部偏移 | 改用纯文本偏移重存 |
| 浏览器差异（换行符） | 同一段在不同浏览器位置差 1-2 字 | 解析阶段统一 `\r\n` → `\n` |
| 段号重复 | 两段同号，批注串到另一段 | `parse_md.py` 的 `duplicates` 应为空，非空要修 md |

## 划选后不弹工具条

**症状**：选中文字，没有「加高亮」按钮。

原因一：**跨段选择**。代码要求选区起止在同一段内（`startContainer` 与 `endContainer` 同属一个 `[data-no]`），跨段直接放弃。这是刻意的——跨段高亮无明确定位。

原因二：选中内容全是空白（`!text.trim()`）。

原因三：选择在段号行、分组标题上（这些元素没有 `data-no`）。

## 工具条位置跑偏

工具条用 `window.scrollX/Y + rect.left/top` 定位。若父容器有 `transform`（例如某些弹窗），
`getBoundingClientRect` 的坐标系与 `absolute` 定位基准不一致，会偏。

处理：给 `#tb` 改用 `position:fixed`，坐标直接用 `rect.left/top` 不加滚动量。

## 多人批注看不到对方

1. 确认 `doc_id` 一致——不同 docId 的数据在各自主页，互不可见
2. 页面每 60 秒自动轮询一次，等一会儿或点「刷新批注」
3. 若用不同域名打开（一个 localhost 一个线上），**云服务按 Origin 精确匹配**，localhost 的请求会被拒，必须都从发布后的域名访问

## 导出为空

`export_notes.py` 拿到 0 条：

1. 确认 `--doc-id` 与页面里的 `DOC_ID` 完全一致（含大小写）
2. 确认 `--key` 用的是 `publishableKey`（不是别的密钥）
3. 先手工验证接口通不通：

```bash
curl -s "https://<endpoint>/.cloud/database/rest/v1/annotations?select=*&limit=1" \
  -H "apikey: <publishableKey>" -H "Authorization: Bearer <publishableKey>"
```

返回 `[]` = 接口通但没数据；返回 401/403 = 密钥或 Origin 问题。

## 部署后页面空白

**静态站点必须让 `index.html` 在站点根目录**。若生成到了 `site/build/index.html` 而部署目录是 `site/`，根路径就没有入口页。

检查：部署目录下 `ls index.html` 存在。

## 中文乱码

生成的 HTML 头部必须 `<meta charset="utf-8">`，模板已带。
若部署后乱码，检查服务器是否返回了 `Content-Type: text/html` 但无 charset——静态托管一般没问题，
自建 Node/Python 服务需显式设置。
