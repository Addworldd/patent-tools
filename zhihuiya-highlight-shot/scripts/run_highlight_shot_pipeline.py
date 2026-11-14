# -*- coding: utf-8 -*-
"""
智慧芽网页高亮截图全流程自动化流水线 (PatSnap Highlight & Screenshot End-to-End Pipeline)

一键完成：
1. 报告解析：提取每篇对比文件公开号、智慧芽分享链接 (RLINK)、评述下划线引文、全部引述段落号及涉及图号。
2. 浏览器自动化：
   - 视口严格锁死 1400px，统一开启 Retina 2x 超清渲染（device_scale_factor=2，物理像素达 2512px，打印分辨率 412 DPI）。
   - 强制单栏全宽模式，屏蔽任何并排分栏。
   - 严格走 /patent-view/abst 入口，提取官方申请人与专利名称。
   - 切换说明书 /patent-view/desc，强制关闭机翻，使用公布原文。
   - 注入全语种（中文/英文/德文/日文/韩文/法文）通用高亮 (#ffeb3b)，支持精准字面匹配与跨语种标号打分。
   - 确保正文评述提及的每一个段落完整覆盖，无任何遗漏。
   - 识别评述涉及图号，平滑跳转 /patent-view/image 抓取官方附图。
   - PIL 图像黄色像素门禁质检。
3. 成果回填：
   - 正文最后一页直接插入分页符另起一页（无大标题）。
   - 自动插入【相关文献X(公开号-发明名称-申请人)】（加粗宋体小五 9pt）。
   - 小标题后另起一行直接放置说明书长图（居中，宽 15.5cm）。
   - 涉及附图以 1 行 N 列无边框表格并排居中排列（每图宽约 7.5cm）。
   - 绝无多余文字评述与图号。
4. 交付清单：输出 JSON 与完整执行报告。

用法：
  python run_highlight_shot_pipeline.py <报告路径.doc/.docx> [--refs 1,2] [--no-insert] [--out-docx <新文件路径>] [--no-headless]
"""

import os
import re
import sys
import json
import time
import argparse
import asyncio

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from extract_report_data import parse_report
from zy_highlighter import ZyHighlighter, get_browser_executable
from insert_screenshots_to_docx import append_highlight_section


def check_runtime_dependencies():
    """在流水线启动前执行依赖自检，若缺少依赖输出一键补齐指引。"""
    required = {
        'playwright': 'playwright',
        'docx': 'python-docx',
        'PIL': 'pillow',
        'lxml': 'lxml'
    }
    missing = []
    for mod, pkg in required.items():
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)

    if missing:
        pkgs_str = " ".join(missing)
        req_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'requirements.txt')
        msg = (
            f"\n"
            f"================================================================================\n"
            f"[依赖自检提示] 当前 Python 环境 ({sys.executable}) 缺少必要依赖包:\n"
            f"  缺少: {pkgs_str}\n"
            f"\n"
            f"【一键自愈修复命令】：\n"
            f"  {sys.executable} -m pip install {pkgs_str}\n"
            f"  或安装完整依赖清单:\n"
            f"  {sys.executable} -m pip install -r \"{req_path}\"\n"
            f"================================================================================\n"
        )
        print(msg)
        return False
    return True


