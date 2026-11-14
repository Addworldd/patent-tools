# -*- coding: utf-8 -*-
"""
批量链接与表格解析工具 (batch_parser.py)
支持解析：
1. 包含超链接的 Excel 文件 (.xlsx, .xls)
2. CSV 文件
3. 多行任意文本（自动提取所有包含 patsnap/zhihuiya 的链接和对应公开号）
"""
import re
import os
from typing import List, Dict, Any

URL_PAT = re.compile(r'https?://[^\s"\'<>]+(?:zhihuiya\.com|patsnap\.com)[^\s"\'<>]*', re.IGNORECASE)
PN_PAT = re.compile(r'[A-Z]{2}\s*\d{6,14}\s*[A-Z0-9]*', re.IGNORECASE)

def parse_text_links(text: str) -> List[Dict[str, Any]]:
    """从纯文本或多行文本中提取所有链接及关联公开号"""
    results = []
    lines = text.strip().splitlines()
    seen_urls = set()

    for line in lines:
        line_clean = line.strip()
        if not line_clean:
            continue
        
        # 查找行中的 URL
        urls = URL_PAT.findall(line_clean)
        if not urls:
            # 兼容非 zhihuiya 域名的标准通用 URL
            urls = re.findall(r'https?://[^\s"\'<>]+', line_clean)
        
        for u in urls:
            u_clean = u.rstrip(',;)"\'')
            if u_clean in seen_urls:
                continue
            seen_urls.add(u_clean)
            
            # 尝试从行文本中找公开号
            pns = PN_PAT.findall(line_clean.replace(u, ''))
            pn = pns[0].replace(' ', '').upper() if pns else ""
            if not pn:
                # 尝试从 URL 参数中提取
                m_pn = re.search(r'[?&](?:patentId|docId|pn)=([a-zA-Z0-9_\-]+)', u_clean)
                if m_pn:
                    pn = m_pn.group(1)
            
            results.append({
                "index": len(results) + 1,
                "pn": pn or f"PATENT_{len(results)+1}",
                "url": u_clean
            })
            
    return results


def parse_excel_file(file_path: str) -> List[Dict[str, Any]]:
    """从 Excel 文件中提取所有超链接（包含单元格文字后隐藏的 hyperlink）"""
    import openpyxl

    results = []
    seen_urls = set()

    wb = openpyxl.load_workbook(file_path, data_only=False)
    for sheetname in wb.sheetnames:
        ws = wb[sheetname]
        for row in ws.iter_rows(values_only=False):
            row_text = []
            found_url = None
            found_pn = None

            for cell in row:
                val = str(cell.value or '').strip()
                if val:
                    row_text.append(val)
                
                # 检查单元格内嵌超链接
                if cell.hyperlink and cell.hyperlink.target:
                    target = str(cell.hyperlink.target).strip()
                    if 'http' in target:
                        found_url = target

                # 检查单元格文本本身是否是 URL
                if 'http' in val and not found_url:
                    m = re.search(r'https?://[^\s"\'<>]+', val)
                    if m:
                        found_url = m.group(0)

                # 检查单元格是否是公开号
                if not found_pn:
                    m_pn = PN_PAT.search(val)
                    if m_pn and len(val) <= 25:
                        found_pn = m_pn.group(0).replace(' ', '').upper()

            if found_url and found_url not in seen_urls:
                seen_urls.add(found_url)
                if not found_pn:
                    m_pn = PN_PAT.search(" ".join(row_text))
                    if m_pn:
                        found_pn = m_pn.group(0).replace(' ', '').upper()
                
                results.append({
                    "index": len(results) + 1,
                    "pn": found_pn or f"PATENT_{len(results)+1}",
                    "url": found_url
                })

    return results
