# -*- coding: utf-8 -*-
"""
从专利查新/检索报告中精准提取对比文件列表、智慧芽分享链接、以及正文下划线原文摘录与涉及附图。

核心能力：
1. 兼容 .doc（通过 Word COM 自动无损转换为 .docx）与原生 .docx。
2. 提取相关文献列表区（表格或段落）中的对比文件元数据：
   - 序号（如 相关文献1, 2, 3...）
   - 文献公开号（如 CN112137717B, US20230270954A1, JP2015182082A, DE102020003419A1 等）
   - 国别/语言倾向识别 (CN, US, JP, DE, KR, EP, WO, FR 等)
   - 真实智慧芽免登录分享链接 RLINK（支持 w:hyperlink 及 w:instrText HYPERLINK 代码）
   - 涉及图号/段落范围（从表格关联度或正文中提取）
3. 提取正文评述区（如“检索简要意见”或正文比对）中的段落与下划线引文：
   - 按文献精准分块（相关文献1 -> 相关文献2 -> ...）
   - 提取正文中被引述的全部段落号 [00XX] / 【00XX】（无论是否画下划线，均纳入截取范围，确保段落不遗漏）
   - 提取纯净下划线文本（仅 w:u 且 w:val != 'none'）
   - 自动提取构件附图标记数字与数学符号
   - 提取引述的图号（如 图1a, 1b, 8, Fig. 1, 図1 等）
"""

import os
import re
import sys
import json
import zipfile
import argparse
import xml.etree.ElementTree as ET

NS = {
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
}


def ensure_docx(doc_path):
    """
    确保输入文件为 .docx 格式。
    - 若本身是 .docx：直接返回（0 额外外部依赖，跨平台原生 OpenXML 解析）。
    - 若输入是 .doc：自适应探测 Word / WPS COM 接口或其它转换引擎转换为 .docx。
    - 若缺少 COM 环境：抛出清晰、友好的解决方案引导，杜绝生硬崩溃。
    """
    if not os.path.exists(doc_path):
        raise FileNotFoundError(f"未找到指定的报告文件: {doc_path}")

    if doc_path.lower().endswith('.docx'):
        return doc_path

    if doc_path.lower().endswith('.doc'):
        converted = os.path.splitext(doc_path)[0] + '_converted.docx'
        # 若已有较新的转换件，直接复用
        if os.path.exists(converted) and os.path.getmtime(converted) >= os.path.getmtime(doc_path):
            return converted

        abs_doc = os.path.abspath(doc_path)
        abs_out = os.path.abspath(converted)

        # 尝试通过 Python win32com 调用 Microsoft Word 或 WPS Office
        com_errors = []
        try:
            import win32com.client
            app = None
            # 依次尝试 Word、WPS 个人版、WPS 专业版
            for prog_id in ['Word.Application', 'wps.Application', 'kwps.Application']:
                try:
                    app = win32com.client.Dispatch(prog_id)
                    break
                except Exception as ex_dispatch:
                    com_errors.append(f"{prog_id}: {ex_dispatch}")

            if app is not None:
                app.Visible = False
                try:
                    doc = app.Documents.Open(abs_doc)
                    # FileFormat=12 代表 wdFormatXMLDocument (.docx)
                    doc.SaveAs2(abs_out, FileFormat=12)
                    doc.Close()
                    return abs_out
                finally:
                    try:
                        app.Quit()
                    except Exception:
                        pass
        except ImportError:
            com_errors.append(f"当前 Python 环境 ({sys.executable}) 未安装 pywin32 库 (无法使用 win32com)")
        except Exception as e_com:
            com_errors.append(f"COM 执行异常: {e_com}")

        # 若本地有 LibreOffice / OpenOffice soffice 命令，尝试无头转换
        try:
            import subprocess
            res = subprocess.run(
                ['soffice', '--headless', '--convert-to', 'docx', abs_doc, '--outdir', os.path.dirname(abs_out)],
                capture_output=True, timeout=30
            )
            if res.returncode == 0 and os.path.exists(abs_out):
                return abs_out
        except Exception:
            pass

        # 遇到无法自动转换时，给出专业、清晰、直接的修复指引
        detail_msg = "; ".join(com_errors) if com_errors else "未检测到可用 Office/WPS 接口"
        guide = (
            f"\n"
            f"================================================================================\n"
            f"【文件格式自适应提示】：检测到输入为老式二进制 Word 文档 (.doc)\n"
            f"目标路径: {doc_path}\n"
            f"当前环境 ({sys.executable}) 无法自动将 .doc 转换为 .docx ({detail_msg})\n"
            f"\n"
            f"【跨电脑开箱即用推荐方案】（任选其一即可立即解决）：\n"
            f"  方案 1（强烈推荐，0额外依赖，最快）：\n"
            f"    直接在 Word 或 WPS 中打开该报告，另存为「Word 文档 (*.docx)」格式，\n"
            f"    然后将 .docx 路径传入流水线重新运行即可（直接走原生 OpenXML，零依赖！）。\n"
            f"\n"
            f"  方案 2（若必须自动处理 .doc）：\n"
            f"    请在当前 Python 环境中安装 pywin32 依赖：\n"
            f"      {sys.executable} -m pip install pywin32\n"
            f"    并确保当前电脑已安装 Microsoft Word 或 金山 WPS Office 任意一款办公软件。\n"
            f"================================================================================\n"
        )
        raise RuntimeError(guide)

    raise ValueError(f"不支持的文件格式（仅支持 .docx 或 .doc）: {doc_path}")


