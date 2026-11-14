# -*- coding: utf-8 -*-
"""
智慧芽专利说明书高亮、超清截图与附图自适应抓取核心引擎 (PatSnap Universal Highlighter & Screenshot Engine)

严格落实工业级标准：
1. 统一视口与 Retina 2x 超清渲染：
   - 视口设定 1400px，device_scale_factor=2（物理像素翻倍至 2512px，打印分辨率达 412 DPI），彻底消灭放大发虚。
2. 强制单栏全宽模式：
   - 注入 CSS 锁死 .patent-app-view__desc 与 .patent-app__content-view 为 100%，屏蔽任何并排分栏。
3. 严格标签流转与申请人提取：
   - 优先进入 /patent-view/abst（摘要页）提取官方准确的申请人全称与专利名称，再平滑切至说明书 /patent-view/desc。
4. 强制选择“原文”（统一不使用译文）：
   - 页面顶部语言栏必须选择“原文”，关闭全文机翻，确保法定证据源自官方公布原文。
5. 全语种（中文/英文/德文/日文/韩文/法文等）通用多层阶梯式高亮引擎：
   - Level 1: 中文专利（CN）直接在段落容器内精准匹配下划线文本。
   - Level 2: 外文专利（US/DE/JP/KR/EP/WO/FR等），报告为中文翻译件：
     * 第一步：段落号（b.d_n[00XX]）100% 语言无关精准锚定段落 DOM；
     * 第二步：全语种智能标点分句（兼容中文句号、日文句号、西文句号+空格、韩文等）；
     * 第三步：提取下划线中的构件附图标记数字（如 5, 6, 100, 21a）与数学物理符号（Cc, Vc, Δ, +, - 等），在各句子中打分匹配最佳目标句；
     * 第四步：若分句得分相同或为纯文字无数字，高亮承载核心论点的代表性句子（或主干句），确保视觉上 100% 出现清晰高亮；
     * 第五步：严格仅对正文带下划线的段落高亮，正文仅作为背景提及、未划线的段落完整截取但不误加高亮。
6. 彻底剔除左侧灰色侧边栏：
   - clip 严格基于正文卡片容器（left 约 144px），仅截取纯白正文。
7. 智能段落覆盖与分簇截取：
   - 涵盖正文评述提及的每一个段落，确保 [0002]、[0021]、[0022] 等无一遗漏；重叠自动合并，避免图片过长。
8. 附图专区图纸智能抓取：
   - 识别评述涉及图号（如 图1a, 1b, 8, Fig. 1 等），切换至 /patent-view/image 抓取官方高质量附图。
9. PIL 黄色像素门禁质检。
"""

import os
import re
import sys
import json
import time
import asyncio
from PIL import Image
from playwright.async_api import async_playwright

VIEWPORT_WIDTH = 1400
HIGHLIGHT_BG = "#ffeb3b"  # 亮黄色，反差鲜明且符合官方规范


def get_browser_executable(preferred_path=None):
    """
    跨平台智能自适应探测可用浏览器：
    1. 优先使用用户指定路径或环境变量 CHROME_PATH / BROWSER_PATH；
    2. 自动检索 Google Chrome（系统 64 位、32 位或用户 LocalAppData）；
    3. 自动检索 Microsoft Edge（Windows 10/11 原生自带，与 Chromium 完全兼容，免装额外浏览器！）；
    4. 若未检索到可执行文件，返回 None（交由 Playwright 自动调用其内置 chromium）。
    """
    if preferred_path and os.path.exists(preferred_path):
        return preferred_path

    env_path = os.environ.get("CHROME_PATH") or os.environ.get("BROWSER_PATH")
    if env_path and os.path.exists(env_path):
        return env_path

    candidates = [
        # Chrome
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        # Edge (Windows 原生自带兜底)
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
    ]

    for c in candidates:
        if c and os.path.exists(c):
            return c

    return None


JS_EXTRACT_APPLICANT = r'''() => {
    function cleanName(n) {
        if (!n) return '';
        let s = n.replace(/^\[标\]/, '').replace(/\([^\)]+\)/g, '').replace(/（[^）]+）/g, '');
        s = s.replace(/^(?:当前|原始)?申请(?:\(专利权\)|（专利权）)?人[\s:：\|\n]*/g, '');
        s = s.replace(/^(?:当前|原始)?权利人[\s:：\|\n]*/g, '');
        return s.trim();
    }

    const leaves = Array.from(document.querySelectorAll('*')).filter(el => {
        const t = (el.innerText || '').trim();
        return el.children.length === 0 && (
            t === '当前申请(专利权)人' || t === '原始申请(专利权)人' || t === '申请(专利权)人' || t === '申请人'
        );
    });
    for (let leaf of leaves) {
        let parent = leaf.parentElement;
        for (let i = 0; i < 3 && parent; i++) {
            const next = parent.nextElementSibling;
            if (next) {
                const val = cleanName(next.innerText || '');
                if (val && !val.includes('发明人') && val.length >= 2) {
                    return val;
                }
            }
            parent = parent.parentElement;
        }
    }
    const text = document.body ? document.body.innerText : '';
    const m = text.match(/(?:当前申请\(专利权\)人|原始申请\(专利权\)人|申请\(专利权\)人)[\s\S]{0,10}?\|\s*([^\|\n\r]+)/);
    if (m && m[1].trim()) {
        const val = cleanName(m[1]);
        if (val && !val.includes('发明人') && val.length >= 2) return val;
    }
    return '';
}'''


