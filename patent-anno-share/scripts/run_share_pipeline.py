# -*- coding: utf-8 -*-
"""
智慧芽分享链接 -> 中英对照可批注 HTML 单文件 一键流水线 (run_share_pipeline.py)

一键完成：
1. 输入智慧芽分享链接 (含 patentId, shareId, signature 等参数)；
2. 自动无头运行浏览器，访问摘要页、权利要求页、说明书页；
3. 抓取权利要求原文及中文机翻，提取说明书段落及中文机翻；
4. 严格执行段落连续性检查；
5. 生成标准 Markdown 归档文件与 data.json；
6. 自动渲染并编译生成独立的「可批注中英对照 HTML 单文件」（0 外部依赖，离线双击即开，支持划选高亮与备注）；
7. 输出清晰的交付摘要。

用法：
  python run_share_pipeline.py "<智慧芽分享链接>" [--out-dir <输出目录>] [--out-html <指定HTML文件名>] [--no-headless]
"""

import os
import re
import sys
import io
import json
import asyncio
import argparse

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from fetch_share import fetch_patent_from_share_url, export_as_markdown, export_as_data_json
from deepseek_translator import DeepSeekTranslator, translate_patent_data_sync


def main():
    parser = argparse.ArgumentParser(description="智慧芽分享链接一键生成中英对照可批注 HTML")
    parser.add_argument("url", help="智慧芽免登录分享链接 (包含 signature 等参数)")
    parser.add_argument("--out-dir", default=".", help="输出成果目录 (默认当前目录)")
    parser.add_argument("--out-html", help="指定生成的 HTML 文件路径 (默认自动按公开号命名)")
    parser.add_argument("--api-key", help="DeepSeek API Key (优先使用；若未提供则读取 config.json 或环境变量)")
    parser.add_argument("--zh-json", help="外部自译/AI翻译 JSON 文件路径 (用于覆盖机翻)")
    parser.add_argument("--trans-mode", choices=['patsnap', 'empty'], default='patsnap',
                        help="翻译来源策略: patsnap (默认抓取智慧芽机翻) | empty (不抓机翻，保留骨架待补)")
    parser.add_argument("--with-figures", action=argparse.BooleanOptionalAction, default=True,
                        help="是否拉取官方附图 (路线B：下载内嵌Base64单文件)")
    parser.add_argument("--no-headless", action="store_true", help="显示浏览器窗口 (调试用)")
    parser.add_argument("--browser-path", help="指定浏览器路径 (Chrome / Edge)")

    args = parser.parse_args()

    print("================================================================================")
    print("      智慧芽分享链接 -> 中英对照可批注 HTML 单文件流水线 (patent-anno-share)      ")
    print("================================================================================")
    print(f"[*] 目标链接: {args.url[:70]}...")
    print(f"[*] 输出目录: {os.path.abspath(args.out_dir)}")
    print(f"[*] 拉取附图: {'是 (路线B内嵌)' if args.with_figures else '否'}")

    # 1. 抓取与对齐数据
    patent_data = asyncio.run(fetch_patent_from_share_url(
        args.url,
        headless=not args.no_headless,
        browser_path=args.browser_path,
        zh_json=args.zh_json,
        trans_mode=args.trans_mode,
        with_figures=args.with_figures
    ))

    # 1.5 若配置了 DeepSeek，执行大模型高精专利翻译
    translator = DeepSeekTranslator(api_key=args.api_key)
    if translator.is_configured() and patent_data.get('is_foreign', True):
        print("\n[+] 检测到已绑定 DeepSeek，正在启动大模型高精专利翻译...")
        patent_data = translate_patent_data_sync(patent_data, api_key=args.api_key)
        # 更新术语与附图标记对照
        from fetch_share import extract_reference_numerals
        patent_data['glossary'] = extract_reference_numerals(patent_data.get('claims', []), patent_data.get('description', []))
        print("[✔] DeepSeek 专利翻译全部完成！")

    bib = patent_data.get('biblio', {})
    pn = bib.get('pn') or 'PATENT'
    title = bib.get('title') or ''
    claims = patent_data.get('claims', [])
    desc = patent_data.get('description', [])
    cont = patent_data.get('continuity', {})

    out_dir = os.path.abspath(args.out_dir)
    os.makedirs(out_dir, exist_ok=True)
    inter_dir = os.path.join(out_dir, "_intermediates")
    os.makedirs(inter_dir, exist_ok=True)

    # 2. 导出原始 JSON (存入 _intermediates 子目录)
    raw_path = os.path.join(inter_dir, f"{pn}_patsnap_raw.json")
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(patent_data, f, ensure_ascii=False, indent=2)

    # 3. 导出 Markdown (存入 _intermediates 子目录)
    md_path = os.path.join(inter_dir, f"{pn}_全文_中英对照.md")
    export_as_markdown(patent_data, md_path)

    # 4. 导出 data.json (存入 _intermediates 子目录)
    data_path = os.path.join(inter_dir, f"{pn}_data.json")
    export_as_data_json(patent_data, data_path)

    # 5. 编译单文件 HTML (输出至根目录，命名为 [本机版])
    html_name = args.out_html or f"{pn}_中英对照_[本机版].html"
    if not os.path.isabs(html_name):
        html_path = os.path.join(out_dir, html_name)
    else:
        html_path = html_name

    build_script = os.path.join(SCRIPT_DIR, "build_single.py")
    doc_id = pn
    cmd = [
        sys.executable,
        build_script,
        "--data", data_path,
        "--out", html_path,
        "--doc-id", doc_id,
        "--title", f"{pn} - {title}" if title else pn
    ]

    import subprocess
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    ret = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    if ret.returncode != 0:
        print(f"[!] 生成 HTML 失败: {ret.stderr}")
        sys.exit(1)

    html_size_kb = round(os.path.getsize(html_path) / 1024, 1)

    print("\n================================================================================")
    print("                           [+] HTML 单文件生成完成！                            ")
    print("================================================================================")
    print(f"[*] 文献公开号  : {pn}")
    print(f"[*] 发明名称    : {title or '（未抓取到）'}")
    print(f"[*] 申请人      : {bib.get('applicant') or '（未抓取到）'}")
    print(f"[*] 权利要求项数: {len(claims)} 项")
    print(f"[*] 说明书段落数: {len(desc)} 段 (段号范围: {cont.get('range', '-')})")
    print(f"[*] 附图张数    : {len(patent_data.get('figures', []))} 张 (已内嵌为离线 Base64)")
    gap_str = '无缺口 (完整)' if not cont.get('gaps') else f"存在缺口: {cont.get('gaps')}"
    print(f"[*] 段落缺口核对: {gap_str}")
    print(f"[*] 中英对照 md : {md_path}")
    print(f"[*] 可批注 HTML : {html_path} ({html_size_kb} KB)")
    print("--------------------------------------------------------------------------------")
    print("【使用提示】:")
    print("  1. 生成的 HTML 为 100% 独立单文件，无外部 CDN，无网络请求；")
    print("  2. 双击该 HTML 文件即可在浏览器中打开；")
    print("  3. 鼠标划选任意中文或英文文字，点击「加高亮」即可写备注并持久保存在浏览器中；")
    print("  4. 需要发给他人或跨设备时，使用右上角「导出批注」/「导入批注」即可。")
    print("================================================================================")


if __name__ == "__main__":
    main()