async def run_pipeline(docx_path, refs=None, output_dir=None, insert_docx=True, out_docx_path=None, headless=True, browser_path=None):
    if not check_runtime_dependencies():
        return {'success': False, 'message': '缺少必要运行依赖'}

    detected_browser = get_browser_executable(browser_path)
    browser_display = detected_browser if detected_browser else 'Playwright 内置 Chromium'

    print("================================================================================")
    print("        智慧芽网页高亮截图与附图全流程流水线 (zhihuiya-highlight-shot)        ")
    print("================================================================================")
    print(f"[*] 执行 Python 解释器: {sys.executable}")
    print(f"[*] 自适应浏览器引擎  : {browser_display}")
    print(f"[*] 输入报告文档路径  : {docx_path}")
    print(f"[*] 渲染规格: 1400px 视口 + Retina 2x 超清 (412 DPI) + 双模公布原文/机翻")

    # 1. 解析报告
    print("\n[阶段 1/3] 正在解析报告文档结构、对比文件列表与涉及段落/附图...")
    report_data = parse_report(docx_path)
    real_docx = report_data['docx_path']
    total_patents = report_data['patents']
    print(f"[+] 解析完成！共提取到 {len(total_patents)} 篇对比文件记录")

    # 过滤待处理文献
    allowed_refs = set(refs.split(',')) if refs else None
    queue = []
    for p in total_patents:
        rid = str(p['ref_index'])
        if allowed_refs and rid not in allowed_refs:
            continue
        has_content = bool(p.get('target_paras') or p.get('has_undeduced_paras') or p.get('all_highlights'))
        if has_content or allowed_refs:
            queue.append(p)

    if not queue:
        print("[-] 提示: 未发现包含有效段落引证的对比文件。")
        return {'success': False, 'message': '未发现待处理文献'}

    print(f"[+] 本次将处理 {len(queue)} 篇对比文件: {[p['pn'] or p['ref_index'] for p in queue]}")

    if not output_dir:
        base_name = os.path.splitext(os.path.basename(real_docx))[0]
        output_dir = os.path.join(os.path.dirname(real_docx), f"{base_name}_高亮截图产物")
    os.makedirs(output_dir, exist_ok=True)

    # 2. 浏览器自动化高亮截图与附图抓取
    print("\n[阶段 2/3] 启动智慧芽多语言高亮、超清截图与附图抓取引擎...")
    highlighter = ZyHighlighter(headless=headless, browser_path=detected_browser)
    execution_results = []

    for idx, p_item in enumerate(queue, 1):
        pn = p_item['pn'] or f"文献{p_item['ref_index']}"
        lang = p_item.get('lang_hint', 'UNKNOWN')
        print(f"\n--- [{idx}/{len(queue)}] 正在处理 相关文献{p_item['ref_index']}: {pn} (语种: {lang}) ---")
        p_out_dir = os.path.join(output_dir, f"ref_{p_item['ref_index']}_{pn}")
        res = await highlighter.process_patent(p_item, p_out_dir)
        execution_results.append(res)

    # 3. 回填 docx 报告
    final_doc_path = None
    if insert_docx:
        print("\n[阶段 3/3] 将高清截图与附图回填至 Word 报告（标准版式）...")
        successful_items = [r for r in execution_results if r.get('success')]
        if successful_items:
            final_doc_path = append_highlight_section(real_docx, successful_items, out_docx_path)
            print(f"[+] 回填完成！成果文档: {final_doc_path}")
        else:
            print("[-] 无成功截图产物，跳过文档回填。")

    # 4. 汇总清单与质检结果
    meta_json_path = os.path.join(output_dir, "pipeline_execution_manifest.json")
    with open(meta_json_path, 'w', encoding='utf-8') as f:
        json.dump({
            'source_docx': docx_path,
            'real_docx': real_docx,
            'final_docx': final_doc_path,
            'processed_count': len(execution_results),
            'results': execution_results
        }, f, ensure_ascii=False, indent=2)

    print("\n================================================================================")
    print("                              流水线执行完成汇总                              ")
    print("================================================================================")
    for res in execution_results:
        pn = res.get('pn')
        rid = res.get('ref_index')
        shots = len(res.get('screenshots', []))
        figs = len(res.get('figures', []))
        ok = res.get('success')
        status = "成功" if ok else f"失败 ({res.get('error')})"
        print(f"  * 相关文献{rid} [{pn}]: {status} -> 说明书截图 {shots} 张, 附图 {figs} 张")

    if final_doc_path:
        print(f"\n[★] 最终 Word 报告已生成: {final_doc_path}")
    print(f"[★] 结构化产物清单: {meta_json_path}")

    return {
        'success': True,
        'final_docx': final_doc_path,
        'output_dir': output_dir,
        'results': execution_results
    }


def main():
    parser = argparse.ArgumentParser(description="智慧芽网页高亮截图与附图全流程流水线")
    parser.add_argument("docx_path", help="检索/查新报告路径 (.docx 或 .doc)")
    parser.add_argument("--refs", default=None, help="仅处理指定的文献序号，逗号隔开，如 '1,3'")
    parser.add_argument("--output-dir", default=None, help="截图保存目录")
    parser.add_argument("--no-insert", action="store_true", help="不回填至 Word 文档，仅截图")
    parser.add_argument("--out-docx", default=None, help="自定义输出 Word 路径")
    parser.add_argument("--browser-path", default=None, help="自定义浏览器可执行文件路径（Chrome/Edge，未指定时自动探测）")
    parser.add_argument("--no-headless", action="store_true", help="显示浏览器窗口进行调试")
    args = parser.parse_args()

    asyncio.run(run_pipeline(
        docx_path=args.docx_path,
        refs=args.refs,
        output_dir=args.output_dir,
        insert_docx=(not args.no_insert),
        out_docx_path=args.out_docx,
        headless=(not args.no_headless),
        browser_path=args.browser_path
    ))


if __name__ == '__main__':
    main()