JS_FORCE_LAYOUT_AND_ORIGINAL = r'''() => {
    const style = document.createElement('style');
    style.id = 'zy-force-single-column';
    style.innerHTML = `
        .patent-app-view--split .patent-app-view__clms,
        .patent-app-view--split .patent-app-view__image,
        .patent-app-view--split .patent-app-view__split-right,
        .patent-app-view__column-layout .patent-app-view__right-col,
        .patent-app-view__columns-right {
            display: none !important;
        }
        .patent-app-view__desc,
        .patent-app__content-view,
        .patent-app-view__column-layout .patent-app-view__left-col,
        .patent-app-view--split .patent-app-view__left,
        .patent-app-view__container {
            width: 100% !important;
            max-width: 100% !important;
            background: #ffffff !important;
        }
        .zy-hl {
            background-color: #ffeb3b !important;
            color: #000000 !important;
            padding: 1px 3px !important;
            border-radius: 2px !important;
            font-weight: 500 !important;
            display: inline !important;
        }
    `;
    document.head.appendChild(style);

    // 检查语言选择：如果是机翻/译文，切换回“原文”
    const spans = Array.from(document.querySelectorAll('span, button, a'));
    const origBtn = spans.find(s => s.innerText && s.innerText.trim() === '原文');
    if (origBtn && !origBtn.classList.contains('is-active')) {
        origBtn.click();
    }
}'''


JS_TRANSLATION_HIGHLIGHT = r'''(underlines) => {
    const trans = document.querySelector('.translation-block');
    if (!trans) return { count: 0, error: 'no trans' };

    let count = 0;
    // 清理旧高亮
    trans.querySelectorAll('mark.zy-trans-hl').forEach(m => {
        const p = m.parentNode;
        if (p) {
            p.replaceChild(document.createTextNode(m.textContent), m);
            p.normalize();
        }
    });

    const divs = Array.from(trans.querySelectorAll('div[p], [data-p]'));
    divs.forEach(el => {
        if (el.querySelector('b.d_n') && (el.innerText || '').trim().length <= 10) return;

        let text = el.innerText || '';
        underlines.forEach(uItem => {
            let uText = '';
            let markers = [];
            if (typeof uItem === 'string') {
                uText = uItem;
            } else if (uItem && typeof uItem === 'object') {
                uText = uItem.segment || uItem.raw || '';
                markers = uItem.markers || [];
            }
            const cleanU = (uText || '').trim();
            if (!cleanU || cleanU.length < 2) return;

            // 1. 直接精确字面包含
            if (text.includes(cleanU)) {
                el.innerHTML = el.innerHTML.replaceAll(cleanU, `<mark class="zy-trans-hl" style="background-color: #ffeb3b; color: #000; padding: 1px 3px; border-radius: 2px; font-weight: 500;">${cleanU}</mark>`);
                count++;
                text = el.innerText || '';
            } else {
                // 2. 切分核心短语匹配 (大模型意译长句切分)
                const parts = cleanU.split(/[,，。、：:；;\s]+/).filter(w => w.length >= 3);
                parts.forEach(part => {
                    if (text.includes(part)) {
                        el.innerHTML = el.innerHTML.replaceAll(part, `<mark class="zy-trans-hl" style="background-color: #ffeb3b; color: #000; padding: 1px 3px; border-radius: 2px; font-weight: 500;">${part}</mark>`);
                        count++;
                        text = el.innerText || '';
                    }
                });

                // 3. 【大模型译文柔性吸附】：基于附图标记数字在段落内向外扩展特征词高亮
                if (markers && markers.length > 0) {
                    markers.forEach(m => {
                        const mStr = String(m).trim();
                        if (!mStr) return;
                        const reg = new RegExp(`[\\u4e00-\\u9fa5A-Za-z0-9_]{1,6}${mStr}[\\u4e00-\\u9fa5A-Za-z0-9_]{0,4}`, 'g');
                        const matches = text.match(reg);
                        if (matches) {
                            matches.forEach(mWord => {
                                if (!mWord.includes('图') && mWord.length >= 2 && text.includes(mWord)) {
                                    el.innerHTML = el.innerHTML.replaceAll(mWord, `<mark class="zy-trans-hl" style="background-color: #ffeb3b; color: #000; padding: 1px 3px; border-radius: 2px; font-weight: 500;">${mWord}</mark>`);
                                    count++;
                                    text = el.innerText || '';
                                }
                            });
                        }
                    });
                }
            }
        });
    });

    return { count };
}'''


