# -*- coding: utf-8 -*-
"""从查新报告 docx 提取评述下划线摘录（高亮基准，skill: patent-novelty-report）

用途：智慧芽网页高亮截图路线的第 2 步 —— 提取 {段落号: [下划线原文, ...]}，
供浏览器内 innerText 匹配 + DOM span 高亮。

下划线判定：<w:u w:val="single"/> 等 w:val != none 才视为真下划线；
括号评述 run 常为 <w:u w:val="none"/>（实为加粗），须排除。

用法：
  python extract_underlines.py <报告docx> [--paras 0040,0042] [--json out.json]
"""
import re
import os
import json
import zipfile
import argparse


def extract_underlines(docx_path):
    """从报告 docx 提取 {段落号: [下划线原文, ...]}。仅 w:u w:val != none 视为下划线。"""
    z = zipfile.ZipFile(docx_path)
    xml = z.read('word/document.xml').decode('utf-8')
    result = {}
    for para in re.findall(r'<w:p[ >].*?</w:p>', xml, re.S):
        ptext = ''.join(re.findall(r'<w:t[^>]*>(.*?)</w:t>', para, re.S))
        m = re.match(r'\s*\[(\d{4})\]', ptext)
        if not m:
            continue
        para_no = m.group(1)
        u_texts = []
        for r in re.findall(r'<w:r(?: [^>]*)?>.*?</w:r>', para, re.S):
            rtext = ''.join(re.findall(r'<w:t[^>]*>(.*?)</w:t>', r, re.S))
            if not rtext:
                continue
            rpr = re.search(r'<w:rPr>(.*?)</w:rPr>', r, re.S)
            rprs = rpr.group(1) if rpr else ''
            um = re.search(r'<w:u w:val="([^"]+)"/>', rprs)
            if um and um.group(1) != 'none':
                u_texts.append(rtext)
        if u_texts:
            result[para_no] = u_texts
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('docx_path', help='报告 docx')
    ap.add_argument('--paras', default=None, help='限定段落号，逗号分隔')
    ap.add_argument('--json', default=None, help='可选，输出 JSON 文件')
    args = ap.parse_args()

    underlines = extract_underlines(args.docx_path)
    if args.paras:
        allowed = {p.strip() for p in args.paras.split(',') if p.strip()}
        underlines = {k: v for k, v in underlines.items() if k in allowed}
    print(f'待高亮段落: {sorted(underlines)}')
    for k in sorted(underlines):
        for t in underlines[k]:
            print(f'  [{k}] {t}')
    if args.json:
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump(underlines, f, ensure_ascii=False, indent=2)
        print(f'已写 JSON: {args.json}')


if __name__ == '__main__':
    main()