def get_country_lang_hint(pn):
    """根据专利公开号前缀推断国家与官方语言。"""
    if not pn:
        return 'UNKNOWN'
    m = re.match(r'^([A-Z]{2})', pn.upper())
    if not m:
        return 'UNKNOWN'
    cc = m.group(1)
    mapping = {
        'CN': 'zh',
        'US': 'en',
        'GB': 'en',
        'JP': 'ja',
        'DE': 'de',
        'KR': 'ko',
        'FR': 'fr',
        'EP': 'multi_ep',
        'WO': 'multi_wo'
    }
    return mapping.get(cc, 'foreign')


def extract_table_patents(doc_root, rel_map):
    """从相关文献表格中提取对比文件列表及对应的超链接与关联度信息。"""
    patents = []
    
    for tbl in doc_root.iterfind('.//w:tbl', NS):
        rows = tbl.findall('.//w:tr', NS)
        if not rows:
            continue
        
        header_row_idx = -1
        col_pn_idx = -1
        col_type_idx = -1
        col_date_idx = -1
        col_rel_idx = -1
        
        for r_idx, row in enumerate(rows):
            cells = row.findall('.//w:tc', NS)
            texts = [''.join(c.itertext()).strip() for c in cells]
            for c_idx, t in enumerate(texts):
                if any(k in t for k in ['文献号', '公开号', '期刊卷号', '申请号']):
                    header_row_idx = r_idx
                    col_pn_idx = c_idx
                elif '类型' in t:
                    col_type_idx = c_idx
                elif '公开日期' in t or '公告日' in t:
                    col_date_idx = c_idx
                elif '关联度' in t or '对应段落' in t or '相关段落' in t:
                    col_rel_idx = c_idx
            if header_row_idx != -1 and col_pn_idx != -1:
                break
                
        if header_row_idx != -1 and col_pn_idx != -1:
            item_seq = 1
            for row in rows[header_row_idx + 1:]:
                cells = row.findall('.//w:tc', NS)
                if len(cells) <= col_pn_idx:
                    continue
                pn_cell = cells[col_pn_idx]
                cell_text = ''.join(pn_cell.itertext()).strip()
                
                clean_cell_text = re.sub(r'HYPERLINK\s*["\'][^"\']+["\']', '', cell_text).strip()
                m_pn = re.search(r'\b([A-Z]{2}\d{5,12}[A-Z\d]*)\b', clean_cell_text)
                if not m_pn:
                    m_pn = re.search(r'\b(CN|US|JP|EP|WO|DE|KR|GB|FR)\d+[A-Z0-9]*\b', clean_cell_text)
                if not m_pn:
                    continue
                pn = m_pn.group(1)
                
                # 提取该单元格内的超链接
                share_url = ''
                h = pn_cell.find('.//w:hyperlink', NS)
                if h is not None:
                    rid = h.attrib.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
                    share_url = rel_map.get(rid, '')
                
                if not share_url:
                    cell_xml = ET.tostring(pn_cell, encoding='utf-8').decode('utf-8')
                    m_url = re.search(r'HYPERLINK\s*["\'](https?://[^"\']+)["\']', cell_xml)
                    if m_url:
                        share_url = m_url.group(1).replace('&amp;', '&')
                
                rel_type = ''.join(cells[col_type_idx].itertext()).strip() if (col_type_idx != -1 and len(cells) > col_type_idx) else ''
                pub_date = ''.join(cells[col_date_idx].itertext()).strip() if (col_date_idx != -1 and len(cells) > col_date_idx) else ''
                rel_info = ''.join(cells[col_rel_idx].itertext()).strip() if (col_rel_idx != -1 and len(cells) > col_rel_idx) else ''
                
                # 从关联度文本中提取段落范围与图号（如 说明书【0002】-【0035】段，图1a、1b、8）
                table_paras = []
                m_range = re.search(r'[\[【](\d{4})[\]】]\s*[-~至到]\s*[\[【](\d{4})[\]】]', rel_info)
                if m_range:
                    s_p = int(m_range.group(1))
                    e_p = int(m_range.group(2))
                    table_paras = [str(i).zfill(4) for i in range(min(s_p, e_p), max(s_p, e_p) + 1)]
                
                table_figs = extract_figure_labels(rel_info)
                
                patents.append({
                    'ref_index': str(item_seq),
                    'pn': pn,
                    'lang_hint': get_country_lang_hint(pn),
                    'type': rel_type,
                    'pub_date': pub_date,
                    'share_url': share_url,
                    'table_paras': table_paras,
                    'table_figs': table_figs
                })
                item_seq += 1

    return patents