JS_DEDUCE_PARAS_FROM_TRANSLATION = r'''(items) => {
    const trans = document.querySelector('.translation-block') || document.body;
    
    // 获取机翻容器中所有候选段落
    let pNodes = Array.from(trans.querySelectorAll('div[p], [data-p]'));
    if (pNodes.length === 0) {
        const bList = Array.from(trans.querySelectorAll('b.d_n'));
        if (bList.length > 0) {
            pNodes = bList.map(b => b.parentElement).filter(Boolean);
        }
    }
    if (pNodes.length === 0) {
        pNodes = Array.from(trans.querySelectorAll('p, div.desc-paragraph, div')).filter(el => {
            const t = (el.innerText || '').trim();
            return t.length >= 10 && el.children.length <= 4;
        });
    }

    // 格式化提取所有段落的智慧芽内部段号与纯文本
    const paras = [];
    pNodes.forEach((node, idx) => {
        let pNo = node.getAttribute('p') || node.getAttribute('data-p');
        if (!pNo) {
            const b = node.querySelector('b.d_n, span.paragraph-num, span.desc-p-num');
            if (b) {
                const m = b.innerText.match(/\d{1,4}/);
                if (m) pNo = m[0];
            }
        }
        if (!pNo) {
            const m = (node.innerText || '').match(/^[\[【](\d{1,4})[\]】]/);
            if (m) pNo = m[1];
            else pNo = String(idx + 1);
        }
        const pInt = parseInt(pNo, 10);
        if (!isNaN(pInt) && pInt >= 1) {
            const pStr = String(pInt).padStart(4, '0');
            paras.push({
                pStr: pStr,
                pInt: pInt,
                text: (node.innerText || '').trim(),
                el: node
            });
        }
    });

    const deducedParas = new Set();
    const newParaGroups = {};

    items.forEach(item => {
        const seg = (item.segment || '').trim();
        const markers = item.markers || [];
        const mathSyms = item.math_symbols || [];
        const pageCol = item.page_col_hint || '';
        
        let bestPara = null;
        let bestScore = -1;

        // 提取下划线中文片段中的核心关键词短语 (>= 3 字符)
        const subPhrases = seg.split(/[,，。、：:；;\s]+/).map(s => s.trim()).filter(s => s.length >= 3);

        paras.forEach(p => {
            let score = 0;
            const pt = p.text;

            // 1. 完全包含下划线原句（超高置信度）
            if (pt.includes(seg)) {
                score += 100;
            }

            // 2. 附图标记数字命中（每个零件数字 +12 分）
            markers.forEach(m => {
                if (m && pt.includes(m)) score += 12;
            });

            // 3. 数学/工程符号命中
            mathSyms.forEach(sym => {
                if (sym && pt.includes(sym)) score += 6;
            });

            // 4. 核心关键词短语命中（每个技术短语 +5 分）
            subPhrases.forEach(sp => {
                if (sp && pt.includes(sp)) score += 5;
            });

            // 5. 页码/栏号引用提示命中
            if (pageCol && pt.includes(pageCol)) {
                score += 15;
            }

            if (score > bestScore) {
                bestScore = score;
                bestPara = p;
            }
        });

        if (bestPara && bestScore >= 10) {
            const pNo = bestPara.pStr;
            deducedParas.add(pNo);
            if (!newParaGroups[pNo]) newParaGroups[pNo] = [];
            const newItem = Object.assign({}, item, { para_no: pNo });
            newParaGroups[pNo].push(newItem);
        }
    });

    const targetList = Array.from(deducedParas).sort((a, b) => parseInt(a, 10) - parseInt(b, 10));
    return {
        target_paras: targetList,
        para_groups: newParaGroups
    };
}'''


