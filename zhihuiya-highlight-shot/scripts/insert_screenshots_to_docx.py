# -*- coding: utf-8 -*-
"""
将生成的高清高亮截图与附图自动回填插入到查新/检索报告 docx 中。

排版规范（严格按最新工业级规范执行）：
1. 插入位置：直接在正文末尾插入分页符另起一页（彻底去除【对比文件说明书关键段落高亮截图对照】大标题）。
2. 小标题格式：加粗的宋体小五（9pt）：
   相关文献{ref_idx}({pn}-{title}-{applicant})
   例如：相关文献1(CN112137717B-一种窗口防尘装置、激光设备及窗口防尘方法-上海瑞柯恩医疗技术股份有限公司)
3. 排版规则：
   - 小标题后另起一行直接放截图！
   - 绝不放任何文字评述！
   - 不需要图号、图题或图片说明文字！
   - 说明书截图顺序居中排列放置，标准宽度：15.5cm（约 6.10 英寸）。
   - 评述涉及的附图以 1 行 N 列无边框表格并排居中排列（每图宽度约 7.5cm / 2.95 英寸），整齐美观。
"""

import os
import re
import sys
import docx
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import qn, nsdecls


def set_run_font(run, font_name="宋体", size_pt=9, bold=True, color_rgb=(0, 0, 0)):
    """设置 run 的中英文字体、字号、加粗与颜色。"""
    run.font.name = font_name
    run.font.size = Pt(size_pt)
    run.bold = bold
    run.font.color.rgb = RGBColor(*color_rgb)
    rPr = run._r.get_or_add_rPr()
    rFonts = OxmlElement('w:rFonts')
    rFonts.set(qn('w:ascii'), font_name)
    rFonts.set(qn('w:hAnsi'), font_name)
    rFonts.set(qn('w:eastAsia'), font_name)
    rPr.append(rFonts)


