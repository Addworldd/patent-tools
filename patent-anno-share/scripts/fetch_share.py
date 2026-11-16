# -*- coding: utf-8 -*-
"""
智慧芽免登录分享链接 (PatSnap Share URL) 内容抓取引擎 (fetch_share.py)

功能：
1. 接收智慧芽专利分享链接 (含 patentId, shareId, signature 等参数)；
2. 自动探测系统可用浏览器 (Chrome / Edge / Chromium) 并无头运行 Playwright；
3. 走 /patent-view/abst 摘要页入口建立鉴权会话并提取著录项 (公开号、标题、申请人、公开日)；
4. 切换 /patent-view/clms 提取权利要求原文，外文文献自动切换机翻并抓取中文对照；
5. 切换 /patent-view/desc 提取说明书段落及段号 [0001] 原文，外文文献自动提取中文机翻对照；
6. 严格执行段落连续性核查 (continuity)；
7. 输出支持直接生成 HTML 单文件的 data.json，或标准中英对照 Markdown 文件。

用法：
  python fetch_share.py "<分享链接>" --out-data data.json
  python fetch_share.py "<分享链接>" --out-md document.md
  python fetch_share.py "<分享链接>" --out-dir ./output/
"""

import os
import re
import sys
import io
import json
import time
import base64
import argparse
import asyncio

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
from urllib.parse import urlparse, parse_qs, urlunparse
from playwright.async_api import async_playwright

VIEWPORT_WIDTH = 1400
VIEWPORT_HEIGHT = 1200