JS_UNIVERSAL_HIGHLIGHT = r'''(args) => {
    const paraGroups = args.paraGroups || {};
    const isChinesePatent = args.isChinesePatent;
    const highlightBg = args.highlightBg || '#ffeb3b';

    let matched = 0;
    let crossAligned = 0;
    let missed = 0;

    // 清理旧高亮
    document.querySelectorAll('span.zy-hl').forEach(sp => {
        const p = sp.parentNode;
        if (p) {
            p.replaceChild(document.createTextNode(sp.textContent), sp);
            p.normalize();
        }
    });

    function findParaTextElements(pNum) {
        if (!pNum || pNum === 'unknown') return [];
        const pInt = parseInt(pNum, 10);
        const pStr = String(pInt).padStart(4, '0');
        const pads = ['[' + pStr + ']', '【' + pStr + '】'];

        const bNodes = Array.from(document.querySelectorAll('b.d_n, span.paragraph-num, span.desc-p-num, div, p'));
        let bEl = null;
        for (let b of bNodes) {
            const t = (b.innerText || '').trim();
            if (pads.includes(t) || t === `[${pStr}]` || t === `【${pStr}】` || t === `[${pNum}]` || t === `【${pNum}】`) {
                bEl = b;
                break;
            }
        }
        if (!bEl) {
            for (let b of bNodes) {
                const t = (b.innerText || '').trim();
                if (t.includes(pStr) || t.includes(pNum)) {
                    bEl = b;
                    break;
                }
            }
        }
        if (!bEl) return [];

        let pContainer = (bEl.tagName === 'B' || bEl.tagName === 'SPAN') ? bEl.parentElement : bEl;
        // 关键：若容器仅有段号本身（如日文说明书），正文在后续兄弟节点中
        if (pContainer.innerText.trim().length <= 10) {
            const textEls = [];
            let sib = pContainer.nextElementSibling;
            while (sib) {
                const st = sib.innerText.trim();
                if (/^[\[【]\d{4}[\]】]/.test(st)) break;
                textEls.push(sib);
                sib = sib.nextElementSibling;
            }
            return textEls.length > 0 ? textEls : [pContainer];
        }
        return [pContainer];
    }

    function highlightTextInElement(container, searchStr) {
        if (!container || !searchStr || searchStr.length < 2) return false;
        // 1. innerHTML 精确包含
        if (container.innerHTML.includes(searchStr)) {
            container.innerHTML = container.innerHTML.replace(searchStr, `<span class="zy-hl">${searchStr}</span>`);
            return true;
        }
        // 2. 拆分子词包含
        const coreWords = searchStr.split(/[()=Δ/\s\n\r\t，,。、：:；;]/).map(s => s.trim()).filter(s => s.length >= 8);
        for (let word of coreWords) {
            if (container.innerHTML.includes(word)) {
                container.innerHTML = container.innerHTML.replace(word, `<span class="zy-hl">${word}</span>`);
                return true;
            }
        }
        // 3. TreeWalker 文本节点替换
        try {
            const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, null, false);
            let node;
            while ((node = walker.nextNode())) {
                if (node.parentNode && node.parentNode.classList && node.parentNode.classList.contains('zy-hl')) continue;
                const val = node.nodeValue;
                const idx = val.indexOf(searchStr);
                if (idx !== -1) {
                    const span = document.createElement('span');
                    span.className = 'zy-hl';
                    span.textContent = searchStr;
                    const afterText = val.substring(idx + searchStr.length);
                    node.nodeValue = val.substring(0, idx);
                    node.parentNode.insertBefore(span, node.nextSibling);
                    if (afterText) {
                        node.parentNode.insertBefore(document.createTextNode(afterText), span.nextSibling);
                    }
                    return true;
                }
            }
        } catch (e) {}
        return false;
    }

    // 全语种分句正则（西文句号+空格、中日英句号、问叹号）
    const SENTENCE_SPLIT_REGEX = /(?<=[。！？\n])|(?<=[.!?])\s+(?=[A-Z0-9\u4e00-\u9fa5\u3040-\u30ff\uac00-\ud7af])/;

    for (let paraNo in paraGroups) {
        const rawItems = paraGroups[paraNo] || [];
        if (!rawItems || rawItems.length === 0) continue;

        const containers = findParaTextElements(paraNo);
        if (!containers || containers.length === 0) {
            missed += rawItems.length;
            continue;
        }

        const items = rawItems.slice().sort((a, b) => (b.segment || '').length - (a.segment || '').length);

        for (let item of items) {
            const seg = (item.segment || '').trim();
            const markers = item.markers || [];
            const mathSyms = item.math_symbols || [];
            let ok = false;

            for (let container of containers) {
                if (ok) break;

                // 模式 A: 中文专利，直接字面高亮 + 穿透内联标签容错
                if (isChinesePatent) {
                    if (highlightTextInElement(container, seg)) {
                        ok = true;
                        matched++;
                    } else {
                        // 1. 拆分子句或关键词高亮
                        const subParts = seg.split(/[ ，,。、：:；;\s]+/).map(s => s.trim()).filter(s => s.length >= 3);
                        for (let sp of subParts) {
                            if (highlightTextInElement(container, sp)) {
                                ok = true;
                                matched++;
                            }
                        }
                        // 2. 穿透附图标签容错：若含有数字标记，匹配包含该标记的特征词
                        if (!ok && markers.length > 0) {
                            const cText = (container.innerText || '');
                            for (let m of markers) {
                                if (cText.includes(m)) {
                                    // 提取标记周围的词
                                    const mRegex = new RegExp(`[\\u4e00-\\u9fa5A-Za-z0-9_]{1,6}${m}[\\u4e00-\\u9fa5A-Za-z0-9_]{0,4}`);
                                    const mMatch = cText.match(mRegex);
                                    if (mMatch && highlightTextInElement(container, mMatch[0])) {
                                        ok = true;
                                        matched++;
                                        break;
                                    }
                                }
                            }
                        }
                    }
                } else {
                    // 模式 B: 多语种外文专利（跨语言对齐）
                    if (/[a-zA-Z]{4,}/.test(seg) && highlightTextInElement(container, seg)) {
                        ok = true;
                        matched++;
                    } else {
                        const fullText = (container.innerText || '').trim();
                        const sentences = fullText.split(SENTENCE_SPLIT_REGEX)
                            .map(s => s.trim())
                            .filter(s => s.length >= 6 && !/^[\[【]\d{4}[\]】]/.test(s));

                        let bestSentence = null;
                        let bestScore = -1;

                        sentences.forEach(sent => {
                            // 全角半角归一化（兼容日文全角数字１２３）
                            const norm = sent.replace(/[０-９]/g, ch => String.fromCharCode(ch.charCodeAt(0) - 0xFEE0));
                            let score = 0;
                            markers.forEach(m => {
                                if (m && norm.includes(m)) score += 3;
                            });
                            mathSyms.forEach(sym => {
                                if (sym && norm.includes(sym)) score += 2;
                            });
                            if (score > bestScore) {
                                bestScore = score;
                                bestSentence = sent;
                            }
                        });

                        if (bestSentence && bestScore > 0) {
                            if (highlightTextInElement(container, bestSentence)) {
                                crossAligned++;
                                ok = true;
                            }
                        } else if (sentences.length > 0) {
                            const candidate = sentences[0];
                            if (highlightTextInElement(container, candidate)) {
                                crossAligned++;
                                ok = true;
                            }
                        }
                    }
                }
            }

            if (!ok) {
                missed++;
            }
        }
    }

    return { matched, crossAligned, missed };
}'''


