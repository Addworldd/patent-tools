# 智慧芽（analytics.zhihuiya.com）说明书页高亮截图 —— 站点经验

> 2026-08-31 吻合器案（P2025744CN1）实测沉淀。执行截图前必读，配合 `screenshot-spec.md` 使用。

## 1. 入口：必须用带完整签名的分享链接，不能手敲 URL

- 正确入口 = xlsx 导出表中的智慧芽超链接，形如
  `https://analytics.zhihuiya.com/patent-view/abst?patentId=<uuid>&shareId=<...>&from=EXPORT&signature=<...>&expire=94608000&date=...&version=1.0`
  —— 注意是 **`/patent-view/abst`（摘要页）**，不是 desc。
- **血泪教训**：手工打开/历史残留的 `patent-view/desc?...`（缺少 `signature` 等参数）会显示**残缺说明书**：
  - 现象：段落看似正常，但关键句整段消失，如 `[0016]。 [0017]。` 为空、`[0008]` 丢掉"无线发送专用于手术器械的识别数据"、`[0024]` 结尾变成"驱动单元还包括。"。
  - 后果：摘录 10 条全部 MISS，误判为"文本不存在/幻觉"。
  - 判定：只要出现 `\[\d{4}\]。` 这种"段号+句号"的空段，或关键句缺失，就是残缺视图，**必须重新走分享链接进入**。
- 步骤：`POST /new`（body 原样传完整 URL）→ 等 8s → `GET /info` 校验 `ready: complete` 且 title 含公开号。
- **申请人**在摘要页取（`innerText` 搜"申请人/权利人/发明人"），不要依赖报告列表里的文字。

## 2. 切到说明书页（用户明确要求）

- 分享链接落在**摘要页**，必须再点一次说明书导航：
  `POST /clickAt?target=ID`，body = `a.nav-menu__item.pendo-track__nav-menu__desc`
- 导航类名（router-link）：`摘要 / clms(权利要求) / desc(说明书) / image(附图) / pdf / patent-family`。
- 切完再校验：URL 变 `/patent-view/desc`，段落齐全（如 CN105411636A 共 155 段）。

## 3. 滚动与视口（最容易卡死的地方）

- **真正的滚动容器是 `MAIN.patent-app-view__desc.highlight-box-scroll`**（`overflow-y:auto`，scrollHeight 10132 / clientHeight 2671），**不是 window**。
- `body` 为 `overflow:hidden`，`documentElement` 也不可滚 → **`window.scrollTo` 完全无效**（这就是规范写"不要用 scrollTo"的原因）。
- 两条可行路线：
  1. **一次性大视口**：先展开内容容器再调视口高度容纳全部内容，然后一次截图（内容 >10000px 时截图很慢，见第 4 点）。
     ```js
     const c = document.querySelector('.patent-app__content-view');
     c.style.height = c.scrollHeight + 'px';
     c.style.overflow = 'visible';
     c.style.maxHeight = 'none';
     ```
  2. **逐段滚动**（推荐）：视口固定 1400×2800，滚动 `MAIN` 容器 `scrollTop` 到目标段，逐段截图后 PIL 裁剪。
- 量坐标时 y 要加滚动容器的 `scrollTop`（而非 `window.scrollY`）。

## 4. 截图超时：代理通道不够用

- web-access 的 `cdp-proxy.mjs`（端口 3456）**CDP 命令超时硬编码 15s**（`timeoutMs = 15000`），1400×2800 以上的视口截图基本必超时，`/screenshot` 返回空。
- 规范里写的 `GET /viewport?target=ID&w=..&h=..` **当前代理版本没有该端点**，需自行用 CDP `Emulation.setDeviceMetricsOverride` 设置。
- 解决：截图、大视口设置**绕开代理，直连 CDP**（`ws://127.0.0.1:9222/devtools/page/<id>`，Node 22 内置 `WebSocket` 即可，无需 ws 模块）；代理仍用于 `/new`、`/eval`、`/clickAt`。
- 直连脚本若在 Bash 前台跑，注意 120s 会被 SIGTERM —— 长任务用 `run_in_background`。

## 5. 摘录文本清洗

- 报告里下划线摘录常带**段号前缀**和**尾部左括号**（如 `[0017]不同类型的……（`、`[0045]可选地，所述外壳主体1……（`），需清洗后再匹配：去 `^\[0*NNNN\]`、去结尾 `（(`。
- 摘录中的**省略号**（`……` / `…` / `...`）是撰写者自己加的，网页原文中没有 → 需**按省略号拆成多个片段分别高亮**（片段长度 ≥5 字），否则整条 MISS。
- 高亮颜色 `#ffe28a`（渲染 `rgb(255,226,138)`），用 `span[data-hl="<段落号>"]`，跨节点用全文拼接 + `Range.setStart/setEnd` + `extractContents()`。
- 校验：`span[data-hl]` 总数 == 片段数；重跑前先清理旧 span（还原文本节点 + `normalize()`），否则嵌套重复。

## 6. 环境检查别漏端口

- 检查 CDP 代理是否存活要扫 **3456**（连同 Chrome 的 9222）。曾因 `netstat` 过滤条件里没写 3456，误判"代理已死"而另起炉灶，绕开成熟通道。