def extract_figure_labels(text):
    """从文本中提取引述的图号，如 图1a, 1b, 8, Fig. 1, 図1 等。"""
    figs = []
    if not text:
        return figs
    
    # 模式 1: 中文 "图1a" / "图 1b" / "图1a、1b、8"
    m_cn = re.findall(r'(?:图|附图|Fig\.?|FIG\.?|図|Abb\.?)\s*([0-9]{1,2}[a-z]?(?:[、,，和及与\-~至到\s]+[0-9]{1,2}[a-z]?)*)', text)
    for m in m_cn:
        sub_figs = re.split(r'[、,，和及与\-~至到\s]+', m)
        for sf in sub_figs:
            sf_clean = sf.strip()
            if re.match(r'^\d{1,2}[a-z]?$', sf_clean):
                if sf_clean not in figs:
                    figs.append(sf_clean)

    # 模式 2: 单独的 (参见图8) / (如图1a)
    m_single = re.findall(r'[（(](?:参见|如图|见)?(?:图|Fig\.?|FIG\.?)\s*([0-9]{1,2}[a-z]?)[)）]', text)
    for sf in m_single:
        if sf not in figs:
            figs.append(sf)

    return figs


def extract_reviews_by_patent(doc_root):
    """从正文与单元格中解析每篇对比文件的下划线评述及全部引用段落。"""
    all_paras = []
    for p in doc_root.iterfind('.//w:p', NS):
        p_text = ''.join(p.itertext()).strip()
        if not p_text:
            continue

        # 核心防污染：若遇到了下方对比文件检索列表表格的表头行，正文评述区已完全结束，立即终止后续扫描
        if any(k in p_text for k in ['文献号', '公开号', '期刊卷号']) and any(k in p_text for k in ['公开日期', '相关段落', '关联度', '类型']):
            break
        
        u_texts = []
        for r in p.findall('.//w:r', NS):
            rPr = r.find('w:rPr', NS)
            if rPr is not None:
                u = rPr.find('w:u', NS)
                if u is not None and u.attrib.get('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val', 'single') != 'none':
                    txt = ''.join(r.itertext())
                    if txt:
                        u_texts.append(txt)
        
        all_paras.append({
            'text': p_text,
            'underlines': u_texts
        })

    reviews = {}  # ref_index -> list of para items
    current_ref_idx = None
    # 兼容格式：
    # 1. 相关文献1 / 对比文件1 / 文献1 / 相关文献一
    # 2. D1（公开号）/ D1(公开号) / D1: / D1： / 对比文件D1
    header_pattern = re.compile(
        r'(?:相关文献|对比文件|文献)\s*([0-9一二三四五六七八九十]+)'
        r'|'
        r'^\s*(?:对比文件|文献)?\s*[Dd]([0-9]+)\s*(?:[（(][A-Za-z0-9]+[）)]|[:：]|\s)'
    )
    # 结构化评述闭合：遇到“对比可知”、“未公开”、“建议补充”等总结段落或大章节标题，自然闭合文献收集状态
    close_review_pattern = re.compile(
        r'^(?:对比可知|由此可知|综上所述|检索结论|分析意见|未公开|建议补充|【结论】|审查意见|对比分析|新颖性[、，与和]?创造性|新创性评述|关于独立权利要求|关于从属权利要求)'
    )
    major_section_pattern = re.compile(r'^(?:[一二三四五六七八九十]+|[0-9]+)[、.\s]+(?!相关文献|对比文件|文献|D|d).*(?:结论|评价|意见|汇总|说明|附件|附录)')
    cn_num_map = {'一': '1', '二': '2', '三': '3', '四': '4', '五': '5', '六': '6', '七': '7', '八': '8', '九': '9', '十': '10'}

    for p_info in all_paras:
        txt = p_info['text'].strip()
        if not txt:
            continue

        # 1. 遇到大章节标题或总结性评述段落，自然闭合文献收集
        if current_ref_idx is not None:
            if close_review_pattern.search(txt) or (major_section_pattern.search(txt) and len(txt) <= 40):
                current_ref_idx = None
                continue

        # 2. 遇到新的文献小标题（如“相关文献1”、“对比文件2评述”、“D1（CN121762858A）”），自然切换到新文献
        m = header_pattern.search(txt)
        if m and (len(txt) <= 60 or '：' in txt or ':' in txt or '意见' in txt or '评述' in txt or '公开' in txt or '比对' in txt or re.search(r'[\[【]\d{4}[\]】]', txt)):
            # 排除表格表头（“公开号”和“申请人”并存的情况）
            if not ('公开号' in txt and '申请人' in txt):
                raw_idx = m.group(1) or m.group(2)
                new_ref_idx = cn_num_map.get(raw_idx, raw_idx)
                current_ref_idx = new_ref_idx
                if current_ref_idx not in reviews:
                    reviews[current_ref_idx] = []
                reviews[current_ref_idx].append(p_info)
                continue

        # 3. 收集当前文献范围内的所有段落
        if current_ref_idx is not None:
            reviews[current_ref_idx].append(p_info)

    # 结构化提炼每篇文献的高亮段落、片段与涉及图号
    structured_reviews = {}
    for ref_idx, p_list in reviews.items():
        para_groups = {}
        all_mentioned_paras = set()
        all_hls = []
        review_figs = []

        for p_info in p_list:
            txt = p_info['text']
            u_list = p_info['underlines']

            # 无论是否有下划线，只要正文提及了段落号 [00XX]，均收录至 all_mentioned_paras
            for pm in re.finditer(r'[\[【](\d{4})[\]】]', txt):
                all_mentioned_paras.add(pm.group(1))

            # 提取正文评述中涉及的图号
            p_figs = extract_figure_labels(txt)
            for f in p_figs:
                if f not in review_figs:
                    review_figs.append(f)

            # 提取段落号 [00XX]
            para_m = re.search(r'[\[【](\d{4})[\]】]', txt)
            para_no = para_m.group(1) if para_m else 'unknown'

            # 识别传统外国专利无段号时的“页/栏/行”引用（如“第3页第2栏”、“第4栏第15-25行”、“Col 2, Line 15”）
            page_col_m = re.findall(r'(?:第\s*[0-9一二三四五六七八九十]+\s*[页頁欄栏]|col(?:umn)?\.?\s*\d+|line\s*\d+|第\s*\d+\s*行)', txt, re.I)
            page_col_hint = ' '.join(page_col_m) if page_col_m else ''

            if para_no == 'unknown' and not u_list:
                # 若无段号且无下划线，仅保留评述正文首句概述
                if not any(k in txt for k in ['公开了', '装置', '设备', '方法', '方案', '系统']):
                    continue

            if para_no not in para_groups:
                para_groups[para_no] = []

            # 处理下划线
            for raw_u in u_list:
                cleaned = clean_underline_text(raw_u)
                if not cleaned:
                    continue
                segments = split_ellipsis(cleaned)
                for seg in segments:
                    if len(seg) >= 3:
                        markers = re.findall(r'(?<![A-Za-z0-9])(\d{1,4}[a-z′\']?)(?![A-Za-z0-9])', seg)
                        math_symbols = re.findall(r'[A-Za-z]+[0-9a-z]?|[=+\-/Δ\\]+', seg)
                        hl_item = {
                            'para_no': para_no,
                            'context': txt[:140],
                            'page_col_hint': page_col_hint,
                            'raw': raw_u,
                            'segment': seg,
                            'markers': markers,
                            'math_symbols': [s for s in math_symbols if len(s) >= 2 or s in ['Δ', '=']],
                            'need_deduce': (para_no == 'unknown')
                        }
                        para_groups[para_no].append(hl_item)
                        all_hls.append(hl_item)

        structured_reviews[ref_idx] = {
            'para_groups': para_groups,
            'all_mentioned_paras': sorted(list(all_mentioned_paras)),
            'all_highlights': all_hls,
            'review_figs': review_figs
        }

    return structured_reviews