def get_browser_executable(preferred_path=None):
    """
    跨平台自适应探测可用浏览器：
    1. 优先使用用户指定路径或环境变量 CHROME_PATH / BROWSER_PATH；
    2. 自动检索 Google Chrome；
    3. 自动检索 Microsoft Edge (Windows 原生自带)；
    4. 若未检索到可执行文件，返回 None (交由 Playwright 自动调用其内置 chromium)。
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


def normalize_share_url(url, target_section='abst'):
    """
    将智慧芽分享链接规范化为指定 section (abst/desc/clms/image) 的 URL，保留所有鉴权 query 参数。
    自动剔除各类脏字符（如多余路径片段 'figures/、单双引号、首尾空格等）。
    """
    if not url:
        return ""
    u = url.strip().strip("'\"<> ")
    # 替换路径中的 /patent-view/... 为 /patent-view/{target_section}
    new_url = re.sub(r'/patent-view/[^?#]+', f'/patent-view/{target_section}', u)
    # 容错：若路径无 /patent-view/ 直接带参数，补齐路径
    if '/patent-view/' not in new_url and 'zhihuiya.com' in new_url:
        new_url = re.sub(r'(zhihuiya\.com)/?', f'\\1/patent-view/{target_section}', new_url)
    return new_url


def is_chinese_text(s, thresh=0.15):
    """
    判断文本是否以中文为主。
    特别注意：日韩专利中常含有汉字，若包含日文平假名/片假名或韩文字母，则必定属于外文(日/韩)，绝非中文专利。
    """
    if not s:
        return False
    # 若含有日文平假名或片假名，必定为日文
    if re.search(r'[\u3040-\u309f\u30a0-\u30ff]', s):
        return False
    # 若含有韩文字母 (Hangul)，必定为韩文
    if re.search(r'[\uac00-\ud7af\u1100-\u11ff]', s):
        return False
    cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    return (cjk / max(1, len(s))) > thresh


def extract_reference_numerals(claims, description):
    """
    从权利要求书与说明书中提取附图标记与构件术语字典。
    零大模型消耗，全面兼容中文、英文、德文、日文（符号の説明）与韩文（부호의 설명）。
    支持全角数字转半角数字。
    """
    from collections import Counter, defaultdict

    def to_half_width(text):
        if not text:
            return ""
        return re.sub(r'[\uff10-\uff19]', lambda m: chr(ord(m.group(0)) - 0xfee0), text)

    STOP_WORDS = set("的 在 于 由 和 与 及 设 有 为 至 从 向 该 此 每 各 一 两 三 四 五 六 七 八 九 十 本 所述 图 附图 实施例 步骤 阶段 表 参照 参见 如图 如图所示 结构 装置 系统 方法 权利要求 請求項 청구항".split())
    BAD_NUMERAL_SUFFIX = {"年", "月", "日", "号", "个", "项", "条", "页", "次", "点", "%", "℃", "mm", "cm", "m", "kg", "g", "v", "V", "a", "A", "w", "W", "hz", "Hz"}

    candidates = defaultdict(lambda: {"zh_names": Counter(), "en_names": Counter(), "first_para": "", "freq": 0})

    all_sections = []
    for c in (claims or []):
        all_sections.append((c.get("no", ""), to_half_width(c.get("zh", "")), to_half_width(c.get("en", ""))))
    for d in (description or []):
        all_sections.append((d.get("no", ""), to_half_width(d.get("zh", "")), to_half_width(d.get("en", ""))))

    # 1. 优先扫描附图标记说明段落 (赋予最高置信权重，涵盖中/英/德/日/韩)
    list_re = re.compile(r'(?:^|[；;\n\r])\s*([0-9]{1,4}[a-zA-Z]?)\s*[:：、\-—\s…\.]+\s*([^\n\r;；]+?)(?=(?:[；;\n\r]|\s{2,}\d{1,4}\s*[:：、\-—]|$))')
    for no, zh, en in all_sections:
        is_glossary_sec = any(k in zh or k in en for k in [
            "附图标记", "标记说明", "符号说明", "元件标号", "主要标号", "图号说明",
            "符号の説明", "符号の簡単な説明", "부호의 설명", "도면의 주요 부분에 대한 부호의 설명",
            "Reference Signs", "Reference Numerals", "Bezugszeichenliste", "Bezugszeichen"
        ])
        if is_glossary_sec:
            target_text = zh if zh else en
            for m in list_re.finditer(target_text):
                num, name = m.group(1), m.group(2).strip(".,。，;；… \t")
                if 2 <= len(name) <= 40:
                    c_data = candidates[num]
                    c_data["zh_names"][name] += 12
                    if not c_data["first_para"]:
                        c_data["first_para"] = no

    # 2. 中文、日文、韩文与英文正文正则挖掘
    zh_pat = re.compile(r'([\u4e00-\u9fa5\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]{2,15})[\s（\(]*([0-9]{1,4}[a-zA-Z]?)[）\)]*')
    en_pat = re.compile(r'\b([a-zA-Z\-]{2,25}(?:\s+[a-zA-Z\-]{2,25}){0,2})[\s\(]+([0-9]{1,4}[a-zA-Z]?)\b')

    for no, zh, en in all_sections:
        # 中文/日文/韩文挖掘
        for txt in [zh, en]:
            if not txt: continue
            for m in zh_pat.finditer(txt):
                raw_name, num = m.group(1), m.group(2)
                if not num or len(num) > 4:
                    continue
                end_pos = m.end()
                if end_pos < len(txt) and txt[end_pos] in BAD_NUMERAL_SUFFIX:
                    continue
                if raw_name in ["图", "附图", "如图", "参见图", "如图所示", "实施例", "表", "步骤", "第", "図", "図面"]:
                    continue
                clean_name = raw_name
                for sep in ["的", "在", "于", "由", "和", "与", "及", "设", "有", "为", "至", "从", "向", "该", "此", "本", "所述", "包括", "具有", "の", "に", "を", "は", "が", "で", "と", "의", "에", "를", "을", "는", "은", "로"]:
                    if sep in clean_name:
                        clean_name = clean_name.split(sep)[-1]
                if len(clean_name) >= 2 and clean_name not in STOP_WORDS:
                    c_data = candidates[num]
                    c_data["zh_names"][clean_name] += 1
                    c_data["freq"] += 1
                    if not c_data["first_para"]:
                        c_data["first_para"] = no

        # 英文/德文等西文字符挖掘
        if en:
            for m in en_pat.finditer(en):
                raw_name, num = m.group(1).strip(), m.group(2)
                if not num or len(num) > 4:
                    continue
                lower_name = raw_name.lower()
                if any(lower_name.startswith(p) for p in ["fig", "figure", "example", "table", "step", "claim", "abb", "abbildung"]):
                    continue
                c_data = candidates[num]
                c_data["en_names"][raw_name] += 1
                c_data["freq"] += 1
                if not c_data["first_para"]:
                    c_data["first_para"] = no

    glossary = []
    for num, info in candidates.items():
        zh_top = info["zh_names"].most_common(1)
        en_top = info["en_names"].most_common(1)
        name_zh = zh_top[0][0] if zh_top else ""
        name_en = en_top[0][0] if en_top else ""
        if not name_zh and not name_en:
            continue
        num_digits = re.sub(r'\D', '', num)
        num_val = int(num_digits) if num_digits else 99999
        glossary.append({
            "numeral": num,
            "name_zh": name_zh,
            "name_en": name_en,
            "first_para": info["first_para"],
            "freq": info["freq"],
            "_sort": (num_val, num)
        })

    glossary.sort(key=lambda x: x["_sort"])
    for item in glossary:
        item.pop("_sort", None)

    return glossary


JS_EXTRACT_BIBLIO = r'''() => {
    function cleanName(n) {
        if (!n) return '';
        let s = n.replace(/^\[标\]/, '').replace(/\([^\)]+\)/g, '').replace(/（[^）]+）/g, '');
        s = s.replace(/^(?:当前|原始)?申请(?:\(专利权\)|（专利权）)?人[\s:：\|\n]*/g, '');
        s = s.replace(/^(?:当前|原始)?权利人[\s:：\|\n]*/g, '');
        s = s.replace(/[\r\n]+/g, '、').replace(/\s{2,}/g, ' ');
        return s.trim();
    }

    let applicant = '';
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
                    applicant = val;
                    break;
                }
            }
            parent = parent.parentElement;
        }
        if (applicant) break;
    }
    if (!applicant && document.body) {
        const m = document.body.innerText.match(/(?:当前申请\(专利权\)人|原始申请\(专利权\)人|申请\(专利权\)人)[\s\S]{0,10}?\|\s*([^\|\n\r]+)/);
        if (m && m[1].trim()) applicant = cleanName(m[1]);
    }

    // 专利号与标题
    let pn = '';
    let title = '';
    const docTitle = document.title || '';
    const cleanDocTitle = docTitle.replace(/\s*-\s*(?:专利详情页|专利详情|智慧芽).*$/i, '').trim();
    const tm = cleanDocTitle.match(/^([A-Z]{2}\d+[A-Z\d]*)\s+(.+)$/s);
    if (tm) {
        pn = tm[1].trim();
        title = tm[2].trim();
    } else {
        const mPn = cleanDocTitle.match(/\b([A-Z]{2}\d{5,14}[A-Z\d]*)\b/);
        if (mPn) pn = mPn[1];
    }

    if (!pn && document.body) {
        const bodyTxt = document.body.innerText || '';
        const mBody = bodyTxt.match(/\b([A-Z]{2}\d{5,14}[A-Z\d]*)\b/);
        if (mBody) pn = mBody[1];
    }

    if (!pn) {
        const pnEl = document.querySelector('.patent-app__pn, .patent-number, h1');
        if (pnEl) {
            const m = (pnEl.innerText || '').match(/\b([A-Z]{2}\d{5,14}[A-Z\d]*)\b/);
            if (m) pn = m[1];
        }
    }
    if (!title) {
        const tEl = document.querySelector('.patent-app__title-context, .patent-title');
        if (tEl) title = tEl.innerText.trim();
    }

    // 公开日、申请日
    let pbd = '', apd = '';
    const bodyText = document.body ? document.body.innerText : '';
    const dateRe = /(\d{4}-\d{2}-\d{2})\s*\n\s*(首次公开日|公开（公告）日|公开\(公告\)日|公开公告日|申请日|优先权日)/g;
    let dm;
    while ((dm = dateRe.exec(bodyText)) !== null) {
        const d = dm[1], lab = dm[2];
        if (lab.includes('公开') && !pbd) pbd = d;
        if (lab.includes('申请') && !apd) apd = d;
    }

    return {
        pn: pn,
        title: title,
        applicant: applicant,
        pub_date: pbd,
        app_date: apd,
        page_title: docTitle
    };
}'''


JS_EXTRACT_CLAIMS_ORIGINAL = r'''() => {
    function toHankaku(str) {
        if (!str) return '';
        return str.replace(/[０-９]/g, ch => String.fromCharCode(ch.charCodeAt(0) - 0xFEE0));
    }

    // 强制切回原文（若处于机翻状态）
    const spans = Array.from(document.querySelectorAll('span, button, a'));
    const origBtn = spans.find(s => s.innerText && s.innerText.trim() === '原文');
    if (origBtn && !origBtn.classList.contains('is-active')) {
        origBtn.click();
    }

    const container = document.querySelector('.patent-app-view__clms, .claim-container, .patent-app__content-view') || document.body;
    
    // 方案 1: 查找结构化权项节点
    let items = [];
    const itemNodes = Array.from(container.querySelectorAll('.claim-item, [data-claim-id], div[c], div[data-c]'));
    if (itemNodes.length > 0) {
        itemNodes.forEach((node, idx) => {
            const rawTxt = (node.innerText || '').trim();
            if (!rawTxt) return;
            const txt = toHankaku(rawTxt);
            const mNo = txt.match(/^(?:【|\[|［|〔|\(|（)?\s*(?:(?:Claim|Claims|Anspruch|Ansprüche|Revendication|Revendications|請求項|청구항|제)\s*)?(\d{1,3})\s*(?:항|項)?\s*(?:】|\]|］|〕|\)|）|[.．、:：\-—\s])/i);
            const no = mNo ? parseInt(mNo[1], 10) : (idx + 1);
            items.push({ no: no, text: rawTxt });
        });
    }

    // 方案 2: 若未找到专门结构，使用兼容多国格式的正则切分正文（支持 日/韩/德/英/中 及方括号/圆括号/全角数字）
    if (items.length === 0) {
        const rawFullText = (container.innerText || '').trim();
        const fullText = toHankaku(rawFullText);
        const re = /(?:^|\n)\s*(?:【|\[|［|〔|\(|（)?\s*(?:(?:Claim|Claims|Anspruch|Ansprüche|Revendication|Revendications|請求項|청구항|제)\s*)?(\d{1,3})\s*(?:항|項)?\s*(?:】|\]|］|〕|\)|）|[.．、:：\-—\s])\s*([\s\S]*?)(?=(?:\n\s*(?:【|\[|［|〔|\(|（)?\s*(?:(?:Claim|Claims|Anspruch|Ansprüche|Revendication|Revendications|請求項|청구항|제)\s*)?\d{1,3}\s*(?:항|項)?\s*(?:】|\]|］|〕|\)|）|[.．、:：\-—\s]))|$)/gi;
        let m;
        while ((m = re.exec(fullText)) !== null) {
            const cNo = parseInt(m[1], 10);
            const cBody = m[2].trim();
            if (cBody.length >= 8) {
                const hasPrefix = /^(?:【|\[|［|〔|\(|\b(?:Claim|Anspruch|請求項|청구항)\b|\d{1,3}[.．、])/.test(cBody);
                const fullClmText = hasPrefix ? cBody : `${cNo}. ${cBody}`;
                items.push({ no: cNo, text: fullClmText });
            }
        }
    }

    return items;
}'''


JS_EXTRACT_CLAIMS_TRANSLATION = r'''() => {
    function toHankaku(str) {
        if (!str) return '';
        return str.replace(/[０-９]/g, ch => String.fromCharCode(ch.charCodeAt(0) - 0xFEE0));
    }

    const trans = document.querySelector('.translation-block') || document.querySelector('.patent-app-view__clms') || document.body;
    let items = [];

    const itemNodes = Array.from(trans.querySelectorAll('.claim-item, [data-claim-id], div[c], div[data-c]'));
    if (itemNodes.length > 0) {
        itemNodes.forEach((node, idx) => {
            const rawTxt = (node.innerText || '').trim();
            if (!rawTxt) return;
            const txt = toHankaku(rawTxt);
            const mNo = txt.match(/^(?:【|\[|［|〔|\(|（)?\s*(?:(?:Claim|Claims|Anspruch|Ansprüche|Revendication|Revendications|請求項|청구항|제)\s*)?(\d{1,3})\s*(?:항|項)?\s*(?:】|\]|］|〕|\)|）|[.．、:：\-—\s])/i);
            const no = mNo ? parseInt(mNo[1], 10) : (idx + 1);
            items.push({ no: no, text: rawTxt });
        });
    }

    if (items.length === 0) {
        const rawFullText = (trans.innerText || '').trim();
        const fullText = toHankaku(rawFullText);
        const re = /(?:^|\n)\s*(?:【|\[|［|〔|\(|（)?\s*(?:(?:Claim|Claims|Anspruch|Ansprüche|Revendication|Revendications|請求項|청구항|제)\s*)?(\d{1,3})\s*(?:항|項)?\s*(?:】|\]|］|〕|\)|）|[.．、:：\-—\s])\s*([\s\S]*?)(?=(?:\n\s*(?:【|\[|［|〔|\(|（)?\s*(?:(?:Claim|Claims|Anspruch|Ansprüche|Revendication|Revendications|請求項|청구항|제)\s*)?\d{1,3}\s*(?:항|項)?\s*(?:】|\]|］|〕|\)|）|[.．、:：\-—\s]))|$)/gi;
        let m;
        while ((m = re.exec(fullText)) !== null) {
            const cNo = parseInt(m[1], 10);
            const cBody = m[2].trim();
            if (cBody.length >= 8) {
                const hasPrefix = /^(?:【|\[|［|〔|\(|\b(?:Claim|Anspruch|請求項|청구항)\b|\d{1,3}[.．、])/.test(cBody);
                const fullClmText = hasPrefix ? cBody : `${cNo}. ${cBody}`;
                items.push({ no: cNo, text: fullClmText });
            }
        }
    }

    return items;
}'''


JS_EXTRACT_DESC_ORIGINAL = r"""() => {
    function toHankaku(str) {
        if (!str) return '';
        return str.replace(/[０-９]/g, ch => String.fromCharCode(ch.charCodeAt(0) - 0xFEE0));
    }

    const HEADING_RE = /^(?:【\s*(?:技术领域|所属技术领域|背景技术|技术背景|现有技术|发明内容|实用新型内容|外观设计内容|发明公开|技术方案|有益效果|附图说明|附图简要说明|具体实施方式|具体实施例|实施方式|实施例|符号の説明|符号の簡単な説明|부호의\s*설명|도면의\s*간단한\s*설명|발명의\s*내용|배경기술|기술분야|발명을\s*실시하기\s*위한\s*구체적인\s*내용)\s*】|(?:技术领域|背景技术|技术背景|发明内容|实用新型内容|外观设计内容|附图说明|附图简要说明|具体实施方式|实施方式|实施例)\s*[:：]?|(?:TECHNICAL\s+FIELD|BACKGROUND(?:\s+OF\s+THE\s+INVENTION|\s+ART)?|SUMMARY(?:\s+OF\s+THE\s+INVENTION)?|BRIEF\s+DESCRIPTION\s+OF(?:\s+THE)?\s+DRAWINGS|DETAILED\s+DESCRIPTION(?:\s+OF(?:\s+THE)?\s+(?:PREFERRED\s+)?EMBODIMENTS)?|FIELD\s+OF\s+THE\s+INVENTION|DESCRIPTION\s+OF\s+EMBODIMENTS|CROSS-REFERENCE\s+TO\s+RELATED\s+APPLICATIONS)\s*[:：\.]?|【\s*(?:技術分野|背景技術|発明の概要|発明の効果|図面の簡単な説明|発明を実施するための形態|実施例|符号の説明)\s*】|【\s*(?:기술분야|배경기술|발명의\s*내용|도면의\s*간단한\s*설명|발명을\s*실시하기\s*위한\s*구체적인\s*내용|부호의\s*설명)\s*】|(?:배\s*경\s*기\s*술|기\s*술\s*분\s*야|발\s*명\s*의\s*내\s*용|도\s*면\s*의\s*간\s*단\s*한\s*설\s*명|실\s*시\s*예))\s*$/i;

    function extractOfficialNum(node, rawText) {
        // 1. 节点内部专属标号标签 (如 <b class="d_n">[0001]</b> 或 span.paragraph-num)
        const b = node.querySelector('b.d_n, span.paragraph-num, span.desc-p-num, span.num');
        if (b) {
            const bt = toHankaku(b.innerText || '');
            const m = bt.match(/\d{1,4}/);
            if (m) return parseInt(m[0], 10);
        }

        const cleanT = toHankaku(rawText).trim();

        // 2. 文本首部带括号的标号: [0001], 【0001】, (0001), ［0001］
        const mBracket = cleanT.match(/^(?:【|\[|［|〔|\(|（)\s*(\d{1,4})\s*(?:】|\]|］|〕|\)|）)/);
        if (mBracket) {
            return parseInt(mBracket[1], 10);
        }

        // 3. 文本首部无括号的裸 4 位数字标号: 0001, 0002...
        const mBare4 = cleanT.match(/^([0-9]{4})(?:\s+|[\.、:：\t]|$)/);
        if (mBare4) {
            const afterText = cleanT.slice(mBare4[0].length).trim();
            if (!/^[年月日%℃度元]/.test(afterText)) {
                return parseInt(mBare4[1], 10);
            }
        }

        return null;
    }

    function stripParaPrefix(rawText) {
        let t = toHankaku(rawText).trim();
        t = t.replace(/^(?:【|\[|［|〔|\(|（)\s*[０-９\d]{1,4}\s*(?:】|\]|］|〕|\)|）)\s*/, '');
        t = t.replace(/^[０-９\d]{4}(?:\s+|[\.、:：\t])\s*/, '');
        return t.trim();
    }

    // 强制锁死单栏排版，屏蔽并排分栏
    const style = document.createElement('style');
    style.id = 'zy-desc-fetch-style';
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
        }
    `;
    document.head.appendChild(style);

    // 切换回原文
    const spans = Array.from(document.querySelectorAll('span, button, a'));
    const origBtn = spans.find(s => s.innerText && s.innerText.trim() === '原文');
    if (origBtn && !origBtn.classList.contains('is-active')) {
        origBtn.click();
    }

    const container = document.querySelector('.patent-app__content-view, .patent-app-view__desc, div.desc-content') || document.body;
    const paras = [];

    // 方案 1: DOM 节点级提取 (遍历 div[p] 或 [data-p])
    const pNodes = Array.from(container.querySelectorAll('div[p], [data-p]'));
    if (pNodes.length > 0) {
        // 先探测整篇说明书是否包含官方显式段号（涵盖括号型与无括号 4 位数字型）
        const hasOfficialParagraphs = container.querySelectorAll('b.d_n, span.paragraph-num, span.desc-p-num, span.num').length > 0 ||
            pNodes.some(n => {
                const t = toHankaku((n.innerText || '').trim());
                return /^(?:【|\[|［|〔|\(|（)\s*\d{1,4}\s*(?:】|\]|］|〕|\)|）)/.test(t) ||
                       /^(?:0[0-9]{3}|[1-9][0-9]{3})(?:\s+|[\.、:：\t]|$)/.test(t);
            });

        let pendingHeadings = [];
        let fallbackSeq = 0; // 仅当整篇文献完全无官方标号时使用的备用顺序号

        pNodes.forEach((node) => {
            const rawText = (node.innerText || '').trim();
            if (!rawText) return;

            const officialNum = extractOfficialNum(node, rawText);

            // 检查是否为纯节标题（【背景技术】、【发明内容】、BACKGROUND 等）
            // 节标题绝对不分配独立段号，暂存并合并到紧随其后的首个正文段落
            const isHeading = officialNum == null && (
                HEADING_RE.test(rawText) ||
                (rawText.length <= 40 && !/[。；;\.]/.test(rawText) && (rawText.startsWith('【') || /^[A-Z\s]{3,35}$/.test(rawText)))
            );

            if (isHeading) {
                let hText = rawText.startsWith('【') ? rawText : `【${rawText}】`;
                pendingHeadings.push(hText);
                return;
            }

            // 若整篇文献存在官方标号，但当前节点没有官方段号（如德文附图列表、未标号续行、公式或开头发明名称）：
            if (hasOfficialParagraphs && officialNum == null) {
                if (paras.length > 0) {
                    // 追加合并至上一段
                    paras[paras.length - 1].text += '\n' + rawText;
                } else {
                    // 开头出现的无号内容吸附至随后的首个带号段落
                    pendingHeadings.push(rawText);
                }
                return; // 严禁执行 fallbackSeq++
            }

            // 确定该段的最终标准段号 [XXXX]
            let pStr = '';
            if (officialNum != null) {
                pStr = String(officialNum).padStart(4, '0');
            } else {
                fallbackSeq++;
                pStr = String(fallbackSeq).padStart(4, '0');
            }

            // 清理正文中的前缀段号
            let cleanBody = stripParaPrefix(rawText);

            // 若之前有暂存的节标题或开头无号前缀，前置合并到该正文段落中（保证视觉上节标题不丢失，但段号与智慧芽官方严格对齐）
            if (pendingHeadings.length > 0) {
                const prefix = pendingHeadings.join('\n');
                cleanBody = prefix + '\n' + cleanBody;
                pendingHeadings = [];
            }

            if (cleanBody) {
                paras.push({ no: pStr, text: cleanBody });
            }
        });
    }

    // 方案 2: 若未找到专门结构，使用全局文本正则切分兜底
    if (paras.length === 0) {
        const rawText = (container.innerText || '').trim();
        const re = /(?:^|\n)\s*(?:(?:【|\[|［|〔|\(|（)\s*([０-９\d]{1,4})\s*(?:】|\]|］|〕|\)|）)|([０-９\d]{4})(?:\s+|[\.、:：\t]))\s*([\s\S]*?)(?=(?:\n\s*(?:(?:【|\[|［|〔|\(|（)\s*[０-９\d]{1,4}\s*(?:】|\]|］|〕|\)|）)|[０-９\d]{4}(?:\s+|[\.、:：\t])))|$)/g;
        let m;
        while ((m = re.exec(rawText)) !== null) {
            const rawNum = m[1] || m[2];
            const numHalf = toHankaku(rawNum);
            const pStr = String(parseInt(numHalf, 10)).padStart(4, '0');
            const body = (m[3] || '').replace(/\s+\n/g, '\n').trim();
            if (body) paras.push({ no: pStr, text: body });
        }
    }

    return paras;
}"""


JS_EXTRACT_DESC_TRANSLATION = r"""() => {
    function toHankaku(str) {
        if (!str) return '';
        return str.replace(/[０-９]/g, ch => String.fromCharCode(ch.charCodeAt(0) - 0xFEE0));
    }

    const HEADING_RE = /^(?:【\s*(?:技术领域|所属技术领域|背景技术|技术背景|现有技术|发明内容|实用新型内容|外观设计内容|发明公开|技术方案|有益效果|附图说明|附图简要说明|具体实施方式|具体实施例|实施方式|实施例|符号の説明|符号の簡単な説明|부호의\s*설명|도면의\s*간단한\s*설명|발명의\s*내용|배경기술|기술분야|발명을\s*실시하기\s*위한\s*구체적인\s*내용)\s*】|(?:技术领域|背景技术|技术背景|发明内容|实用新型内容|外观设计内容|附图说明|附图简要说明|具体实施方式|实施方式|实施例)\s*[:：]?|(?:TECHNICAL\s+FIELD|BACKGROUND(?:\s+OF\s+THE\s+INVENTION|\s+ART)?|SUMMARY(?:\s+OF\s+THE\s+INVENTION)?|BRIEF\s+DESCRIPTION\s+OF(?:\s+THE)?\s+DRAWINGS|DETAILED\s+DESCRIPTION(?:\s+OF(?:\s+THE)?\s+(?:PREFERRED\s+)?EMBODIMENTS)?|FIELD\s+OF\s+THE\s+INVENTION|DESCRIPTION\s+OF\s+EMBODIMENTS|CROSS-REFERENCE\s+TO\s+RELATED\s+APPLICATIONS)\s*[:：\.]?|【\s*(?:技術分野|背景技術|発明の概要|発明の効果|図面の簡単な説明|発明を実施するための形態|実施例|符号の説明)\s*】|【\s*(?:기술분야|배경기술|발명의\s*내용|도면의\s*간단한\s*설명|발명을\s*실시하기\s*위한\s*구체적인\s*내용|부호의\s*설명)\s*】|(?:배\s*경\s*기\s*술|기\s*술\s*분\s*야|발\s*명\s*의\s*내\s*용|도\s*면\s*의\s*간\s*단\s*한\s*설\s*명|실\s*시\s*예))\s*$/i;

    function extractOfficialNum(node, rawText) {
        const b = node.querySelector('b.d_n, span.paragraph-num, span.desc-p-num, span.num');
        if (b) {
            const bt = toHankaku(b.innerText || '');
            const m = bt.match(/\d{1,4}/);
            if (m) return parseInt(m[0], 10);
        }

        const cleanT = toHankaku(rawText).trim();

        const mBracket = cleanT.match(/^(?:【|\[|［|〔|\(|（)\s*(\d{1,4})\s*(?:】|\]|］|〕|\)|）)/);
        if (mBracket) {
            return parseInt(mBracket[1], 10);
        }

        const mBare4 = cleanT.match(/^([0-9]{4})(?:\s+|[\.、:：\t]|$)/);
        if (mBare4) {
            const afterText = cleanT.slice(mBare4[0].length).trim();
            if (!/^[年月日%℃度元]/.test(afterText)) {
                return parseInt(mBare4[1], 10);
            }
        }

        return null;
    }

    function stripParaPrefix(rawText) {
        let t = toHankaku(rawText).trim();
        t = t.replace(/^(?:【|\[|［|〔|\(|（)\s*[０-９\d]{1,4}\s*(?:】|\]|］|〕|\)|）)\s*/, '');
        t = t.replace(/^[０-９\d]{4}(?:\s+|[\.、:：\t])\s*/, '');
        return t.trim();
    }

    const trans = document.querySelector('.translation-block') || document.body;
    const paras = [];

    const pNodes = Array.from(trans.querySelectorAll('div[p], [data-p]'));
    if (pNodes.length > 0) {
        const hasOfficialParagraphs = trans.querySelectorAll('b.d_n, span.paragraph-num, span.desc-p-num, span.num').length > 0 ||
            pNodes.some(n => {
                const t = toHankaku((n.innerText || '').trim());
                return /^(?:【|\[|［|〔|\(|（)\s*\d{1,4}\s*(?:】|\]|］|〕|\)|）)/.test(t) ||
                       /^(?:0[0-9]{3}|[1-9][0-9]{3})(?:\s+|[\.、:：\t]|$)/.test(t);
            });

        let pendingHeadings = [];
        let fallbackSeq = 0;

        pNodes.forEach((node) => {
            const rawText = (node.innerText || '').trim();
            if (!rawText) return;

            const officialNum = extractOfficialNum(node, rawText);

            const isHeading = officialNum == null && (
                HEADING_RE.test(rawText) ||
                (rawText.length <= 40 && !/[。；;\.]/.test(rawText) && (rawText.startsWith('【') || /^[A-Z\s]{3,35}$/.test(rawText)))
            );

            if (isHeading) {
                let hText = rawText.startsWith('【') ? rawText : `【${rawText}】`;
                pendingHeadings.push(hText);
                return;
            }

            if (hasOfficialParagraphs && officialNum == null) {
                if (paras.length > 0) {
                    paras[paras.length - 1].text += '\n' + rawText;
                } else {
                    pendingHeadings.push(rawText);
                }
                return;
            }

            let pStr = '';
            if (officialNum != null) {
                pStr = String(officialNum).padStart(4, '0');
            } else {
                fallbackSeq++;
                pStr = String(fallbackSeq).padStart(4, '0');
            }

            let cleanBody = stripParaPrefix(rawText);

            if (pendingHeadings.length > 0) {
                const prefix = pendingHeadings.join('\n');
                cleanBody = prefix + '\n' + cleanBody;
                pendingHeadings = [];
            }

            if (cleanBody) {
                paras.push({ no: pStr, text: cleanBody });
            }
        });
    }

    if (paras.length === 0) {
        const rawText = (trans.innerText || '').trim();
        const re = /(?:^|\n)\s*(?:(?:【|\[|［|〔|\(|（)\s*([０-９\d]{1,4})\s*(?:】|\]|］|〕|\)|）)|([０-９\d]{4})(?:\s+|[\.、:：\t]))\s*([\s\S]*?)(?=(?:\n\s*(?:(?:【|\[|［|〔|\(|（)\s*[０-９\d]{1,4}\s*(?:】|\]|］|〕|\)|）)|[０-９\d]{4}(?:\s+|[\.、:：\t])))|$)/g;
        let m;
        while ((m = re.exec(rawText)) !== null) {
            const rawNum = m[1] || m[2];
            const numHalf = toHankaku(rawNum);
            const pStr = String(parseInt(numHalf, 10)).padStart(4, '0');
            const body = (m[3] || '').trim();
            if (body) paras.push({ no: pStr, text: body });
        }
    }

    return paras;
}"""


def verify_and_heal_description(paras, patent_id=""):
    """
    智慧芽说明书段号一致性校验与自适应对齐门禁（Gatekeeper & Auto-Alignment）
    对说明书段落执行全自动体检与智能修复：
    1. 控制字符清洗（杜绝 JSON 解析崩溃和 HTML 标签切换卡死）
    2. 查重与文本无损融合（多节点同段号合并）
    3. 孤立标题行识别与下移吸附（消除标题占用独立段号导致的错位）
    4. 段号起始基准对齐与平移纠偏（保证从 0001 连续递增）
    5. 返回修复后的干净段落列表与质检指标字典
    """
    if not paras:
        return paras, {"status": "empty", "range": "none", "count": 0, "actions": []}

    actions = []

    # 1. 控制字符安全清洗
    cleaned = []
    for p in paras:
        no = str(p.get("no", "")).strip()
        txt = p.get("text", "")
        safe_txt = "".join(c for c in txt if c in ('\n', '\t') or ord(c) >= 32).strip()
        cleaned.append({"no": no, "text": safe_txt})

    # 2. 查重与同段号文本无损拼接
    deduped = []
    no_to_idx = {}
    for p in cleaned:
        no = p["no"]
        if no in no_to_idx:
            idx = no_to_idx[no]
            deduped[idx]["text"] += "\n" + p["text"]
            actions.append(f"段号 [{no}] 出现重复节点，已执行无损文本合并")
        else:
            no_to_idx[no] = len(deduped)
            deduped.append(p)

    # 3. 扫描并吸附前部的孤立纯节标题段落（杜绝标题占用独立段号导致的错位）
    HEADING_TEXT_RE = re.compile(
        r'^(?:【\s*(?:技术领域|所属技术领域|背景技术|技术背景|现有技术|发明内容|实用新型内容|外观设计内容|发明公开|技术方案|有益效果|附图说明|附图简要说明|具体实施方式|具体实施例|实施方式|实施例|符号の説明|符号の簡単な説明|부호의\s*설명|도면의\s*간단한\s*설명|배경기술|기술분야|발명의\s*내용|발명을\s*실시하기\s*위한\s*구체적인\s*내용)\s*】|'
        r'(?:技术领域|背景技术|技术背景|发明内容|实用新型内容|附图说明|附图简要说明|具体实施方式|实施方式|实施例)\s*[:：]?|'
        r'(?:TECHNICAL\s+FIELD|BACKGROUND(?:\s+OF\s+THE\s+INVENTION|\s+ART)?|SUMMARY(?:\s+OF\s+THE\s+INVENTION)?|BRIEF\s+DESCRIPTION\s+OF(?:\s+THE)?\s+DRAWINGS|DETAILED\s+DESCRIPTION(?:\s+OF(?:\s+THE)?\s+(?:PREFERRED\s+)?EMBODIMENTS)?|FIELD\s+OF\s+THE\s+INVENTION|METHOD\s+OF\s+USE|DESCRIPTION\s+OF\s+EMBODIMENTS)\s*[:：\.]?)$',
        re.I
    )

    def is_pure_isolated_heading(text):
        t = text.strip()
        if not t or len(t) > 40:
            return False
        if any(c in t for c in ['。', '；', ';', '，', ',']):
            return False
        if any(c in t for c in [':', '：']) and not t.endswith((':', '：')):
            return False
        if re.search(r'\d{1,4}\s*[:：、\-—]', t):
            return False
        if HEADING_TEXT_RE.fullmatch(t):
            return True
        if re.fullmatch(r'[A-Z\s]{3,35}', t):
            return True
        return False

    absorbed = []
    i = 0
    while i < len(deduped):
        cur = deduped[i]
        cur_text = cur["text"].strip()
        if is_pure_isolated_heading(cur_text) and i + 1 < len(deduped):
            prefix = cur_text if cur_text.startswith('【') else f"【{cur_text}】"
            deduped[i + 1]["text"] = prefix + "\n" + deduped[i + 1]["text"].strip()
            actions.append(f"剔除孤立标题段 [{cur['no']}]（'{cur_text}'），已自动吸附至随后的段落 [{deduped[i+1]['no']}]")
            i += 1
            continue
        absorbed.append(cur)
        i += 1

    # 4. 检查数字段号的基准对齐与平移纠偏
    int_nos = []
    for p in absorbed:
        if re.fullmatch(r"\d{1,4}", p["no"]):
            int_nos.append(int(p["no"]))

    if int_nos:
        min_no = min(int_nos)
        max_no = max(int_nos)
        total_p = len(int_nos)

        if min_no > 1 and max_no == min_no + total_p - 1:
            offset = min_no - 1
            for p in absorbed:
                if re.fullmatch(r"\d{1,4}", p["no"]):
                    p["no"] = str(int(p["no"]) - offset).zfill(4)
            actions.append(f"检测到说明书段号整体偏移 {offset} 位（首段原为 {str(min_no).zfill(4)}），已自动平移校正对齐至 0001 起始")
        elif min_no > 1 and int_nos[0] > 1:
            offset = int_nos[0] - 1
            for p in absorbed:
                if re.fullmatch(r"\d{1,4}", p["no"]):
                    new_val = int(p["no"]) - offset
                    if new_val >= 1:
                        p["no"] = str(new_val).zfill(4)
            actions.append(f"校正首段段号偏移（原 {str(int_nos[0]).zfill(4)}），重设基准对齐至 0001")

    # 重新统计连续性指标
    final_nums = [int(p["no"]) for p in absorbed if re.fullmatch(r"\d{1,4}", p["no"])]
    if final_nums:
        lo, hi = min(final_nums), max(final_nums)
        exp_set = set(range(lo, hi + 1))
        found_set = set(final_nums)
        gaps = sorted(exp_set - found_set)
        range_str = f"{str(lo).zfill(4)}-{str(hi).zfill(4)}"
    else:
        lo, hi, gaps, range_str = 0, 0, [], "custom"

    report = {
        "status": "pass" if not gaps else "warning_gaps",
        "range": range_str,
        "count": len(absorbed),
        "gaps": [str(g).zfill(4) for g in gaps],
        "actions": actions
    }

    return absorbed, report


def check_continuity(paras, expect=None):
    """
    检查段落连续性：外文文献与说明书不许漏段缺段。
    paras: list of dict(no="0001", text="...")
    """
    if not paras:
        return {"range": "none", "expected": 0, "found": 0, "gaps": [], "duplicates": []}

    nums = []
    seen = set()
    dups = set()
    for p in paras:
        no_str = p.get("no", "")
        if re.fullmatch(r"\d{1,4}", no_str):
            n = int(no_str)
            if n in seen:
                dups.add(n)
            seen.add(n)
            nums.append(n)

    if not nums:
        return {"range": "custom", "expected": len(paras), "found": len(paras), "gaps": [], "duplicates": []}

    lo, hi = min(nums), max(nums)
    expected_set = set(range(lo, hi + 1))
    found_set = set(nums)
    gaps = sorted(expected_set - found_set)

    return {
        "range": f"{str(lo).zfill(4)}-{str(hi).zfill(4)}",
        "expected": len(expected_set) if expect is None else expect,
        "found": len(found_set),
        "gaps": [str(g).zfill(4) for g in gaps],
        "duplicates": [str(d).zfill(4) for d in sorted(dups)],
    }


async def fetch_patent_from_share_url(share_url, headless=True, browser_path=None, zh_json=None, trans_mode='patsnap', with_figures=True):
    """
    通过智慧芽分享链接提取著录项、权利要求和说明书。
    zh_json: 可选自译/外部翻译字典或 JSON 文件路径 {"权1": "...", "0001": "..."}
    trans_mode: 'patsnap' (默认，尝试抓取智慧芽机翻) | 'empty' (跳过机翻直接留空/待补，加快速度供 AI/自译填充)
    with_figures: 是否拉取附图（路线B：下载官方附图并内嵌为 Base64 单文件）
    """
    if not share_url or 'zhihuiya.com' not in share_url:
        raise ValueError("无效的智慧芽分享链接，链接须包含 zhihuiya.com 域名。")

    custom_zh_map = {}
    if zh_json:
        if isinstance(zh_json, str) and os.path.exists(zh_json):
            with open(zh_json, encoding='utf-8') as f:
                custom_zh_map = json.load(f)
        elif isinstance(zh_json, dict):
            custom_zh_map = zh_json

    detected_browser = get_browser_executable(browser_path)
    print(f"[*] 使用浏览器引擎: {detected_browser or 'Playwright 内置 Chromium'}")
    print(f"[*] 翻译模式: {'加载外部自译数据' if custom_zh_map else ('智慧芽机翻模式' if trans_mode == 'patsnap' else '纯原文骨架模式(待补)')}")
    print(f"[*] 正在解析入口链接...")

    abst_url = normalize_share_url(share_url, 'abst')
    clms_url = normalize_share_url(share_url, 'clms')
    desc_url = normalize_share_url(share_url, 'desc')

    result = {
        "share_url": share_url,
        "biblio": {},
        "claims": [],
        "description": [],
        "continuity": {},
        "figures": [],
        "is_foreign": False
    }

    async with async_playwright() as p:
        launch_args = {
            'headless': headless,
            'args': ['--no-sandbox', '--disable-setuid-sandbox', '--disable-gpu']
        }
        if detected_browser:
            launch_args['executable_path'] = detected_browser

        browser = await p.chromium.launch(**launch_args)
        context = await browser.new_context(
            viewport={'width': VIEWPORT_WIDTH, 'height': VIEWPORT_HEIGHT},
            device_scale_factor=1,
            is_mobile=False
        )
        page = await context.new_page()

        try:
            # ------------------------------------------------------------------
            # 1. 打开摘要页 (/patent-view/abst) 建立签名认证会话并抓取著录项
            # ------------------------------------------------------------------
            print(f"[1/3] 访问摘要页建立鉴权会话: {abst_url[:80]}...")
            await page.goto(abst_url, timeout=75000, wait_until='domcontentloaded')
            await page.wait_for_timeout(4000)

            biblio = await page.evaluate(JS_EXTRACT_BIBLIO)
            pn = biblio.get('pn', '')
            if not pn and biblio.get('page_title'):
                m_t = re.search(r'\b([A-Z]{2}\d{5,14}[A-Z\d]*)\b', biblio['page_title'])
                if m_t:
                    pn = m_t.group(1)
            if not pn:
                m_url_pn = re.search(r'[?&]patentId=([A-Za-z0-9-]+)', share_url)
                pn = m_url_pn.group(1) if m_url_pn else 'PATENT'
            biblio['pn'] = pn

            is_chinese = pn.startswith('CN')
            result['is_foreign'] = not is_chinese
            result['biblio'] = biblio
            print(f"    -> 公开号: {pn}")
            print(f"    -> 专利名称: {biblio.get('title') or '（未抓取到标题）'}")
            print(f"    -> 申请人: {biblio.get('applicant') or '（未抓取到申请人）'}")

            # ------------------------------------------------------------------
            # 2. 访问权利要求页 (/patent-view/clms) 抓取权利要求
            # ------------------------------------------------------------------
            print(f"[2/3] 访问权利要求页: {clms_url[:80]}...")
            await page.goto(clms_url, timeout=60000, wait_until='domcontentloaded')
            await page.wait_for_timeout(3500)

            # 提取权利要求原文
            claims_orig = await page.evaluate(JS_EXTRACT_CLAIMS_ORIGINAL)
            print(f"    -> 提取到权利要求原文: {len(claims_orig)} 项")

            # 动态检测语言（解决 WO/TW/HK 等母语为中文但公开号非 CN 的误判问题）
            sample_blob = "".join(c.get('text', '') for c in claims_orig[:5])
            if not sample_blob:
                sample_blob = biblio.get('title', '')
            if is_chinese_text(sample_blob, thresh=0.15):
                is_chinese = True
                result['is_foreign'] = False
            else:
                is_chinese = False
                result['is_foreign'] = True
            print(f"    -> 文献语言性质动态判定: {'中文文献 (单轨模式)' if is_chinese else '外文文献 (双语中英对照模式)'}")

            claims_trans = []
            if not is_chinese and trans_mode == 'patsnap' and not custom_zh_map:
                try:
                    print("    -> 切换至中文机翻模式提取权要译文...")
                    # 尝试点击语言切换按钮
                    lang_switcher = await page.query_selector('.lang-switcher, .patsnap-biz-header-dropdown')
                    if lang_switcher:
                        await lang_switcher.click()
                        await page.wait_for_timeout(600)
                        cn_btn = await page.query_selector('.pendo-track__function-bar__translate-CN, [data-lang="zh"], span:has-text("中文")')
                        if cn_btn:
                            await cn_btn.click()
                            await page.wait_for_timeout(4000)
                    claims_trans = await page.evaluate(JS_EXTRACT_CLAIMS_TRANSLATION)
                    print(f"    -> 提取到权利要求译文: {len(claims_trans)} 项")
                except Exception as e_clm_trans:
                    print(f"    [!] 提取权利要求机翻遇到提示: {e_clm_trans}")

            # 对齐权利要求 (以原文序号为主键)
            trans_map = {c['no']: c['text'] for c in claims_trans if isinstance(c, dict) and 'no' in c}
            aligned_claims = []
            for item in claims_orig:
                c_no = item['no']
                orig_txt = item['text']
                # 优先级: custom_zh_map > 智慧芽机翻 > （待补）
                c_key_variants = [f"权{c_no}", str(c_no), f"Claim {c_no}"]
                custom_trans = next((custom_zh_map[k] for k in c_key_variants if k in custom_zh_map), None)
                final_zh = custom_trans if custom_trans else (trans_map.get(c_no, "") if trans_mode == 'patsnap' else "")

                if is_chinese:
                    aligned_claims.append({
                        "no": f"权{c_no}",
                        "group": "权利要求书",
                        "en": "",
                        "zh": orig_txt
                    })
                else:
                    aligned_claims.append({
                        "no": f"权{c_no}",
                        "group": "权利要求书",
                        "en": orig_txt,
                        "zh": final_zh or "（待补）"
                    })
            result['claims'] = aligned_claims

            # ------------------------------------------------------------------
            # 3. 访问说明书页 (/patent-view/desc) 抓取说明书段落及段号
            # ------------------------------------------------------------------
            print(f"[3/3] 访问说明书页: {desc_url[:80]}...")
            await page.goto(desc_url, timeout=70000, wait_until='domcontentloaded')
            await page.wait_for_timeout(4000)

            # 提取说明书原文并进行段号一致性校验与自适应对齐
            raw_orig = await page.evaluate(JS_EXTRACT_DESC_ORIGINAL)
            desc_orig, orig_report = verify_and_heal_description(raw_orig, patent_id=result.get("biblio", {}).get("pn", ""))
            print(f"    -> 提取到说明书原文段落: {len(desc_orig)} 段 (原始: {len(raw_orig)} 段)")
            if orig_report.get("actions"):
                for act in orig_report["actions"]:
                    print(f"    [✔] 原文段号纠偏门禁: {act}")

            desc_trans = []
            if not is_chinese and trans_mode == 'patsnap' and not custom_zh_map:
                try:
                    print("    -> 切换至中文机翻模式提取说明书译文...")
                    lang_switcher = await page.query_selector('.lang-switcher, .patsnap-biz-header-dropdown')
                    if lang_switcher:
                        await lang_switcher.click()
                        await page.wait_for_timeout(600)
                        cn_btn = await page.query_selector('.pendo-track__function-bar__translate-CN, [data-lang="zh"], span:has-text("中文")')
                        if cn_btn:
                            await cn_btn.click()
                            await page.wait_for_timeout(4500)
                    raw_trans = await page.evaluate(JS_EXTRACT_DESC_TRANSLATION)
                    desc_trans, trans_report = verify_and_heal_description(raw_trans, patent_id=result.get("biblio", {}).get("pn", ""))
                    print(f"    -> 提取到说明书译文段落: {len(desc_trans)} 段 (原始: {len(raw_trans)} 段)")
                    if trans_report.get("actions"):
                        for act in trans_report["actions"]:
                            print(f"    [✔] 译文段号纠偏门禁: {act}")
                except Exception as e_desc_trans:
                    print(f"    [!] 提取说明书机翻遇到提示: {e_desc_trans}")

            # 对齐说明书段落
            desc_trans_map = {p['no']: p['text'] for p in desc_trans if isinstance(p, dict) and 'no' in p}
            aligned_desc = []
            seen_no = set()
            for p in desc_orig:
                p_no = p['no']
                if p_no in seen_no:
                    continue
                seen_no.add(p_no)
                orig_txt = p['text']
                # 优先级: custom_zh_map > 智慧芽机翻 > （待补）
                p_key_variants = [p_no, str(int(p_no)) if p_no.isdigit() else p_no]
                custom_trans = next((custom_zh_map[k] for k in p_key_variants if k in custom_zh_map), None)
                final_zh = custom_trans if custom_trans else (desc_trans_map.get(p_no, "") if trans_mode == 'patsnap' else "")

                if is_chinese:
                    aligned_desc.append({
                        "no": p_no,
                        "group": "说明书",
                        "en": "",
                        "zh": orig_txt
                    })
                else:
                    aligned_desc.append({
                        "no": p_no,
                        "group": "说明书",
                        "en": orig_txt,
                        "zh": final_zh or "（待补）"
                    })
            result['description'] = aligned_desc

            # 连续性检查
            cont = orig_report
            result['continuity'] = cont
            print(f"[*] 说明书连续性自检: 范围={cont.get('range')}, 实有段数={cont.get('count')}, 缺口={cont.get('gaps')}")

            # ------------------------------------------------------------------
            # 4. （可选）访问附图页 (/patent-view/image) 抓取官方附图 (路线B)
            # ------------------------------------------------------------------
            if with_figures:
                img_url = normalize_share_url(share_url, 'image')
                print(f"[4/4] 访问附图页抓取官方附图 (路线B下载内嵌): {img_url[:80]}...")
                # clean_url -> {"bytes": body, "is_abst": bool, "sort_tuple": tuple, "url": clean_u}
                collected_figs = {}

                async def on_fig_resp(resp):
                    u = resp.url
                    # 匹配智慧芽附图相关 CDN 域名
                    if any(domain in u for domain in ["data-fulltext-image.zhihuiya.com", "data-abstract-image.zhihuiya.com", "image.zhihuiya.com"]):
                        # 忽略小图标、svg 等
                        if any(ext in u.lower() for ext in [".png", ".jpg", ".jpeg"]):
                            clean_u = u.split("?")[0]
                            if clean_u in collected_figs:
                                return
                            try:
                                body = await resp.body()
                                if not body or len(body) < 1500:
                                    return

                                is_abst = "data-abstract-image" in u or "abstract" in u.lower()

                                # 解析图号用于自然升序排列
                                m_d = re.search(r'D(\d{5})', clean_u, re.IGNORECASE)
                                m_hda = re.search(r'HDA\d*?(\d{5})', clean_u, re.IGNORECASE)
                                m_imgf = re.search(r'imgf(\d+)', clean_u, re.IGNORECASE)
                                m_srep = re.search(r'srep(\d+)', clean_u, re.IGNORECASE)
                                m_tail = re.search(r'_(\d{4})\.', clean_u, re.IGNORECASE)

                                if is_abst:
                                    sort_tuple = (0, 0, clean_u)
                                elif m_d:
                                    sort_tuple = (1, int(m_d.group(1)), clean_u)
                                elif m_hda:
                                    sort_tuple = (1, int(m_hda.group(1)), clean_u)
                                elif m_imgf:
                                    sort_tuple = (1, int(m_imgf.group(1)), clean_u)
                                elif m_tail:
                                    sort_tuple = (1, int(m_tail.group(1)), clean_u)
                                elif m_srep:
                                    sort_tuple = (2, int(m_srep.group(1)), clean_u)
                                else:
                                    sort_tuple = (3, 0, clean_u)

                                collected_figs[clean_u] = {
                                    "bytes": body,
                                    "is_abst": is_abst,
                                    "sort_tuple": sort_tuple,
                                    "url": clean_u
                                }
                            except Exception:
                                pass

                page.on("response", on_fig_resp)
                try:
                    await page.goto(img_url, timeout=60000, wait_until='domcontentloaded')
                    await page.wait_for_timeout(3500)

                    # 滚动多轮以触发整批懒加载
                    for s_round in range(6):
                        prev_cnt = len(collected_figs)
                        await page.evaluate(r'''() => {
                            const byClass = Array.from(document.querySelectorAll('div')).find(e => 
                                /thum-mainsection-left|commen-scroll-loader/.test(String(e.className || '')) && e.clientHeight > 100);
                            const el = byClass || Array.from(document.querySelectorAll('div')).find(e => 
                                e.scrollHeight > e.clientHeight + 100 && e.clientHeight > 150);
                            if (el) {
                                el.scrollTop = el.scrollTop + Math.round(el.clientHeight * 0.9);
                            } else {
                                window.scrollBy(0, 800);
                            }
                        }''')
                        await page.wait_for_timeout(1500)
                        cur_cnt = len(collected_figs)
                        if cur_cnt > 0 and cur_cnt == prev_cnt and s_round >= 2:
                            break

                    # 排序：摘要附图排首位(0)，全文附图按自然数字升序(1, num)，其余排最后(2)
                    sorted_items = sorted(collected_figs.values(), key=lambda x: x["sort_tuple"])
                    figures_list = []
                    full_idx = 1
                    for i, item in enumerate(sorted_items):
                        b = item["bytes"]
                        b64_str = base64.b64encode(b).decode('utf-8')
                        if item["is_abst"]:
                            fig_name = "摘要附图"
                        else:
                            fig_name = f"图 {full_idx}"
                            full_idx += 1

                        figures_list.append({
                            "no": str(i + 1),
                            "name": fig_name,
                            "key": os.path.basename(item["url"]),
                            "src": f"data:image/png;base64,{b64_str}"
                        })

                    result['figures'] = figures_list
                    print(f"    -> 成功抓取并内嵌官方附图: {len(figures_list)} 张")
                except Exception as e_fig:
                    print(f"    [!] 抓取附图遇到提示: {e_fig}")

        finally:
            await browser.close()

    # 5. 自动归纳附图标记与构件术语字典 (0 Token 消耗)
    result['glossary'] = extract_reference_numerals(result.get('claims', []), result.get('description', []))
    print(f"[*] 自动提取附图标记与术语表: 共归纳提取到 {len(result['glossary'])} 个标记构件")

    return result


def export_as_markdown(patent_data, out_path):
    """
    导出为符合 patent-anno-share 规范的标准中英对照 Markdown 文件。
    """
    bib = patent_data.get('biblio', {})
    pn = bib.get('pn', 'PATENT')
    title = bib.get('title', '')
    applicant = bib.get('applicant', '')
    pub_date = bib.get('pub_date', '')

    lines = []
    lines.append(f"# {pn} - {title}")
    lines.append("")
    meta_info = []
    if applicant: meta_info.append(f"申请人：{applicant}")
    if pub_date: meta_info.append(f"公开日：{pub_date}")
    meta_info.append("来源：智慧芽免登录分享链接")
    lines.append(f"> {' | '.join(meta_info)}")
    lines.append("")

    # 1. 权利要求书
    claims = patent_data.get('claims', [])
    if claims:
        lines.append("## 权利要求书")
        lines.append("")
        for c in claims:
            no = c['no']
            en_txt = (c.get('en') or '').strip()
            zh_txt = (c.get('zh') or '').strip()
            lines.append(f"### 【{no}】")
            lines.append("")
            if en_txt:
                for ln in en_txt.split('\n'):
                    lines.append(f"> {ln}")
                lines.append("")
            if zh_txt:
                lines.append(f"　　**译**：{zh_txt}")
            lines.append("")

    # 2. 说明书
    desc = patent_data.get('description', [])
    if desc:
        lines.append("## 说明书")
        lines.append("")
        for p in desc:
            no = p['no']
            en_txt = (p.get('en') or '').strip()
            zh_txt = (p.get('zh') or '').strip()
            lines.append(f"### 【{no}】")
            lines.append("")
            if en_txt:
                for ln in en_txt.split('\n'):
                    lines.append(f"> {ln}")
                lines.append("")
            if zh_txt:
                lines.append(f"　　**译**：{zh_txt}")
            lines.append("")

    content = "\n".join(lines)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[+] 中英对照 Markdown 已导出至: {out_path}")
    return out_path


def export_as_data_json(patent_data, out_path):
    """
    导出为符合 build_single.py 所需的 data.json 数据结构。
    """
    bib = patent_data.get('biblio', {})
    pn = bib.get('pn', 'PATENT')
    title = bib.get('title', '')
    applicant = bib.get('applicant', '')
    pub_date = bib.get('pub_date', '')

    subtitle_parts = []
    if applicant:
        clean_applicant = re.sub(r'[\r\n]+', '、', applicant).strip('、 ')
        subtitle_parts.append(f"申请人：{clean_applicant}")
    if pub_date: subtitle_parts.append(f"公开日：{pub_date}")
    subtitle_parts.append("划选文字 → 加高亮 → 写备注。批注存本机浏览器，用「导出/导入」在设备间搬运。")

    all_items = []
    # 权要
    for c in patent_data.get('claims', []):
        all_items.append({
            "no": c['no'],
            "group": c.get('group', '权利要求书'),
            "en": c.get('en', ''),
            "zh": c.get('zh', ''),
            "enMarks": [],
            "zhMarks": []
        })
    # 说明书
    for d in patent_data.get('description', []):
        all_items.append({
            "no": d['no'],
            "group": d.get('group', '说明书'),
            "en": d.get('en', ''),
            "zh": d.get('zh', ''),
            "enMarks": [],
            "zhMarks": []
        })

    data_payload = {
        "docId": pn,
        "title": f"{pn} - {title}" if title else pn,
        "subtitle": " · ".join(subtitle_parts),
        "count": len(all_items),
        "claim_count": len(patent_data.get('claims', [])),
        "desc_count": len(patent_data.get('description', [])),
        "figures": patent_data.get('figures', []),
        "continuity": patent_data.get('continuity', {}),
        "glossary": patent_data.get('glossary', []),
        "is_foreign": patent_data.get('is_foreign', False),
        "items": all_items
    }

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data_payload, f, ensure_ascii=False, indent=2)
    print(f"[+] 渲染数据 JSON 已导出至: {out_path}")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="从智慧芽分享链接提取权要与说明书并生成结构化数据")
    parser.add_argument("url", help="智慧芽免登录分享链接")
    parser.add_argument("--out-data", help="输出供 HTML 渲染的 data.json 路径")
    parser.add_argument("--out-md", help="输出标准中英对照 Markdown 文件路径")
    parser.add_argument("--out-raw", help="输出原始抓取 JSON 文件路径")
    parser.add_argument("--out-dir", default=".", help="输出目录 (若未单独指定文件路径，自动按公开号命名)")
    parser.add_argument("--zh-json", help="外部自译/AI翻译 JSON 文件路径 (用于覆盖机翻)")
    parser.add_argument("--trans-mode", choices=['patsnap', 'empty'], default='patsnap',
                        help="翻译来源策略: patsnap (默认抓取智慧芽机翻) | empty (不抓机翻，保留骨架待补)")
    parser.add_argument("--with-figures", action=argparse.BooleanOptionalAction, default=True,
                        help="是否拉取官方附图 (路线B：下载内嵌Base64单文件)")
    parser.add_argument("--no-headless", action="store_true", help="显示浏览器窗口进行调试")
    parser.add_argument("--browser-path", help="指定浏览器可执行文件路径")

    args = parser.parse_args()

    patent_data = asyncio.run(fetch_patent_from_share_url(
        args.url,
        headless=not args.no_headless,
        browser_path=args.browser_path,
        zh_json=args.zh_json,
        trans_mode=args.trans_mode,
        with_figures=args.with_figures
    ))

    pn = patent_data.get('biblio', {}).get('pn') or 'PATENT'
    out_dir = args.out_dir
    os.makedirs(out_dir, exist_ok=True)
    inter_dir = os.path.join(out_dir, "_intermediates")
    os.makedirs(inter_dir, exist_ok=True)

    # 1. 原始 JSON (存入 _intermediates 子目录)
    raw_path = args.out_raw or os.path.join(inter_dir, f"{pn}_patsnap_raw.json")
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(patent_data, f, ensure_ascii=False, indent=2)
    print(f"[+] 原始抓取数据已保存至: {raw_path}")

    # 2. Markdown 导出 (存入 _intermediates 子目录)
    md_path = args.out_md or os.path.join(inter_dir, f"{pn}_全文_中英对照.md")
    export_as_markdown(patent_data, md_path)

    # 3. data.json 导出 (存入 _intermediates 子目录)
    data_path = args.out_data or os.path.join(inter_dir, f"{pn}_data.json")
    export_as_data_json(patent_data, data_path)

    print("\n[✔] 抓取与对齐全部完成！")


if __name__ == "__main__":
    main()
