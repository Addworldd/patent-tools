---
name: zhihuiya-highlight-shot
description: >-
  智慧芽网页高亮截图与附图抓取全流程自动化专家技能（查新报告 / 现有技术检索报告 / 稳定性分析报告通用）。
  自动解析报告 docx/doc，提取对比文件智慧芽分享链接 (RLINK)、正文评述段落、下划线引文及涉及图号；
  全平台跨电脑自适应自愈：自动探测 Google Chrome 或系统原生 Microsoft Edge 浏览器、智能格式降级与依赖自检，零硬编码；
  驱动浏览器统一 1400px 视口并启用 Retina 2x 超清渲染（device_scale_factor=2，物理像素 2512px，412 DPI）、强制单栏全宽排版、先落摘要页精准提取申请人再切说明书；
  外文专利支持【公布原文截图 + 中文机翻截图 + 涉及附图】双模自动化截取与严谨排版；中文专利自动截取公布原件高亮及附图；
  全语种通用多层高亮（中文穿透附图内联标签精准对齐，外文支持日文、英文、德文、韩文、法文等兄弟节点链式扫描、标号打分与机翻字面匹配）；
  正文提及段落 100% 完整覆盖（重叠自动合并，密集分簇）；智能抓取引述附图并以无边框表格并排居中；
  正文末尾直接分页（无大标题），严格按【相关文献X(公开号-发明名称-申请人)】（加粗宋体小五 9pt）纯图标准规范回填 Word 文档。
---

# 智慧芽网页高亮截图与附图自动化专家技能 (zhihuiya-highlight-shot)

本 Skill 为专利查新报告、现有技术检索报告、专利稳定性分析报告提供工业级、跨电脑高度通用、端到端极其可靠的**智慧芽网页高亮截图、附图智能抓取与标准版式自动回填流水线**。

---

## 核心红线与防错准则（Non-Negotiable Rules）

1. **跨电脑无缝适配与零硬编码（Zero-Pain Portability）**：
   - **浏览器自适应**：脚本自动检索本机 Google Chrome（64位/32位/用户目录）；若未安装 Chrome，**自动切换使用 Windows 10/11 系统原生自带的 Microsoft Edge（msedge.exe，内核同为 Chromium，Playwright 完美兼容且画质无损）**，彻底告别浏览器依赖缺失报错；
   - **文档格式自适应**：优先推荐现代标准 `.docx`（0 外部工具依赖，跨平台纯原生 OpenXML 解析）；若输入 `.doc`，自动降级探测 Microsoft Word COM 或 金山 WPS Office COM，若环境不支持则输出明确友好的中文另存为指引；
   - **零绝对路径绑定**：产物输出路径默认与当前报告同级目录绑定，杜绝任何 `Users/Administrator` 等用户名绝对路径。
2. **统一视口与 Retina 2x 超清渲染（412 DPI 无损画质）**：
   - 浏览器视口锁定为 `width: 1400px`；
   - 必须显式启用 `device_scale_factor: 2`！截取的物理图像宽度达 **2512px**，插入 Word 15.5cm 时达到 **412 DPI** 打印级锐利度，彻底消除放大发虚与拉伸模糊。
3. **强制单栏全宽模式（严禁双栏并排）**：
   - 说明书正文必须处于全宽单栏视图，严禁右侧并排显示权利要求或附图栏。
   - 脚本自动注入布局样式锁死 `.patent-app-view__desc` 与 `.patent-app__content-view` 宽度为 100%，屏蔽任何并排分栏。
4. **彻底剔除左侧灰色侧边栏**：
   - clip 测量严格基于正文卡片容器（从 left 约 144px 开始裁剪），保证截取的图片仅有纯白正文卡片，杜绝截入左侧深灰导航背景。
5. **严格标签流转与申请人提取**：
   - 必须以带签名的分享链接 **`/patent-view/abst`（摘要页）** 为第一入口进入；
   - 从摘要页著录项提取官方准确的申请人全称与专利名称（用于后续标题 `相关文献X(公开号-发明名称-申请人)`）；
   - 平滑切换至说明书 `/patent-view/desc`，确认说明书段落全部渲染。
6. **外文双模模式（公布原文 + 智慧芽机翻 + 附图）排版顺序红线**：
   - **对于外文文献（日文/英文/德文/韩文等）**：
     - **第一阶段（公布原文）**：在说明书官方公布原文下，高亮对应段落引文，截取超清原文截图；
     - **第二阶段（智慧芽机翻）**：点击语言栏切换至“简体中文”机翻模式，注入机翻全宽排版样式，对机翻中的对应中文下划线进行精准匹配与黄色高亮，截取超清机翻截图；
     - **第三阶段（涉及附图）**：切换至附图页抓取引述的图纸；
     - **排版回填顺序严格为**：
       在小标题 `相关文献X(公开号-发明名称-申请人)` 下：
       1. **外文公布原文截图（全部放完）**；
       2. **机翻截图（全部放完）**；
       3. **附图（最后放，1 行 2 列并排无边框居中表格）**。
   - **对于中文文献（CN）**：
     - 直接截取公布原文高亮截图，随后放置涉及附图。
