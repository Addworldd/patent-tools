# 相关文献截图生成规范（评述确认后执行）

## 触发时机与前置条件

- 评述文字**经 Addworld 确认后**执行，作为填表收尾步骤。
- 输入：
  1. 报告 docx（含评述下划线摘录、相关文献列表区的智慧芽分享链接 RLINK）；
  2. 智慧芽网页（对比文件说明书原文）。
- 输出：每篇对比文件在 docx 末尾追加【相关文献X(公开号，申请人)：】标题，标题下先**文字部分**再**图片部分**（高亮截图）。

## 唯一截图路线：智慧芽网页高亮截图（浏览器自动化）

> 2026-08-19 Addworld 拍板：**去除 PDF/OCR 路线，以后统一走智慧芽网页高亮截图路线**（已实测跑通并复跑验证 PASS，稳定可复现）。

- 浏览器执行底座：**web-access skill**（`check-deps.mjs`/`cdp-proxy.mjs`，CDP 代理端口 3456，Chrome `--remote-debugging-port=9222`）。
- 站点操作细节：web-access skill `references/site-patterns/analytics.zhihuiya.com.md`（导航类名、视口锁定陷阱、截图超时处理），执行前读一次。

## 截图规格（Addworld 确认）

| 项 | 规格 |
|---|---|
| 截取范围 | **高亮段落 + 前后各两段**；目标段±2段前后重叠则合并为一张；目标段密集连成一张时**按语义分簇**（如 40/42/44/47 一簇、51/52/54 一簇） |
| 高亮颜色 | **黄色**（span background `#ffe28a`，渲染 rgb(255,226,138)） |
| 导出格式 | PNG（视口 1400×2800 全图截取后 PIL 裁剪） |
| 插入位置 | docx 末尾"测试相关文献"区：标题下方先**文字部分**（该篇评述摘要）再**图片部分** |
| 标题格式 | `相关文献X(公开号，申请人)：`（宋体 9pt 加粗），如 `相关文献1(CN116439840B，深圳康诺思腾科技有限公司)：` |
| 申请人来源 | **智慧芽链接对应摘要页**获取（不依赖报告列表原文） |

## 网页高亮截图流程（已实测，Addworld 确认"这次可以了"；2026-08-19 复跑 PASS）

> **执行前必读** `references/site-zhihuiya.md`（入口链接 / 说明书导航 / 滚动容器 / 代理 15s 超时 / 省略号拆片段）。
> 2026-08-31 教训：**不读站点经验就动手 = 必然返工**（残缺视图导致摘录全 MISS、window.scrollTo 无效卡死）。

0. **复述确认**（Addworld 长期约定）：先复述目标、范围、产出、关键约束，确认后再动。

1. **启动浏览器 + 代理**：`node check-deps.mjs`（web-access skill，确保 proxy ready）。
2. **提取高亮基准**：从报告 docx 用 `scripts/extract_underlines.py`（或等效逻辑）提取评述下划线原文 + 所属段落号，只保留当前网页专利范围内的段（≤本专利说明书总段数；超出的摘录属于其它对比文件，跳过）。
3. **打开说明书页**：用智慧芽分享链接（RLINK）→ `POST /new` 打开，等 8s，`/info` 确认 `ready: complete`、标题含专利号；若落在摘要页，切到说明书（`POST /clickAt` 传 `a.nav-menu__item.pendo-track__nav-menu__desc`）。**申请人从摘要页获取**。
4. **页面文本匹配**：`document.body.innerText` 逐一 `indexOf` 摘录，记录 found/not found。未命中多为 DOM 拆分，进入跨节点阶段。
5. **DOM 高亮**：命中文字包 `<span style="background:#ffe28a" data-hl="段落号">`；跨节点长句用"全文本拼接 + Range setStart/setEnd 节点定位"（`range.deleteContents()+insertNode(span)`）。验证 `span[data-hl]` 总数 = 摘录数，背景色 `rgb(255,226,138)`。误跑两次出现重复 span 时用清理脚本还原。
6. **截图**：
   - `Emulation.setDeviceMetricsOverride {width:1400,height:2800,deviceScaleFactor:1,mobile:false}`
     （⚠️ 规范旧版写的 `GET /viewport?target=ID&w=..&h=..`：当前 cdp-proxy **无此端点**，须直连 CDP 设置）；
   - `Page.captureScreenshot`（视口截图，1400×2800，PNG）。
     （⚠️ 代理 `/screenshot` 的 CDP 超时是 **15s**，大视口必超时返回空 → **绕开代理直连 CDP**，Node 22 内置 `WebSocket` 即可）；
   - **不要用 `window.scrollTo`**（无效：`body` overflow hidden，真滚动容器是
     `MAIN.patent-app-view__desc.highlight-box-scroll`，要设它的 `scrollTop`）；
   - 不要用 full=1（captureBeyondViewport 基本超时）、不要用 clip（不稳定）；
   - 逐段截图时 y 坐标 = `getBoundingClientRect().top + 容器.scrollTop`。
7. **PIL 裁剪（关键）**：先量各目标段 `[00XX]` 在 DOM 的 y 坐标（getBoundingClientRect），按"目标段±2段、重叠合并、密集分簇"计算裁剪区间，`Image.crop((0,yTop,1400,yBottom))` 导出 PNG。
   - **段落超视口陷阱（2026-08-19 实测，文献2 CN114343851B 场景）**：说明书段数多（如 133 段）时，正文在**可滚动容器**（`.patent-app__content-view`，scrollHeight 6784 > clientHeight 2671）内，视口 override 只影响外层文档（scrollHeight=视口高），段落 y 坐标仍会超出视口 → 截图截不到目标段。**解法**：先 `document.querySelector('.patent-app__content-view')` 把容器 `style.height=scrollHeight+'px'; style.overflow='visible'; style.maxHeight='none'` 展开，再 `/viewport?w=1400&h=6900` 调大视口容纳全部内容，然后量 y 坐标、截图、裁剪。
8. **验证**：PIL 检测裁剪图中黄色像素带，确认全部高亮点都在图内（期望点与检测带中心容差 ≤25px 全匹配）。
9. **回填 docx**：docx 末尾追加"测试相关文献"区——【相关文献X(公开号，申请人)：】（宋体9pt加粗）→ 文字部分（该篇评述）→ 图片部分（高亮截图，依次排列）。

## 智慧芽站点经验

已沉淀到 web-access skill：`references/site-patterns/analytics.zhihuiya.com.md`（平台特征/有效模式/已知陷阱，含导航类名、视口锁定陷阱、截图超时处理）。执行前读一次。

## 脚本

- `scripts/extract_underlines.py` — 从报告 docx 提取 {段落号: [下划线原文, ...]}（高亮基准；`<w:u>` 判定须 `w:val != none`，排除 val=none 的括号评述）。
- 高亮/截图/裁剪为临时脚本（浏览器内执行 JS + Python PIL），随案生成，不固化进 skill。

## 遗留事项

- 智慧芽分享链接（RLINK）带 `expire` 参数（约 2029 过期），过期后需重新生成。
- 截图超时为常态，重试 2-3 次；`hl_final.js` 高亮脚本重复运行会产生重复 span，用清理脚本还原文本节点后再跑。