def clean_underline_text(text):
    """清洗下划线原文：去除段号前缀、尾部左括号等，过滤超链接代码。"""
    t = text.strip()
    if 'HYPERLINK' in t or 'http://' in t or 'https://' in t:
        return ''
    t = re.sub(r'^[\[【]\d{4}[\]】][\s:：]*', '', t)
    t = re.sub(r'[\(（]$', '', t)
    return t.strip()


def split_ellipsis(text):
    """按省略号智能拆分片段，过滤掉过短的片段。"""
    parts = re.split(r'…{1,3}|\.{3,6}|……', text)
    result = []
    for p in parts:
        p_clean = p.strip(' ，,。、：:；;')
        if len(p_clean) >= 4:
            result.append(p_clean)
    return result if result else ([text] if len(text) >= 4 else [])


def parse_report(docx_path):
    """综合解析报告主流程。"""
    real_docx = ensure_docx(docx_path)
    z = zipfile.ZipFile(real_docx)

    rels_xml = z.read('word/_rels/document.xml.rels').decode('utf-8')
    rels_root = ET.fromstring(rels_xml)
    rel_map = {rel.attrib.get('Id'): rel.attrib.get('Target') for rel in rels_root}

    doc_xml = z.read('word/document.xml').decode('utf-8')
    doc_root = ET.fromstring(doc_xml)

    # 1. 提取表格对比文件元数据及超链接
    patents = extract_table_patents(doc_root, rel_map)

    # 2. 提取正文评述段落、下划线及图号
    reviews = extract_reviews_by_patent(doc_root)

    # 3. 融合对齐
    final_patents = []
    for p in patents:
        ref_idx = p['ref_index']
        rev = reviews.get(ref_idx, {'para_groups': {}, 'all_mentioned_paras': [], 'all_highlights': [], 'review_figs': []})

        # 核心：必须合并【正文中提及的所有段落】与【下划线段落】以及【表格指定段落】
        combined_paras = set(rev['all_mentioned_paras'])
        for pno in rev['para_groups'].keys():
            if pno != 'unknown':
                combined_paras.add(pno)
        for pno in p.get('table_paras', []):
            combined_paras.add(pno)

        target_paras = sorted(list(combined_paras))

        # 合并涉及图号
        combined_figs = []
        for f in p.get('table_figs', []) + rev.get('review_figs', []):
            if f not in combined_figs:
                combined_figs.append(f)

        has_undeduced = (len(target_paras) == 0 and len(rev['all_highlights']) > 0)

        final_patents.append({
            'ref_index': ref_idx,
            'pn': p['pn'],
            'lang_hint': p['lang_hint'],
            'type': p['type'],
            'pub_date': p['pub_date'],
            'share_url': p['share_url'],
            'target_paras': target_paras,
            'target_figs': combined_figs,
            'para_groups': rev['para_groups'],
            'all_highlights': rev['all_highlights'],
            'has_undeduced_paras': has_undeduced
        })

    # 正文有评述但表格无记录的兜底
    known_indices = {p['ref_index'] for p in patents}
    for ref_idx, rev in reviews.items():
        if ref_idx not in known_indices:
            combined_paras = set(rev['all_mentioned_paras'])
            for pno in rev['para_groups'].keys():
                if pno != 'unknown':
                    combined_paras.add(pno)
            final_patents.append({
                'ref_index': ref_idx,
                'pn': '',
                'lang_hint': 'UNKNOWN',
                'type': '',
                'pub_date': '',
                'share_url': '',
                'target_paras': sorted(list(combined_paras)),
                'target_figs': rev.get('review_figs', []),
                'para_groups': rev['para_groups'],
                'all_highlights': rev['all_highlights']
            })

    return {
        'docx_path': real_docx,
        'total_patents': len(final_patents),
        'patents': final_patents
    }


def main():
    parser = argparse.ArgumentParser(description="从报告 docx 解析对比文件、下划线及附图标记")
    parser.add_argument("docx_path", help="报告路径 (.docx 或 .doc)")
    parser.add_argument("--json", default=None, help="输出 JSON 路径")
    args = parser.parse_args()

    data = parse_report(args.docx_path)
    print("==================================================")
    print(f"成功解析报告: {args.docx_path}")
    print(f"提取到对比文件总数: {data['total_patents']}")
    print("==================================================")
    for p in data['patents']:
        pn = p['pn'] or '未知PN'
        url_preview = p['share_url'][:55] + '...' if p['share_url'] else '【无URL】'
        hl_count = len(p['all_highlights'])
        print(f"★ [相关文献{p['ref_index']}] {pn} ({p['type']} / 语种:{p['lang_hint']})")
        print(f"   URL: {url_preview}")
        print(f"   目标涵盖段落: {p['target_paras']} (包含正文提及与下划线)")
        print(f"   涉及图号: {p['target_figs']}")
        print(f"   下划线特征数: {hl_count}")

    if args.json:
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"\n已完整写入结构化数据: {args.json}")


if __name__ == '__main__':
    main()