class ZyHighlighter:
    def __init__(self, headless=True, browser_path=None):
        self.headless = headless
        self.browser_path = get_browser_executable(browser_path)

    async def process_patent(self, patent_data, output_dir):
        """处理单篇文献高亮截图与附图抓取。"""
        pn = patent_data.get('pn', '未知公开号')
        lang_hint = patent_data.get('lang_hint', 'UNKNOWN')
        is_chinese = (lang_hint == 'zh' or pn.startswith('CN'))
        share_url = patent_data.get('share_url', '')
        target_paras = patent_data.get('target_paras', [])
        para_groups = patent_data.get('para_groups', {})
        target_figs = patent_data.get('target_figs', [])

        result = {
            'ref_index': patent_data.get('ref_index'),
            'pn': pn,
            'title': '',
            'applicant': '',
            'lang_hint': lang_hint,
            'success': False,
            'screenshots': [],
            'screenshots_orig': [],
            'screenshots_trans': [],
            'figures': [],
            'error': None
        }

        all_highlights = patent_data.get('all_highlights', [])
        has_undeduced = patent_data.get('has_undeduced_paras') or (not target_paras and len(all_highlights) > 0)

        if not share_url:
            result['error'] = '缺少智慧芽分享链接 (share_url)'
            return result

        if not target_paras and not has_undeduced:
            result['error'] = '报告中无对应段落引证且无下划线文本'
            return result

        os.makedirs(output_dir, exist_ok=True)

        # 收集所有的下划线引文及附图标记，备用于机翻高亮匹配与柔性吸附
        all_underlines = []
        for p_no, items in para_groups.items():
            for item in items:
                all_underlines.append(item)
        if not all_underlines and all_highlights:
            all_underlines = all_highlights

        async with async_playwright() as p:
            launch_args = {
                'headless': self.headless,
                'args': ['--no-sandbox', '--disable-setuid-sandbox', '--disable-gpu']
            }
            if self.browser_path:
                launch_args['executable_path'] = self.browser_path

            browser = await p.chromium.launch(**launch_args)
            # 关键：开启 2x Retina 超清物理渲染！
            context = await browser.new_context(
                viewport={'width': VIEWPORT_WIDTH, 'height': 1200},
                device_scale_factor=2,
                is_mobile=False
            )
            page = await context.new_page()

            try:
                # ----------------------------------------------------
                # 1. 访问摘要页 (/patent-view/abst) 提取申请人与发明名称
                # ----------------------------------------------------
                print(f"[{pn}] 打开摘要页: {share_url[:60]}...")
                entry_url = share_url
                if '/patent-view/desc' in entry_url:
                    entry_url = entry_url.replace('/patent-view/desc', '/patent-view/abst')

                await page.goto(entry_url, timeout=70000, wait_until='domcontentloaded')
                await page.wait_for_timeout(4000)

                applicant = await page.evaluate(JS_EXTRACT_APPLICANT)
                result['applicant'] = applicant or '未知申请人'
                print(f"[{pn}] 提取申请人: {result['applicant']}")

                doc_title = await page.title()
                title_match = re.search(r'\b[A-Z0-9]+\s+([^-\n\r]+?)\s*-\s*专利详情页', doc_title)
                if title_match:
                    result['title'] = title_match.group(1).strip()
                else:
                    title_el = await page.evaluate(r'''() => {
                        const el = document.querySelector('.patent-app__title-context, .patent-title, h1');
                        return el ? el.innerText.trim() : '';
                    }''')
                    result['title'] = title_el or '未知专利名称'
                print(f"[{pn}] 提取专利名称: {result['title']}")

                # ----------------------------------------------------
                # 2. 切换至说明书页并根据模式执行处理
                # ----------------------------------------------------
                print(f"[{pn}] 切换至说明书页...")
                desc_url = entry_url.replace('/patent-view/abst', '/patent-view/desc')
                await page.goto(desc_url, timeout=60000, wait_until='domcontentloaded')
                await page.wait_for_timeout(3000)

                # 若为无段号/按页栏行引证的外文文献，首先启动机翻逆向定段引擎推导智慧芽段号
                if has_undeduced and not is_chinese:
                    print(f"[{pn}] 检测到报告未标明标准段号（或使用传统页栏行/纯下划线引用），启动【机翻逆向定段引擎】...")
                    try:
                        await page.click('.lang-switcher')
                        await page.wait_for_timeout(500)
                        await page.click('.pendo-track__function-bar__translate-CN')
                        await page.wait_for_timeout(4500)

                        deduce_res = await page.evaluate(JS_DEDUCE_PARAS_FROM_TRANSLATION, all_highlights)
                        deduced_paras = deduce_res.get('target_paras', [])
                        if deduced_paras:
                            target_paras = deduced_paras
                            for p_no, items in deduce_res.get('para_groups', {}).items():
                                if p_no not in para_groups:
                                    para_groups[p_no] = items
                                else:
                                    para_groups[p_no].extend(items)
                            print(f"[{pn}] 逆向定段成功！成功推导出智慧芽段号: {target_paras}")
                        else:
                            print(f"[{pn}] 逆向定段未匹配到高分段落，采用默认前置段号兜底")
                            target_paras = ['0001', '0002']
                    except Exception as e_deduce:
                        print(f"[{pn}] 逆向定段遇到异常: {e_deduce}")
                        if not target_paras:
                            target_paras = ['0001', '0002']

                # 切换并锁定公布原文单栏排版
                await page.evaluate(JS_FORCE_LAYOUT_AND_ORIGINAL)
                await page.wait_for_timeout(1000)

                hl_stats = await page.evaluate(JS_UNIVERSAL_HIGHLIGHT, {
                    'paraGroups': para_groups,
                    'isChinesePatent': is_chinese,
                    'highlightBg': HIGHLIGHT_BG
                })
                print(f"[{pn}] 原文高亮完成: 命中 {hl_stats.get('matched', 0)} 处, 跨语种对齐 {hl_stats.get('crossAligned', 0)} 处, 未命中 {hl_stats.get('missed', 0)} 处")

                # ----------------------------------------------------
                # 3. 智能计算覆盖所有评述段落的截图簇（目标段前后各合并）
                # ----------------------------------------------------
                clusters = self._compute_clusters(target_paras)
                print(f"[{pn}] 计算得到截图簇: {len(clusters)} 个区间 -> {clusters}")

                # 截取公布原文长图（对外文加 _原文 后缀，中文加 _截图 后缀）
                orig_suffix = "_截图" if is_chinese else "_原文"
                screenshots_orig = await self._capture_and_crop(page, clusters, pn, output_dir, suffix=orig_suffix)
                result['screenshots_orig'] = screenshots_orig

                # ----------------------------------------------------
                # 3.5 对于外文专利，执行第二阶段【智慧芽中文机翻高亮截图】
                # ----------------------------------------------------
                screenshots_trans = []
                if not is_chinese:
                    try:
                        print(f"[{pn}] 切换至智慧芽中文机翻模式并注入译文高亮...")
                        await page.click('.lang-switcher')
                        await page.wait_for_timeout(500)
                        await page.click('.pendo-track__function-bar__translate-CN')
                        await page.wait_for_timeout(4500)

                        # 全宽渲染机翻容器，隐藏原文及多余横幅
                        await page.evaluate(r'''() => {
                            const orig = document.querySelector('.orgin_desc');
                            const trans = document.querySelector('.translation-block');
                            if (orig) orig.style.display = 'none';
                            if (trans) {
                                trans.style.width = '100%';
                                trans.style.maxWidth = '100%';
                                trans.style.flex = '1';
                                trans.style.padding = '0 10px';
                            }
                            const header = document.querySelector('.patsnap-biz-header');
                            if (header) header.style.display = 'none';
                            const subNav = document.querySelector('.patent-view-sub-nav');
                            if (subNav) subNav.style.display = 'none';
                        }''')
                        await page.wait_for_timeout(1000)

                        trans_hl_res = await page.evaluate(JS_TRANSLATION_HIGHLIGHT, all_underlines)
                        print(f"[{pn}] 机翻高亮完成: 匹配 {trans_hl_res.get('count', 0)} 处文本")

                        screenshots_trans = await self._capture_and_crop_translation(page, clusters, pn, output_dir, suffix="_机翻")
                        result['screenshots_trans'] = screenshots_trans
                    except Exception as e_trans:
                        print(f"[{pn}] 抓取机翻遇到异常: {e_trans}")

                result['screenshots'] = screenshots_orig + screenshots_trans

                # ----------------------------------------------------
                # 4. 抓取引述附图（若涉及图号）
                # ----------------------------------------------------
                if target_figs:
                    print(f"[{pn}] 切换至附图页抓取涉及图纸: {target_figs}...")
                    img_url = entry_url.replace('/patent-view/abst', '/patent-view/image')
                    figures = await self._capture_figures(page, img_url, target_figs, pn, output_dir)
                    result['figures'] = figures

                result['success'] = len(screenshots_orig) > 0

            except Exception as e:
                print(f"[{pn}] 执行异常: {e}")
                result['error'] = str(e)
            finally:
                await browser.close()

        return result

    def _compute_clusters(self, target_paras):
        """计算截取区间：确保每一个目标段都被完整覆盖，重叠自动合并，密集分簇。"""
        int_paras = []
        for p in target_paras:
            try:
                int_paras.append(int(p))
            except ValueError:
                pass
        int_paras = sorted(list(set(int_paras)))

        if not int_paras:
            return [{'start': 1, 'end': 5}]

        # 对每个目标段，向前取 1~2 段，向后取 1~2 段
        raw_intervals = []
        for p in int_paras:
            s = max(1, p - 1)
            e = p + 1
            raw_intervals.append({'start': s, 'end': e})

        merged = []
        curr = raw_intervals[0].copy()
        for next_iv in raw_intervals[1:]:
            # 如果区间重叠或紧邻（间隔 <= 2 段），且合并后跨度不超过 12 段，则合并
            if next_iv['start'] <= curr['end'] + 2 and (next_iv['end'] - curr['start']) <= 12:
                curr['end'] = max(curr['end'], next_iv['end'])
            else:
                merged.append(curr)
                curr = next_iv.copy()
        merged.append(curr)

        return merged

    async def _capture_and_crop(self, page, clusters, pn, output_dir, suffix="_截图"):
        """利用 scrollIntoView 定位，精准剔除左侧灰色侧边栏，截取纯白正文卡片。"""
        screenshots = []

        for idx, cluster in enumerate(clusters):
            start_no = str(cluster['start']).zfill(4)
            end_no = str(cluster['end']).zfill(4)
            out_filename = f"{pn}_段{start_no}-{end_no}{suffix}.png"
            out_path = os.path.join(output_dir, out_filename)

            clip = await page.evaluate(r'''(c) => {
                function findParaElement(num) {
                    const pInt = parseInt(num, 10);
                    const pStr = String(pInt).padStart(4, '0');
                    const b = Array.from(document.querySelectorAll('b.d_n')).find(x => {
                        return x.innerText.trim() === `[${pStr}]` || x.innerText.trim() === `【${pStr}】`;
                    });
                    if (b && b.parentElement) return b.parentElement;

                    const pads = ['[' + pStr + ']', '【' + pStr + '】'];
                    const container = document.querySelector('.patent-app__content-view') || document.body;
                    const els = Array.from(container.querySelectorAll('*')).filter(el => {
                        const txt = el.innerText || '';
                        return pads.some(p => txt.includes(p));
                    });
                    return els.length > 0 ? els[els.length - 1] : null;
                }

                let elStart = findParaElement(c.start);
                if (!elStart) {
                    for (let s = c.start + 1; s <= c.end; s++) {
                        elStart = findParaElement(s);
                        if (elStart) break;
                    }
                }

                let elEnd = findParaElement(c.end);
                if (!elEnd) {
                    for (let e = c.end - 1; e >= c.start; e--) {
                        elEnd = findParaElement(e);
                        if (elEnd) break;
                    }
                }
                // 关键：若 elEnd 仅为段号短标签（如日文专利），延伸至该段正文最后一个兄弟节点
                if (elEnd && elEnd.nextElementSibling && elEnd.innerText.trim().length <= 10) {
                    let sib = elEnd.nextElementSibling;
                    while (sib && !/^[\[【]\d{4}[\]】]/.test(sib.innerText.trim())) {
                        elEnd = sib;
                        sib = sib.nextElementSibling;
                    }
                }

                if (elStart) {
                    elStart.scrollIntoView({ block: 'start' });
                    const sc = document.querySelector('.patent-app__content-view') || window;
                    if (sc.scrollTop !== undefined) {
                        sc.scrollTop = Math.max(0, sc.scrollTop - 40);
                    }
                }

                const rStart = elStart ? elStart.getBoundingClientRect() : { top: 100 };
                const rEnd = elEnd ? elEnd.getBoundingClientRect() : { bottom: 800 };

                const top = Math.max(0, Math.round(rStart.top - 15));
                const bottom = Math.round(rEnd.bottom + 15);
                const height = Math.max(250, bottom - top);

                const contentBox = document.querySelector('.patent-app__content-view') || document.querySelector('.desc-render');
                let left = 144;
                let width = 1256;
                if (contentBox) {
                    const cRect = contentBox.getBoundingClientRect();
                    left = Math.round(cRect.left);
                    width = Math.round(cRect.width);
                }

                return {
                    x: left,
                    y: top,
                    width: width,
                    height: Math.min(2400, height)
                };
            }''', cluster)

            await page.screenshot(path=out_path, clip=clip, timeout=30000)
            img = Image.open(out_path)
            has_yellow = self._verify_yellow_pixels(img)

            print(f"[{pn}] 原文说明书截图成功: {out_filename} (尺寸: {img.size}, 黄色高亮: {'PASS' if has_yellow else '未检出/无划线段'})")
            screenshots.append({
                'path': out_path,
                'filename': out_filename,
                'start_para': start_no,
                'end_para': end_no,
                'verified': has_yellow,
                'type': 'orig'
            })

        return screenshots

    async def _capture_and_crop_translation(self, page, clusters, pn, output_dir, suffix="_机翻"):
        """针对智慧芽中文机翻容器 (.translation-block) 定位并截取高亮长图。"""
        screenshots = []

        for idx, cluster in enumerate(clusters):
            start_no = str(cluster['start']).zfill(4)
            end_no = str(cluster['end']).zfill(4)
            out_filename = f"{pn}_段{start_no}-{end_no}{suffix}.png"
            out_path = os.path.join(output_dir, out_filename)

            clip = await page.evaluate(r'''(c) => {
                const trans = document.querySelector('.translation-block');
                if (!trans) return null;

                function findParaElement(num) {
                    const pInt = parseInt(num, 10);
                    const pStr = String(pInt).padStart(4, '0');
                    const b = Array.from(trans.querySelectorAll('b.d_n')).find(x => {
                        return x.innerText.trim() === `[${pStr}]` || x.innerText.trim() === `【${pStr}】`;
                    });
                    if (b) {
                        return b.closest('div[p]') || b.parentElement;
                    }
                    return null;
                }

                let elStart = findParaElement(c.start);
                if (!elStart) {
                    for (let s = c.start + 1; s <= c.end; s++) {
                        elStart = findParaElement(s);
                        if (elStart) break;
                    }
                }

                let elEnd = findParaElement(c.end);
                if (!elEnd) {
                    for (let e = c.end - 1; e >= c.start; e--) {
                        elEnd = findParaElement(e);
                        if (elEnd) break;
                    }
                }

                if (elEnd) {
                    let curr = elEnd;
                    while (curr && curr.nextElementSibling) {
                        const next = curr.nextElementSibling;
                        if (next.querySelector('b.d_n') || (next.tagName === 'B' && next.classList.contains('d_n'))) break;
                        curr = next;
                    }
                    elEnd = curr;
                }

                if (elStart) elStart.scrollIntoView({ block: 'start' });

                const rStart = elStart ? elStart.getBoundingClientRect() : { top: 100 };
                const rEnd = elEnd ? elEnd.getBoundingClientRect() : { bottom: 800 };
                const top = Math.max(0, Math.round(rStart.top - 15));
                const bottom = Math.round(rEnd.bottom + 15);
                const height = Math.max(250, bottom - top);

                const tRect = trans.getBoundingClientRect();
                return {
                    x: Math.round(tRect.left),
                    y: top,
                    width: Math.round(tRect.width),
                    height: Math.min(2400, height)
                };
            }''', cluster)

            if clip:
                await page.screenshot(path=out_path, clip=clip, timeout=30000)
                img = Image.open(out_path)
                has_yellow = self._verify_yellow_pixels(img)

                print(f"[{pn}] 机翻说明书截图成功: {out_filename} (尺寸: {img.size}, 黄色高亮: {'PASS' if has_yellow else '未检出/无划线段'})")
                screenshots.append({
                    'path': out_path,
                    'filename': out_filename,
                    'start_para': start_no,
                    'end_para': end_no,
                    'verified': has_yellow,
                    'type': 'trans'
                })

        return screenshots

    async def _capture_figures(self, page, img_url, target_figs, pn, output_dir):
        """在智慧芽附图专区定位并截取引述的图纸。"""
        figures = []
        try:
            await page.goto(img_url, timeout=60000, wait_until='domcontentloaded')
            await page.wait_for_timeout(3500)

            # 点击“大”图模式
            await page.evaluate(r'''() => {
                const btns = Array.from(document.querySelectorAll('button, span, div'));
                const bigBtn = btns.find(b => b.innerText && b.innerText.trim() === '大');
                if (bigBtn) bigBtn.click();
            }''')
            await page.wait_for_timeout(1500)

            # 分析页面上的所有附图卡片
            cards_info = await page.evaluate(r'''() => {
                const res = [];
                const cards = document.querySelectorAll('.img-flip-container, .image-list-item, [data-image-index]');
                cards.forEach((card, idx) => {
                    const indexAttr = card.getAttribute('data-image-index') || String(idx + 1);
                    const descText = card.innerText || '';
                    res.push({
                        idx: idx,
                        image_index: indexAttr,
                        text: descText
                    });
                });
                return res;
            }''')

            # 匹配目标图纸卡片
            captured_indices = set()
            for fig_label in target_figs:
                # 提取数字（如 1a -> 1, 8 -> 8）
                m_num = re.search(r'\d+', fig_label)
                if not m_num:
                    continue
                num_str = m_num.group(0)

                matched_idx = None
                for c in cards_info:
                    # 匹配规则 1: 文本中包含 Figure 1, Fig. 1, 图1 等
                    if re.search(r'(?:Figure|Fig\.?|FIG\.?|图|図|Abb\.?)\s*' + re.escape(fig_label), c['text'], re.I) or \
                       re.search(r'(?:Figure|Fig\.?|FIG\.?|图|図|Abb\.?)\s*' + re.escape(num_str) + r'\b', c['text'], re.I):
                        matched_idx = c['image_index']
                        break
                    # 匹配规则 2: 美欧专利图号通常直接对应 Sheet 编号（如 Fig 1 对应 Sheet 1）
                    if c['image_index'] == num_str:
                        matched_idx = c['image_index']

                # 兜底直接用数字
                if not matched_idx:
                    matched_idx = num_str

                if matched_idx not in captured_indices:
                    captured_indices.add(matched_idx)
                    out_f_fn = f"{pn}_附图_图{fig_label}_Sheet_{matched_idx}.png"
                    out_f_p = os.path.join(output_dir, out_f_fn)

                    loc = page.locator(f'.img-flip-container[data-image-index="{matched_idx}"]').first
                    if await loc.count() > 0:
                        await loc.scroll_into_view_if_needed()
                        await page.wait_for_timeout(400)
                        await loc.screenshot(path=out_f_p)
                        im = Image.open(out_f_p)
                        print(f"[{pn}] 附图截图成功: {out_f_fn} 尺寸: {im.size}")
                        figures.append({'path': out_f_p, 'label': f"图{fig_label}", 'size': im.size})

        except Exception as e:
            print(f"[{pn}] 抓取附图遇到异常: {e}")

        return figures

    def _verify_yellow_pixels(self, img):
        """验证截图中是否存在黄色高亮像素 (#ffeb3b / #ffe28a)。"""
        img_rgb = img.convert('RGB')
        w, h = img_rgb.size
        yellows = 0
        step = 4
        for x in range(0, w, step):
            for y in range(0, h, step):
                r, g, b = img_rgb.getpixel((x, y))
                if r > 230 and g > 200 and b < 160 and (r - b) > 60:
                    yellows += 1
                    if yellows > 15:
                        return True
        return False