7. **全语种（中文/日文/英文/德文/韩文/法文等）通用多层阶梯式高亮引擎**：
   - **中文穿透内联标签容错**：解决智慧芽在正文中插入 `<a class="see-img-anchor">图2</a>` 导致下划线句子被截断的问题，提取核心标记词穿透高亮；
   - **日文及多语种 DOM 结构自适应**：日文专利段号 `<b class="d_n">【00XX】</b>` 为独立短标签时，自动链式扫描后续所有兄弟正文节点；兼容全角数字（`１２３` -> `123`）归一化匹配；
   - **多语种原文对齐**：提取中文下划线中的构件附图标记数字与数学物理符号，在各外文句子中打分匹配最佳目标句；
   - **机翻中文直接对齐**：在机翻容器 `.translation-block` 中，下划线中文直接进行字面与主干匹配高亮。
8. **正文引述段落 100% 完整覆盖（防遗漏机制）**：
   - 自动提取正文评述提及的**每一个段落号**；
   - 智能计算分簇区间：前后适度合并扩展，确保报告引用的每个段落无一遗漏。
9. **评述涉及附图智能抓取**：
   - 自动识别评述中引述的图号（如 图1a, 1b, 8, Fig. 1, 図1 等）；
   - 平滑跳转至 `/patent-view/image` 附图专区，开启大图模式，精准定位对应图纸卡片并截取高清图纸。
10. **律所级排版回填规范**：
    - **移除大标题**：彻底移除【对比文件说明书关键段落高亮截图对照】大标题；
    - **正文末尾分页**：直接在正文最后一页插入分页符另起一页；
    - **小标题规范**：加粗宋体小五（9pt）：  
      `相关文献X(公开号-发明名称-申请人)`
    - **小标题后纯图片排版**：另起一行直接放置说明书截图（居中，宽 15.5cm），随后附图以 1 行 N 列无边框表格并排居中排列（每图宽约 7.5cm），绝无多余文字评述与图号。

---

## 跨电脑运行依赖与安装

在任意新电脑上，仅需执行一行命令安装依赖：

```bash
pip install -r C:/Users/Administrator/.workbuddy/skills/zhihuiya-highlight-shot/requirements.txt
```

*依赖清单 (`requirements.txt`)*：
* `playwright>=1.40.0`（浏览器自动化控制器）
* `python-docx>=1.0.0`（Word 文档读写解析器）
* `pillow>=10.0.0`（Retina 超清位图与高亮像素质检）
* `lxml>=4.9.0`（高性能 XML/HTML 分析）
* `pywin32>=306; sys_platform == 'win32'`（可选，仅当输入老版 .doc 时需要）

---

## 模块工具链

| 脚本文件 | 核心职责 |
|---|---|
| `gui_server.py` | **轻量可视化 WebUI 服务端**（免额外依赖，一键唤起现代浏览器界面，支持原生文件选择/拖拽、实时日志与一键打开文档） |
| `scripts/run_highlight_shot_pipeline.py` | **一键端到端总控流水线**（依赖自检 -> Chrome/Edge自适应 -> 浏览器高亮附图抓取 -> PIL质检 -> Word标准版式回填） |
| `scripts/extract_report_data.py` | 报告文档解析器（提取对比文件元数据、RLINK、下划线引文、全部引述段号与涉及图号；Word/WPS多重格式自适应） |
| `scripts/zy_highlighter.py` | 智慧芽浏览器核心控制器（Chrome/Edge自动探测/1400视口/Retina 2x超清/单栏/外文双模/中文穿透高亮/附图专区抓取） |
| `scripts/insert_screenshots_to_docx.py` | 律所级排版回填器（宋体小五加粗标题 + 原文图 -> 机翻图 -> 附图表格无缝排版，相对路径动态绑定） |

---

## 启动方式

### 方式一：可视化界面（推荐，0 命令行门槛）

* **方法 A（桌面双击）**：直接在桌面双击运行 `智慧芽高亮截图工具.bat`；
* **方法 B（命令行启动 WebUI）**：
  ```bash
  python C:/Users/Administrator/.workbuddy/skills/zhihuiya-highlight-shot/gui_server.py
  ```
  *启动后会自动在默认浏览器中打开现代卡片界面，支持点选文件或拖拽，并在原报告同级目录下自动生成成果文档，可一键直达打开！*

---

### 方式二：命令行一键执行（批处理/自动化脚本集成）

```bash
# 1. 最通用用法（处理报告中的所有有引证的对比文件并自动回填至 Word）
python C:/Users/Administrator/.workbuddy/skills/zhihuiya-highlight-shot/scripts/run_highlight_shot_pipeline.py "path/to/报告.docx"

# 2. 仅处理指定对比文件（如相关文献1）
python C:/Users/Administrator/.workbuddy/skills/zhihuiya-highlight-shot/scripts/run_highlight_shot_pipeline.py "path/to/报告.docx" --refs 1

# 3. 指定自定义浏览器路径（默认会自动检索 Chrome 或 Edge）
python C:/Users/Administrator/.workbuddy/skills/zhihuiya-highlight-shot/scripts/run_highlight_shot_pipeline.py "path/to/报告.docx" --browser-path "D:/tools/chrome.exe"

# 4. 调试模式（显示浏览器界面）
python C:/Users/Administrator/.workbuddy/skills/zhihuiya-highlight-shot/scripts/run_highlight_shot_pipeline.py "path/to/报告.docx" --no-headless
```