def append_highlight_section(doc_path, processed_results, out_doc_path=None):
    """
    将处理结果回填到 docx 中。
    processed_results: list of dict, 每个包含:
      - ref_index: '1'
      - pn: 'CN112137717B'
      - title: '一种窗口防尘装置、激光设备及窗口防尘方法'
      - applicant: '上海瑞柯恩医疗技术股份有限公司'
      - screenshots: list of {'path': ..., 'filename': ...}
      - figures: list of {'path': ..., 'label': ...} (可选)
    """
    if not out_doc_path:
        out_doc_path = os.path.splitext(doc_path)[0] + '_已插入高亮截图.docx'

    doc = docx.Document(doc_path)

    # 容错保障：部分第三方编辑器（WPS/转换器）导出的文档可能缺失根级节属性 sectPr
    # 若缺失则自动补全，防止在调用 doc.add_table() 计算页面宽度时抛出 IndexError: list index out of range
    if len(doc.sections) == 0:
        sectPr = parse_xml(
            f'<w:sectPr {nsdecls("w")}>'
            f'<w:pgSz w:w="11906" w:h="16838"/>'
            f'<w:pgMar w:top="1440" w:right="1800" w:bottom="1440" w:left="1800"/>'
            f'</w:sectPr>'
        )
        doc._element.body.append(sectPr)

    # 1. 直接在正文末尾另起一页（不加大标题）
    doc.add_page_break()

    for item in processed_results:
        ref_idx = str(item.get('ref_index', '')).strip()
        pn = str(item.get('pn', '')).strip()
        title = str(item.get('title', '')).strip() or '未知发明名称'
        applicant = str(item.get('applicant', '')).strip() or '未知申请人'
        screenshots = item.get('screenshots', [])
        figures = item.get('figures', [])

        if not screenshots and not figures:
            continue

        # 2. 小标题行：加粗宋体小五（9pt）
        # 格式严格为：相关文献X(公开号-发明名称-申请人)
        p_title = doc.add_paragraph()
        p_title.paragraph_format.space_before = Pt(12)
        p_title.paragraph_format.space_after = Pt(4)
        p_title.paragraph_format.line_spacing = 1.15

        title_text = f"相关文献{ref_idx}({pn}-{title}-{applicant})"
        r_title = p_title.add_run(title_text)
        set_run_font(r_title, font_name="宋体", size_pt=9, bold=True)

        # 3. 说明书截图插入逻辑（严格按照：外文公布原文截图全部放完 -> 机翻截图全部放完 -> 附图表格）
        screenshots_orig = item.get('screenshots_orig', [])
        screenshots_trans = item.get('screenshots_trans', [])
        if not screenshots_orig and not screenshots_trans:
            screenshots_orig = item.get('screenshots', [])

        # 3.1 公布原文截图（外文/中文正文原件）
        for shot in screenshots_orig:
            img_path = shot.get('path', '')
            if not os.path.exists(img_path):
                continue

            p_img = doc.add_paragraph()
            p_img.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p_img.paragraph_format.space_before = Pt(3)
            p_img.paragraph_format.space_after = Pt(6)
            run_img = p_img.add_run()
            # 标准宽度 15.5cm (约 6.10 英寸)
            run_img.add_picture(img_path, width=Inches(6.10))

        # 3.2 智慧芽机翻截图（外文专利对照中文译文）
        for shot in screenshots_trans:
            img_path = shot.get('path', '')
            if not os.path.exists(img_path):
                continue

            p_img = doc.add_paragraph()
            p_img.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p_img.paragraph_format.space_before = Pt(3)
            p_img.paragraph_format.space_after = Pt(6)
            run_img = p_img.add_run()
            # 标准宽度 15.5cm (约 6.10 英寸)
            run_img.add_picture(img_path, width=Inches(6.10))

        # 4. 附图以 1 行 N 列无边框表格并排居中排版
        valid_figs = [f for f in figures if os.path.exists(f.get('path', ''))]
        if valid_figs:
            cols = len(valid_figs)
            # 若附图较多，每行最多并排 2~3 张
            chunk_size = 2 if cols > 1 else 1
            for chunk_idx in range(0, cols, chunk_size):
                sub_figs = valid_figs[chunk_idx:chunk_idx + chunk_size]
                table = doc.add_table(rows=1, cols=len(sub_figs))
                table.alignment = WD_TABLE_ALIGNMENT.CENTER
                # 每图宽度约 2.95 英寸 (约 7.5cm)
                cell_width = Inches(2.95) if len(sub_figs) > 1 else Inches(4.5)

                for c_idx, fig_item in enumerate(sub_figs):
                    cell = table.cell(0, c_idx)
                    p_c = cell.paragraphs[0]
                    p_c.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    p_c.paragraph_format.space_before = Pt(2)
                    p_c.paragraph_format.space_after = Pt(4)
                    r_c = p_c.add_run()
                    r_c.add_picture(fig_item['path'], width=cell_width)

                # 去除表格边框
                tblPr = table._tbl.tblPr
                tblBorders = OxmlElement('w:tblBorders')
                for border_name in ['top', 'left', 'bottom', 'right', 'insideH', 'insideV']:
                    border = OxmlElement(f'w:{border_name}')
                    border.set(qn('w:val'), 'none')
                    tblBorders.append(border)
                tblPr.append(tblBorders)

    # 安全保存（避免权限占用崩溃）
    real_out = out_doc_path
    for retry in range(10):
        try:
            doc.save(real_out)
            break
        except PermissionError:
            base, ext = os.path.splitext(out_doc_path)
            real_out = f"{base}_{retry+1}{ext}"

    print(f"[+] 成果已成功回填并保存至: {real_out}")
    return real_out


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("用法: python insert_screenshots_to_docx.py <docx_path> <results.json> [out_docx]")
        sys.exit(1)

    doc_p = sys.argv[1]
    with open(sys.argv[2], 'r', encoding='utf-8') as f:
        res_data = json.load(f)
    out_p = sys.argv[3] if len(sys.argv) > 3 else None
    append_highlight_section(doc_p, res_data, out_p)
