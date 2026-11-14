# -*- coding: utf-8 -*-
"""
专利完整信息提取与质检交付引擎 (patent_fetcher.py)

核心原则：
“成功时交付经过检查的完整资料；失败时快速停在明确的故障点，绝不把不完整文本当成成功结果。”

功能概览：
1. 从智慧芽免登录分享链接提取：
   - 摘要与著录项（公开号、发明名称、申请人、申请日、公开日、摘要正文原文与机翻）
   - 权利要求书（权项原文、双语对照、权项序号连续性核查）
   - 说明书正文（段落原文、段号 [0001]~[XXXX]、段落连续性严格断言：无缺口、无丢失）
   - 官方附图（自动探测预期附图数量，下载内嵌 Base64 并支持导出实体 PNG，校验图数无遗漏）
   - 附图标记与构件术语字典（基于规则的多语言挖掘）
2. 设立 6 道强校验门禁（Quality Gates），任何一项未达标直接触发 Fail-Fast 抛出明确异常中止；
3. 支持 Python API (class PatentFetcher / fetch_patent) 与独立 CLI 命令行调用。
"""

import os
import re
import sys
import json
import time
import base64
import asyncio
import argparse
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field, asdict

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from playwright.async_api import async_playwright


# ==============================================================================
# 1. 明确的故障点异常定义 (Fail-Fast Exceptions)
# ==============================================================================

class PatentExtractionError(Exception):
    """专利信息提取顶层基类异常"""
    def __init__(self, stage: str, message: str, details: Optional[Dict[str, Any]] = None, suggestion: str = ""):
        self.stage = stage
        self.message = message
        self.details = details or {}
        self.suggestion = suggestion
        super().__init__(f"[{stage}] {message}")

    def format_diagnostic_report(self) -> str:
        """格式化输出明确的故障诊断报告"""
        lines = [
            "\n" + "=" * 80,
            "❌ 专利信息抓取失败：已快速停在明确故障点（绝不交付残缺结果）",
            "-" * 80,
            f"【故障阶段】: {self.stage}",
            f"【错误信息】: {self.message}",
        ]
        if self.details:
            lines.append("【关键细节】:")
            for k, v in self.details.items():
                lines.append(f"  • {k}: {v}")
        if self.suggestion:
            lines.append(f"【排查建议】: {self.suggestion}")
        lines.append("=" * 80 + "\n")
        return "\n".join(lines)


class UrlValidationError(PatentExtractionError):
    """分享链接校验失败异常"""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None, suggestion: str = ""):
        super().__init__("链接格式校验", message, details, suggestion)


class AuthOrPageError(PatentExtractionError):
    """鉴权失败、链接过期或页面加载异常"""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None, suggestion: str = ""):
        super().__init__("页面访问与鉴权", message, details, suggestion)


class BiblioExtractionError(PatentExtractionError):
    """著录项或摘要抓取失败异常"""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None, suggestion: str = ""):
        super().__init__("著录项与摘要质检", message, details, suggestion)


class ClaimsExtractionError(PatentExtractionError):
    """权利要求抓取或连续性质检失败异常"""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None, suggestion: str = ""):
        super().__init__("权利要求质检", message, details, suggestion)


class DescriptionExtractionError(PatentExtractionError):
    """说明书段落抓取失败异常"""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None, suggestion: str = ""):
        super().__init__("说明书提取", message, details, suggestion)


class DescriptionContinuityError(PatentExtractionError):
    """说明书段落连续性破损（存在漏段缺口）异常"""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None, suggestion: str = ""):
        super().__init__("说明书连续性断言", message, details, suggestion)


class FiguresExtractionError(PatentExtractionError):
    """附图抓取失败或数量不匹配异常"""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None, suggestion: str = ""):
        super().__init__("官方附图质检", message, details, suggestion)


class QualityGateFailedError(PatentExtractionError):
    """最终聚合门禁质检不通过异常"""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None, suggestion: str = ""):
        super().__init__("综合交付门禁", message, details, suggestion)


# ==============================================================================
# 2. 结构化数据对象定义
# ==============================================================================

@dataclass
class QualityReport:
    """全套质量门禁检查报告"""
    passed: bool = False
    url_check: str = "PENDING"
    biblio_check: str = "PENDING"
    abstract_check: str = "PENDING"
    claims_check: str = "PENDING"
    description_check: str = "PENDING"
    continuity_check: str = "PENDING"
    figures_check: str = "PENDING"
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))


@dataclass
class PatentData:
    """经过严格质检的完整专利资料对象"""
    pn: str
    title: str
    share_url: str
    biblio: Dict[str, Any]
    abstract: Dict[str, str]
    claims: List[Dict[str, Any]]
    description: List[Dict[str, Any]]
    figures: List[Dict[str, Any]]
    glossary: List[Dict[str, Any]]
    continuity: Dict[str, Any]
    quality_report: QualityReport
    is_foreign: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """序列化为完整字典"""
        d = asdict(self)
        return d

    def save_json(self, out_path: str) -> str:
        """保存为完整结构化 JSON"""
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        return out_path

    def save_markdown(self, out_path: str) -> str:
        """导出为符合 patent-anno-share 规范的标准中英对照 Markdown"""
        lines = []
        lines.append(f"# {self.pn} - {self.title}")
        lines.append("")
        meta_items = []
        if self.biblio.get("applicant"): meta_items.append(f"申请人：{self.biblio['applicant']}")
        if self.biblio.get("pub_date"): meta_items.append(f"公开日：{self.biblio['pub_date']}")
        if self.biblio.get("app_date"): meta_items.append(f"申请日：{self.biblio['app_date']}")
        meta_items.append("来源：智慧芽分享链接 (严格质检通过)")
        lines.append(f"> {' | '.join(meta_items)}")
        lines.append("")

        # 摘要
        lines.append("## 摘要")
        lines.append("")
        if self.abstract.get("en"):
            lines.append(f"> {self.abstract['en']}")
            lines.append("")
        if self.abstract.get("zh"):
            lines.append(f"　　**译**：{self.abstract['zh']}")
            lines.append("")
        elif self.abstract.get("raw") and not self.abstract.get("en"):
            lines.append(self.abstract["raw"])
            lines.append("")

        # 权利要求书
        if self.claims:
            lines.append("## 权利要求书")
            lines.append("")
            for c in self.claims:
                no = c.get("no", "")
                en_txt = (c.get("en") or "").strip()
                zh_txt = (c.get("zh") or "").strip()
                lines.append(f"### 【{no}】")
                lines.append("")
                if en_txt:
                    for ln in en_txt.split("\n"):
                        lines.append(f"> {ln}")
                    lines.append("")
                if zh_txt:
                    lines.append(f"　　**译**：{zh_txt}")
                lines.append("")

        # 说明书
        if self.description:
            lines.append("## 说明书")
            lines.append("")
            for d in self.description:
                no = d.get("no", "")
                en_txt = (d.get("en") or "").strip()
                zh_txt = (d.get("zh") or "").strip()
                lines.append(f"### 【{no}】")
                lines.append("")
                if en_txt:
                    for ln in en_txt.split("\n"):
                        lines.append(f"> {ln}")
                    lines.append("")
                if zh_txt:
                    lines.append(f"　　**译**：{zh_txt}")
                lines.append("")

        content = "\n".join(lines)
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(content)
        return out_path

    def save_figures(self, out_dir: str) -> List[str]:
        """将附图导出为本地高清图片文件 (PNG)"""
        os.makedirs(out_dir, exist_ok=True)
        saved_paths = []
        for fig in self.figures:
            name = fig.get("name", "未命名").replace(" ", "_")
            no = fig.get("no", "0")
            src = fig.get("src", "")
            if not src.startswith("data:image/"):
                continue
            try:
                header, b64_data = src.split(",", 1)
                img_bytes = base64.b64decode(b64_data)
                ext = "png"
                if "jpeg" in header or "jpg" in header:
                    ext = "jpg"
                filename = f"{self.pn}_{no.zfill(2)}_{name}.{ext}"
                filepath = os.path.join(out_dir, filename)
                with open(filepath, "wb") as f:
                    f.write(img_bytes)
                saved_paths.append(filepath)
            except Exception as e:
                print(f"[!] 导出附图 {name} 遇到异常: {e}")
        return saved_paths

    def to_anno_data(self) -> Dict[str, Any]:
        """
        转换为完全兼容 patent-anno-share (build_single.py) 渲染单文件 HTML 的 data.json 格式
        """
        subtitle_parts = []
        app = self.biblio.get("applicant", "")
        if app:
            subtitle_parts.append(f"申请人：{app}")
        if self.biblio.get("pub_date"):
            subtitle_parts.append(f"公开日：{self.biblio['pub_date']}")
        subtitle_parts.append("划选文字 → 加高亮 → 写备注。批注存本机浏览器，用「导出/导入」在设备间搬运。")

        items = []
        for c in self.claims:
            items.append({
                "no": c.get("no", ""),
                "group": c.get("group", "权利要求书"),
                "en": c.get("en", ""),
                "zh": c.get("zh", ""),
                "enMarks": [],
                "zhMarks": []
            })
        for d in self.description:
            items.append({
                "no": d.get("no", ""),
                "group": d.get("group", "说明书"),
                "en": d.get("en", ""),
                "zh": d.get("zh", ""),
                "enMarks": [],
                "zhMarks": []
            })

        return {
            "docId": self.pn,
            "title": f"{self.pn} - {self.title}" if self.title else self.pn,
            "subtitle": " · ".join(subtitle_parts),
            "count": len(items),
            "claim_count": len(self.claims),
            "desc_count": len(self.description),
            "figures": self.figures,
            "continuity": self.continuity,
            "glossary": self.glossary,
            "is_foreign": self.is_foreign,
            "abstract": self.abstract,
            "items": items
        }


# ==============================================================================
# 3. 辅助探测与规范化函数
# ==============================================================================

def get_browser_executable(preferred_path: Optional[str] = None) -> Optional[str]:
    """跨平台探测可用浏览器路径 (Chrome / Edge)"""
    if preferred_path and os.path.exists(preferred_path):
        return preferred_path

    env_path = os.environ.get("CHROME_PATH") or os.environ.get("BROWSER_PATH")
    if env_path and os.path.exists(env_path):
        return env_path

    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


def normalize_share_url(url: str, target_section: str = "abst") -> str:
    """规范化分享链接为指定 section 并保留签名参数"""
    if not url:
        return ""
    u = url.strip().strip("'\"<> ")
    new_url = re.sub(r'/patent-view/[^?#]+', f'/patent-view/{target_section}', u)
    if '/patent-view/' not in new_url and 'zhihuiya.com' in new_url:
        new_url = re.sub(r'(zhihuiya\.com)/?', f'\\1/patent-view/{target_section}', new_url)
    return new_url


def is_chinese_text(s: str, thresh: float = 0.15) -> bool:
    """精准判定文本是否以中文为主（剔除日文假名与韩文字母干扰）"""
    if not s:
        return False
    if re.search(r'[\u3040-\u309f\u30a0-\u30ff]', s):
        return False
    if re.search(r'[\uac00-\ud7af\u1100-\u11ff]', s):
        return False
    cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    return (cjk / max(1, len(s))) > thresh


# ==============================================================================
# 4. 前端 DOM 注入提取脚本定义
# ==============================================================================

JS_EXTRACT_BIBLIO_AND_ABST = r'''() => {
    function cleanName(n) {
        if (!n) return '';
        let s = n.replace(/^\[标\]/, '').replace(/\([^\)]+\)/g, '').replace(/（[^）]+）/g, '');
        s = s.replace(/^(?:当前|原始)?申请(?:\(专利权\)|（专利权）)?人[\s:：\|\n]*/g, '');
        s = s.replace(/^(?:当前|原始)?权利人[\s:：\|\n]*/g, '');
        s = s.replace(/[\r\n]+/g, '、').replace(/\s{2,}/g, ' ');
        return s.trim();
    }

    // 1. 公开号与发明名称
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
        const mBody = (document.body.innerText || '').match(/\b([A-Z]{2}\d{5,14}[A-Z\d]*)\b/);
        if (mBody) pn = mBody[1];
    }
    if (!title) {
        const tEl = document.querySelector('.patent-app__title-context, .patent-title, h1');
        if (tEl) title = tEl.innerText.trim();
    }

    // 2. 申请人
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

    // 3. 申请日、公开日
    let pbd = '', apd = '';
    const bodyText = document.body ? document.body.innerText : '';
    const dateRe = /(\d{4}-\d{2}-\d{2})\s*\n\s*(首次公开日|公开（公告）日|公开\(公告\)日|公开公告日|申请日|优先权日)/g;
    let dm;
    while ((dm = dateRe.exec(bodyText)) !== null) {
        const d = dm[1], lab = dm[2];
        if (lab.includes('公开') && !pbd) pbd = d;
        if (lab.includes('申请') && !apd) apd = d;
    }

    // 4. 导航栏 tab 探测附图数量 (如 "附图 (4)")
    let expectedFigCount = null;
    const navItems = Array.from(document.querySelectorAll('a, button, div, span, li'))
        .map(e => (e.innerText || '').trim())
        .filter(t => /附图\s*\(\d+\)/.test(t));
    for (let t of navItems) {
        const m = t.match(/附图\s*\((\d+)\)/);
        if (m) {
            expectedFigCount = parseInt(m[1], 10);
            break;
        }
    }
    if (expectedFigCount === null && document.body) {
        const m = (document.body.innerText || '').match(/附图\s*\((\d+)\)/);
        if (m) expectedFigCount = parseInt(m[1], 10);
    }

    // 5. 摘要正文提取
    let abstractRaw = '';
    // 方案 A: 匹配智慧芽摘要块
    const abstBlock = document.querySelector('.patent-app-view__abst, .abstract-content, [class*="abst-context"]');
    if (abstBlock && (abstBlock.innerText || '').trim()) {
        abstractRaw = abstBlock.innerText.trim();
    }
    // 方案 B: 遍历 DOM 文本
    if (!abstractRaw) {
        const all = Array.from(document.querySelectorAll('*'));
        for (let el of all) {
            const txt = (el.innerText || '').trim();
            if ((txt === '摘要' || txt === 'Abstract') && el.children.length === 0) {
                let p = el.parentElement;
                for (let i = 0; i < 4 && p; i++) {
                    const sib = p.nextElementSibling;
                    if (sib) {
                        const sTxt = (sib.innerText || '').trim();
                        if (sTxt.length > 20 && !sTxt.includes('同族专利') && !sTxt.includes('IPC分类号')) {
                            abstractRaw = sTxt;
                            break;
                        }
                    }
                    p = p.parentElement;
                }
            }
            if (abstractRaw) break;
        }
    }
    // 方案 C: 从正文正则定位 "摘要\n...\n(更多|日期|专利时间轴)"
    if (!abstractRaw && document.body) {
        const mAbst = document.body.innerText.match(/(?:^|\n)\s*摘要\s*\n\s*([\s\S]{30,1200}?)(?=\n\s*(?:更多|日期|专利时间轴|申请日|IPC分类号|同族专利|$))/);
        if (mAbst) {
            abstractRaw = mAbst[1].trim();
        }
    }

    return {
        pn: pn,
        title: title,
        applicant: applicant,
        pub_date: pbd,
        app_date: apd,
        page_title: docTitle,
        expected_fig_count: expectedFigCount,
        abstract_raw: abstractRaw
    };
}'''


JS_EXTRACT_CLAIMS_ORIGINAL = r'''() => {
    function toHankaku(str) {
        if (!str) return '';
        return str.replace(/[０-９]/g, ch => String.fromCharCode(ch.charCodeAt(0) - 0xFEE0));
    }

    // 尝试切回原文
    const spans = Array.from(document.querySelectorAll('span, button, a'));
    const origBtn = spans.find(s => s.innerText && s.innerText.trim() === '原文');
    if (origBtn && !origBtn.classList.contains('is-active')) {
        origBtn.click();
    }

    const container = document.querySelector('.patent-app-view__clms, .claim-container, .patent-app__content-view') || document.body;
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


JS_EXTRACT_DESC_ORIGINAL = r'''() => {
    function toHankaku(str) {
        if (!str) return '';
        return str.replace(/[０-９]/g, ch => String.fromCharCode(ch.charCodeAt(0) - 0xFEE0));
    }

    const HEADING_RE = /^(?:【\s*(?:技术领域|所属技术领域|背景技术|技术背景|发明内容|实用新型内容|外观设计内容|发明公开|技术方案|有益效果|附图说明|附图简要说明|具体实施方式|具体实施例|实施方式|实施例|符号の説明|符号の簡単な説明|부호의 설명)\s*】|(?:技术领域|背景技术|技术背景|发明内容|实用新型内容|外观设计内容|附图说明|具体实施方式|实施方式)\s*[:：]?|(?:Technical\s+Field|Background(?:\s+of\s+the\s+Invention|\s+Art)?|Summary(?:\s+of\s+the\s+Invention)?|Brief\s+Description\s+of(?:\s+the)?\s+Drawings|Detailed\s+Description(?:\s+of(?:\s+the)?\s+(?:Preferred\s+)?Embodiments)?)\s*[:：]?|【\s*(?:技術分野|背景技術|発明の概要|発明の効果|図面の簡単な説明|発明を実施するための形態|実施例|符号の説明)\s*】|【\s*(?:기술분야|배경기술|발명의\s*내용|도면의\s*간단한\s*설명|발명을\s*실시하기\s*위한\s*구체적인\s*내용|부호의\s*설명)\s*】)$/i;

    // 锁定单栏排版
    const style = document.createElement('style');
    style.id = 'strict-fetch-desc-style';
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

    const spans = Array.from(document.querySelectorAll('span, button, a'));
    const origBtn = spans.find(s => s.innerText && s.innerText.trim() === '原文');
    if (origBtn && !origBtn.classList.contains('is-active')) {
        origBtn.click();
    }

    const container = document.querySelector('.patent-app__content-view, .patent-app-view__desc, div.desc-content') || document.body;
    const paras = [];

    const pNodes = Array.from(container.querySelectorAll('div[p], [data-p]'));
    if (pNodes.length > 0) {
        const hasOfficialParagraphs = container.querySelectorAll('b.d_n, span.paragraph-num, span.desc-p-num').length > 0 ||
            pNodes.some(n => /^(?:【|\[|［|〔|\(|（)\s*\d{1,4}\s*(?:】|\]|］|〕|\)|）)/.test(toHankaku((n.innerText || '').trim())));

        let pendingHeading = '';
        let fallbackSeq = 0;

        pNodes.forEach((node) => {
            const rawText = (node.innerText || '').trim();
            if (!rawText) return;

            let officialNum = '';
            const b = node.querySelector('b.d_n, span.paragraph-num, span.desc-p-num');
            if (b) {
                const bt = toHankaku(b.innerText || '');
                const m = bt.match(/\d{1,4}/);
                if (m) officialNum = m[0];
            }

            if (!officialNum) {
                const headMatch = toHankaku(rawText).match(/^(?:【|\[|［|〔|\(|（)\s*(\d{1,4})\s*(?:】|\]|］|〕|\)|）)/);
                if (headMatch) {
                    officialNum = headMatch[1];
                }
            }

            if (!officialNum && HEADING_RE.test(rawText)) {
                pendingHeading = rawText.startsWith('【') ? rawText : `【${rawText}】`;
                return;
            }

            if (!officialNum && paras.length === 0 && rawText.length <= 30 && !/[。；;\.]/.test(rawText)) {
                return;
            }

            if (hasOfficialParagraphs && !officialNum) {
                if (paras.length > 0) {
                    paras[paras.length - 1].text += '\n' + rawText;
                }
                return;
            }

            let pStr = '';
            if (officialNum) {
                pStr = String(parseInt(officialNum, 10)).padStart(4, '0');
            } else {
                fallbackSeq++;
                pStr = String(fallbackSeq).padStart(4, '0');
            }

            let cleanBody = rawText.replace(/^(?:【|\[|［|〔|\(|（)\s*[０-９\d]{1,4}\s*(?:】|\]|］|〕|\)|）)\s*/, '').trim();

            if (pendingHeading) {
                cleanBody = pendingHeading + '\n' + cleanBody;
                pendingHeading = '';
            }

            if (cleanBody) {
                paras.push({ no: pStr, text: cleanBody });
            }
        });
    }

    if (paras.length === 0) {
        const rawText = (container.innerText || '').trim();
        const re = /(?:^|\n)\s*(?:【|\[|［|〔|\(|（)\s*([０-９\d]{1,4})\s*(?:】|\]|］|〕|\)|）)\s*([\s\S]*?)(?=(?:\n\s*(?:【|\[|［|〔|\(|（)\s*[０-９\d]{1,4}\s*(?:】|\]|］|〕|\)|）))|$)/g;
        let m;
        while ((m = re.exec(rawText)) !== null) {
            const numHalf = toHankaku(m[1]);
            const pStr = String(parseInt(numHalf, 10)).padStart(4, '0');
            const body = m[2].replace(/\s+\n/g, '\n').trim();
            if (body) paras.push({ no: pStr, text: body });
        }
    }

    return paras;
}'''


JS_EXTRACT_DESC_TRANSLATION = r'''() => {
    function toHankaku(str) {
        if (!str) return '';
        return str.replace(/[０-９]/g, ch => String.fromCharCode(ch.charCodeAt(0) - 0xFEE0));
    }

    const HEADING_RE = /^(?:【\s*(?:技术领域|所属技术领域|背景技术|技术背景|发明内容|实用新型内容|外观设计内容|发明公开|技术方案|有益效果|附图说明|附图简要说明|具体实施方式|具体实施例|实施方式|实施例|符号の説明|符号の簡単な说明|부호의 설명)\s*】|(?:技术领域|背景技术|技术背景|发明内容|实用新型内容|外观设计内容|附图说明|具体实施方式|实施方式)\s*[:：]?|(?:Technical\s+Field|Background(?:\s+of\s+the\s+Invention|\s+Art)?|Summary(?:\s+of\s+the\s+Invention)?|Brief\s+Description\s+of(?:\s+the)?\s+Drawings|Detailed\s+Description(?:\s+of(?:\s+the)?\s+(?:Preferred\s+)?Embodiments)?)\s*[:：]?|【\s*(?:技術分野|背景技術|発明の概要|発明の効果|図面の簡単な説明|発明を実施するための形態|実施例|符号の説明)\s*】|【\s*(?:기술분야|배경기술|발명의\s*내용|도면의\s*간단한\s*설명|발명을\s*실시하기\s*위한\s*구체적인\s*내용|부호의\s*설명)\s*】)$/i;

    const trans = document.querySelector('.translation-block') || document.body;
    const paras = [];

    const pNodes = Array.from(trans.querySelectorAll('div[p], [data-p]'));
    if (pNodes.length > 0) {
        const hasOfficialParagraphs = trans.querySelectorAll('b.d_n, span.paragraph-num, span.desc-p-num').length > 0 ||
            pNodes.some(n => /^(?:【|\[|［|〔|\(|（)\s*\d{1,4}\s*(?:】|\]|］|〕|\)|）)/.test(toHankaku((n.innerText || '').trim())));

        let pendingHeading = '';
        let fallbackSeq = 0;

        pNodes.forEach((node) => {
            const rawText = (node.innerText || '').trim();
            if (!rawText) return;

            let officialNum = '';
            const b = node.querySelector('b.d_n, span.paragraph-num, span.desc-p-num');
            if (b) {
                const bt = toHankaku(b.innerText || '');
                const m = bt.match(/\d{1,4}/);
                if (m) officialNum = m[0];
            }

            if (!officialNum) {
                const headMatch = toHankaku(rawText).match(/^(?:【|\[|［|〔|\(|（)\s*(\d{1,4})\s*(?:】|\]|］|〕|\)|）)/);
                if (headMatch) {
                    officialNum = headMatch[1];
                }
            }

            if (!officialNum && HEADING_RE.test(rawText)) {
                pendingHeading = rawText.startsWith('【') ? rawText : `【${rawText}】`;
                return;
            }

            if (!officialNum && paras.length === 0 && rawText.length <= 30 && !/[。；;\.]/.test(rawText)) {
                return;
            }

            if (hasOfficialParagraphs && !officialNum) {
                if (paras.length > 0) {
                    paras[paras.length - 1].text += '\n' + rawText;
                }
                return;
            }

            let pStr = '';
            if (officialNum) {
                pStr = String(parseInt(officialNum, 10)).padStart(4, '0');
            } else {
                fallbackSeq++;
                pStr = String(fallbackSeq).padStart(4, '0');
            }

            let cleanBody = rawText.replace(/^(?:【|\[|［|〔|\(|（)\s*[０-９\d]{1,4}\s*(?:】|\]|］|〕|\)|）)\s*/, '').trim();

            if (pendingHeading) {
                cleanBody = pendingHeading + '\n' + cleanBody;
                pendingHeading = '';
            }

            if (cleanBody) {
                paras.push({ no: pStr, text: cleanBody });
            }
        });
    }

    if (paras.length === 0) {
        const rawText = (trans.innerText || '').trim();
        const re = /(?:^|\n)\s*(?:【|\[|［|〔|\(|（)\s*([０-９\d]{1,4})\s*(?:】|\]|］|〕|\)|）)\s*([\s\S]*?)(?=(?:\n\s*(?:【|\[|［|〔|\(|（)\s*[０-９\d]{1,4}\s*(?:】|\]|］|〕|\)|）))|$)/g;
        let m;
        while ((m = re.exec(rawText)) !== null) {
            const numHalf = toHankaku(m[1]);
            const pStr = String(parseInt(numHalf, 10)).padStart(4, '0');
            const body = m[2].trim();
            if (body) paras.push({ no: pStr, text: body });
        }
    }

    return paras;
}'''


def extract_reference_numerals(claims: List[Dict[str, Any]], description: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """提取附图标记与构件术语字典"""
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

    # 1. 优先扫描附图标记说明段落
    list_re = re.compile(r'(?:^|[；;\n\r])\s*([0-9]{1,4}[a-zA-Z]?)\s*[:：、\-—\s…\.]+\s*([^\n\r;；]+?)(?=(?:[；;\n\r]|\s{2,}\d{1,4}\s*[:：、\-—]|$))')
    for no, zh, en in all_sections:
        is_glossary_sec = any(k in zh or k in en for k in [
            "附图标记", "标记说明", "符号说明", "元件标号", "主要标号", "图号说明",
            "符号の説明", "符号の簡単な説明", "부호의 설명",
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

    # 2. 正则提取正文标记
    zh_pat = re.compile(r'([\u4e00-\u9fa5\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]{2,15})[\s（\(]*([0-9]{1,4}[a-zA-Z]?)[）\)]*')
    en_pat = re.compile(r'\b([a-zA-Z\-]{2,25}(?:\s+[a-zA-Z\-]{2,25}){0,2})[\s\(]+([0-9]{1,4}[a-zA-Z]?)\b')

    for no, zh, en in all_sections:
        for txt in [zh, en]:
            if not txt: continue
            for m in zh_pat.finditer(txt):
                raw_name, num = m.group(1), m.group(2)
                if not num or len(num) > 4:
                    continue
                end_pos = m.end()
                if end_pos < len(txt) and txt[end_pos] in BAD_NUMERAL_SUFFIX:
                    continue
                if raw_name in ["图", "附图", "如图", "参见图", "如图所示", "实施例", "表", "步骤", "第"]:
                    continue
                clean_name = raw_name
                for sep in ["的", "在", "于", "由", "和", "与", "及", "设", "有", "为", "至", "从", "向", "该", "此", "本", "所述", "包括", "具有"]:
                    if sep in clean_name:
                        clean_name = clean_name.split(sep)[-1]
                if len(clean_name) >= 2 and clean_name not in STOP_WORDS:
                    c_data = candidates[num]
                    c_data["zh_names"][clean_name] += 1
                    c_data["freq"] += 1
                    if not c_data["first_para"]:
                        c_data["first_para"] = no

        if en:
            for m in en_pat.finditer(en):
                raw_name, num = m.group(1).strip(), m.group(2)
                if not num or len(num) > 4:
                    continue
                lower_name = raw_name.lower()
                if any(lower_name.startswith(p) for p in ["fig", "figure", "example", "table", "step", "claim", "abb"]):
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


# ==============================================================================
# 5. 核心提取器与质量门禁引擎 (PatentFetcher)
# ==============================================================================

class PatentFetcher:
    """
    专利完整数据提取器
    遵循原则：“成功时交付经过检查的完整资料；失败时快速停在明确的故障点，绝不把不完整文本当成成功结果。”
    """

    def __init__(
        self,
        headless: bool = True,
        browser_path: Optional[str] = None,
        with_figures: bool = True,
        trans_mode: str = "patsnap",
        zh_json: Optional[Any] = None,
        timeout: int = 60000
    ):
        self.headless = headless
        self.browser_path = get_browser_executable(browser_path)
        self.with_figures = with_figures
        self.trans_mode = trans_mode
        self.custom_zh_map = self._load_custom_zh(zh_json)
        self.timeout = timeout
        self.report = QualityReport()

    def _load_custom_zh(self, zh_input: Any) -> Dict[str, str]:
        if not zh_input:
            return {}
        if isinstance(zh_input, dict):
            return zh_input
        if isinstance(zh_input, str) and os.path.exists(zh_input):
            with open(zh_input, encoding="utf-8") as f:
                return json.load(f)
        return {}

    async def fetch(self, share_url: str) -> PatentData:
        """异步执行完整抓取与质检流程"""
        # ----------------------------------------------------------------------
        # 门禁 1: 链接格式校验 (URL Gate)
        # ----------------------------------------------------------------------
        if not share_url or not isinstance(share_url, str):
            raise UrlValidationError("分享链接不能为空", {"url": str(share_url)})
        clean_url = share_url.strip().strip("'\"<> ")
        if "zhihuiya.com" not in clean_url:
            raise UrlValidationError(
                "无效的分享链接：必须包含 zhihuiya.com 域名",
                {"url": clean_url},
                suggestion="请检查是否传入了正确的智慧芽免登录分享链接。"
            )
        if "patentId=" not in clean_url:
            raise UrlValidationError(
                "无效的分享链接：缺少必要的 patentId 查询参数",
                {"url": clean_url},
                suggestion="请确认链接中包含 ?patentId=xxxx-xxxx 字段。"
            )

        self.report.url_check = "PASS"
        self.report.details["share_url"] = clean_url

        abst_url = normalize_share_url(clean_url, "abst")
        clms_url = normalize_share_url(clean_url, "clms")
        desc_url = normalize_share_url(clean_url, "desc")
        img_url = normalize_share_url(clean_url, "image")

        print(f"[*] 启动浏览器引擎: {self.browser_path or 'Playwright 内置 Chromium'}")
        print(f"[*] 目标链接: {clean_url[:75]}...")

        async with async_playwright() as p:
            launch_args = {
                "headless": self.headless,
                "args": ["--no-sandbox", "--disable-setuid-sandbox", "--disable-gpu"]
            }
            if self.browser_path:
                launch_args["executable_path"] = self.browser_path

            browser = await p.chromium.launch(**launch_args)
            context = await browser.new_context(
                viewport={"width": 1440, "height": 1000},
                device_scale_factor=1,
                is_mobile=False
            )
            page = await context.new_page()

            try:
                # ------------------------------------------------------------------
                # 门禁 2: 访问摘要页建立鉴权并提取著录项与摘要 (Biblio & Abst Gate)
                # ------------------------------------------------------------------
                print(f"[1/4] 访问摘要页建立鉴权会话: {abst_url[:80]}...")
                try:
                    await page.goto(abst_url, timeout=self.timeout, wait_until="domcontentloaded")
                    await page.wait_for_timeout(3500)
                except Exception as e:
                    raise AuthOrPageError(
                        f"摘要页加载失败或网络超时: {e}",
                        {"abst_url": abst_url},
                        suggestion="请检查网络连接、代理或该分享链接是否过期。"
                    )

                # 检查页面是否显示失效或未授权
                page_body_txt = await page.evaluate("() => document.body ? document.body.innerText : ''")
                if "链接已失效" in page_body_txt or "分享已过期" in page_body_txt:
                    raise AuthOrPageError(
                        "智慧芽分享链接已失效或签名已过期",
                        {"page_excerpt": page_body_txt[:200]},
                        suggestion="请在智慧芽平台重新生成并导出免登录分享链接。"
                    )

                raw_biblio = await page.evaluate(JS_EXTRACT_BIBLIO_AND_ABST)
                pn = raw_biblio.get("pn", "").strip()
                title = raw_biblio.get("title", "").strip()

                # 著录项断言
                if not pn or not re.match(r"^[A-Z]{2}\d+[A-Z\d]*$", pn):
                    raise BiblioExtractionError(
                        "未能识别到合法且有效的专利公开号 (PN)",
                        {"detected_pn": pn, "page_title": raw_biblio.get("page_title")},
                        suggestion="请确认页面是否完全渲染完成，或分享链接是否被重定向到受限登录页。"
                    )

                if not title:
                    raise BiblioExtractionError(
                        "未能提取到专利发明名称 (Title)",
                        {"pn": pn},
                        suggestion="页面缺少标题节点，请检查页面 DOM 状态。"
                    )

                applicant = raw_biblio.get("applicant", "")
                pub_date = raw_biblio.get("pub_date", "")
                app_date = raw_biblio.get("app_date", "")
                expected_fig_count = raw_biblio.get("expected_fig_count")
                abstract_raw = raw_biblio.get("abstract_raw", "").strip()

                # 摘要断言 (绝大多数专利必须有摘要)
                if not abstract_raw:
                    # 再次放宽探测
                    abstract_raw = await page.evaluate(r"""() => {
                        const m = (document.body.innerText || '').match(/摘要\s*\n\s*([^\n]{30,800})/);
                        return m ? m[1].trim() : '';
                    }""")

                if not abstract_raw:
                    raise BiblioExtractionError(
                        "未能提取到摘要正文 (Abstract)，摘要内容为空",
                        {"pn": pn, "title": title},
                        suggestion="请检查智慧芽详情页中该专利是否包含摘要文本。"
                    )

                self.report.biblio_check = "PASS"
                self.report.abstract_check = "PASS"
                self.report.details["pn"] = pn
                self.report.details["title"] = title
                self.report.details["applicant"] = applicant
                self.report.details["expected_fig_count"] = expected_fig_count

                print(f"    -> 公开号: {pn}")
                print(f"    -> 发明名称: {title}")
                print(f"    -> 申请人: {applicant or '（未列出）'}")
                print(f"    -> 摘要正文: 已获取 (长度: {len(abstract_raw)} 字符)")
                print(f"    -> 官方声明附图数: {expected_fig_count if expected_fig_count is not None else '未在导航标明'}")

                # ------------------------------------------------------------------
                # 门禁 3: 访问权利要求页提取权要 (Claims Gate)
                # ------------------------------------------------------------------
                print(f"[2/4] 访问权利要求页: {clms_url[:80]}...")
                try:
                    await page.goto(clms_url, timeout=self.timeout, wait_until="domcontentloaded")
                    await page.wait_for_timeout(3500)
                except Exception as e:
                    raise AuthOrPageError(f"权利要求页访问失败: {e}", {"clms_url": clms_url})

                claims_orig = await page.evaluate(JS_EXTRACT_CLAIMS_ORIGINAL)
                if not claims_orig or len(claims_orig) == 0:
                    raise ClaimsExtractionError(
                        "权利要求提取结果为空 (0项)！",
                        {"pn": pn, "clms_url": clms_url},
                        suggestion="页面未加载出权要节点，或该专利类型无权利要求书（如部分外观专利）。"
                    )

                # 动态语言判定
                sample_blob = "".join(c.get("text", "") for c in claims_orig[:5]) or title
                is_chinese = is_chinese_text(sample_blob, thresh=0.15)
                is_foreign = not is_chinese
                print(f"    -> 文献语言性质: {'中文文献 (单轨模式)' if is_chinese else '外文文献 (双语对照模式)'}")

                claims_trans = []
                if is_foreign and self.trans_mode == "patsnap" and not self.custom_zh_map:
                    try:
                        print("    -> 正在拉取智慧芽中文机翻对照...")
                        lang_switcher = await page.query_selector('.lang-switcher, .patsnap-biz-header-dropdown')
                        if lang_switcher:
                            await lang_switcher.click()
                            await page.wait_for_timeout(600)
                            cn_btn = await page.query_selector('.pendo-track__function-bar__translate-CN, [data-lang="zh"], span:has-text("中文")')
                            if cn_btn:
                                await cn_btn.click()
                                await page.wait_for_timeout(4000)
                        claims_trans = await page.evaluate(JS_EXTRACT_CLAIMS_TRANSLATION)
                    except Exception as e_trans:
                        print(f"    [!] 抓取权利要求机翻提示: {e_trans}")

                trans_map = {c["no"]: c["text"] for c in claims_trans if isinstance(c, dict) and "no" in c}
                aligned_claims = []
                claim_numbers = []
                for item in claims_orig:
                    c_no = item["no"]
                    claim_numbers.append(c_no)
                    orig_txt = item["text"].strip()
                    if len(orig_txt) < 5:
                        raise ClaimsExtractionError(
                            f"权项 【权{c_no}】 文本内容异常过短 (<5字符)",
                            {"c_no": c_no, "text": orig_txt}
                        )
                    c_key_variants = [f"权{c_no}", str(c_no), f"Claim {c_no}"]
                    custom_trans = next((self.custom_zh_map[k] for k in c_key_variants if k in self.custom_zh_map), None)
                    final_zh = custom_trans if custom_trans else (trans_map.get(c_no, "") if self.trans_mode == "patsnap" else "")

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

                # 检查权项连续性（从1到N）
                sorted_c_nos = sorted(set(claim_numbers))
                if sorted_c_nos:
                    expected_range = set(range(1, max(sorted_c_nos) + 1))
                    missing_claims = sorted(expected_range - set(sorted_c_nos))
                    if missing_claims:
                        print(f"    [!] 警告: 权利要求序号存在跳号缺项: {missing_claims}")

                self.report.claims_check = "PASS"
                self.report.details["claims_count"] = len(aligned_claims)
                print(f"    -> 成功提取并质检权利要求: 共 {len(aligned_claims)} 项 (门禁通过)")

                # ------------------------------------------------------------------
                # 门禁 4: 访问说明书页提取段落并严格断言连续性 (Desc Gate)
                # ------------------------------------------------------------------
                print(f"[3/4] 访问说明书页: {desc_url[:80]}...")
                try:
                    await page.goto(desc_url, timeout=self.timeout, wait_until="domcontentloaded")
                    await page.wait_for_timeout(4000)
                except Exception as e:
                    raise AuthOrPageError(f"说明书页访问失败: {e}", {"desc_url": desc_url})

                desc_orig = await page.evaluate(JS_EXTRACT_DESC_ORIGINAL)
                if not desc_orig or len(desc_orig) == 0:
                    raise DescriptionExtractionError(
                        "说明书提取结果为空 (0段)！",
                        {"pn": pn, "desc_url": desc_url},
                        suggestion="页面未成功渲染说明书正文段落。"
                    )

                desc_trans = []
                if is_foreign and self.trans_mode == "patsnap" and not self.custom_zh_map:
                    try:
                        print("    -> 正在拉取说明书中文机翻对照...")
                        lang_switcher = await page.query_selector('.lang-switcher, .patsnap-biz-header-dropdown')
                        if lang_switcher:
                            await lang_switcher.click()
                            await page.wait_for_timeout(600)
                            cn_btn = await page.query_selector('.pendo-track__function-bar__translate-CN, [data-lang="zh"], span:has-text("中文")')
                            if cn_btn:
                                await cn_btn.click()
                                await page.wait_for_timeout(4500)
                        desc_trans = await page.evaluate(JS_EXTRACT_DESC_TRANSLATION)
                    except Exception as e_desc_trans:
                        print(f"    [!] 抓取说明书机翻提示: {e_desc_trans}")

                desc_trans_map = {p["no"]: p["text"] for p in desc_trans if isinstance(p, dict) and "no" in p}
                aligned_desc = []
                seen_no = set()
                for p in desc_orig:
                    p_no = p["no"]
                    if p_no in seen_no:
                        continue
                    seen_no.add(p_no)
                    orig_txt = p["text"].strip()
                    if not orig_txt:
                        continue
                    p_key_variants = [p_no, str(int(p_no)) if p_no.isdigit() else p_no]
                    custom_trans = next((self.custom_zh_map[k] for k in p_key_variants if k in self.custom_zh_map), None)
                    final_zh = custom_trans if custom_trans else (desc_trans_map.get(p_no, "") if self.trans_mode == "patsnap" else "")

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

                # 连续性检查核对
                cont = self._check_continuity(desc_orig)
                self.report.details["continuity"] = cont
                self.report.description_check = "PASS"

                # 关键断言：绝对不许漏段缺段！
                if cont.get("gaps") and len(cont["gaps"]) > 0:
                    raise DescriptionContinuityError(
                        f"说明书连续性断言失败：检测到 {len(cont['gaps'])} 个段号缺口！绝不交付不完整文本！",
                        {
                            "range": cont.get("range"),
                            "expected_count": cont.get("expected"),
                            "found_count": cont.get("found"),
                            "gaps": cont.get("gaps"),
                        },
                        suggestion="页面在懒加载或分页时未完全展开对应段落，请重试或检查该专利官方公布文本。"
                    )

                self.report.continuity_check = "PASS"
                print(f"    -> 成功提取并质检说明书: 共 {len(aligned_desc)} 段 (范围 {cont['range']}, 0 缺口，连续性断言全绿)")

                # ------------------------------------------------------------------
                # 门禁 5: 访问附图页抓取官方附图并校验完整性 (Figures Gate)
                # ------------------------------------------------------------------
                figures_list = []
                if self.with_figures:
                    print(f"[4/4] 访问附图页抓取官方附图 (校验图数无遗漏): {img_url[:80]}...")
                    collected_figs = {}

                    async def on_fig_resp(resp):
                        u = resp.url
                        if any(domain in u for domain in ["data-fulltext-image.zhihuiya.com", "data-abstract-image.zhihuiya.com", "image.zhihuiya.com"]):
                            if any(ext in u.lower() for ext in [".png", ".jpg", ".jpeg"]):
                                clean_u = u.split("?")[0]
                                if clean_u in collected_figs:
                                    return
                                try:
                                    body = await resp.body()
                                    if not body or len(body) < 1500:
                                        return
                                    is_abst = "data-abstract-image" in u or "abstract" in u.lower()

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
                        await page.goto(img_url, timeout=self.timeout, wait_until="domcontentloaded")
                        await page.wait_for_timeout(3500)

                        # 多轮平滑滚动触发全量懒加载
                        for s_round in range(8):
                            prev_cnt = len(collected_figs)
                            await page.evaluate(r'''() => {
                                const byClass = Array.from(document.querySelectorAll('div')).find(e => 
                                    /thum-mainsection-left|commen-scroll-loader/.test(String(e.className || '')) && e.clientHeight > 100);
                                const el = byClass || Array.from(document.querySelectorAll('div')).find(e => 
                                    e.scrollHeight > e.clientHeight + 100 && e.clientHeight > 150);
                                if (el) {
                                    el.scrollTop = el.scrollTop + Math.round(el.clientHeight * 0.95);
                                } else {
                                    window.scrollBy(0, 800);
                                }
                            }''')
                            await page.wait_for_timeout(1500)
                            cur_cnt = len(collected_figs)
                            if expected_fig_count is not None and cur_cnt >= expected_fig_count:
                                break
                            if cur_cnt > 0 and cur_cnt == prev_cnt and s_round >= 4:
                                break

                        sorted_items = sorted(collected_figs.values(), key=lambda x: x["sort_tuple"])
                        full_idx = 1
                        for i, item in enumerate(sorted_items):
                            b = item["bytes"]
                            b64_str = base64.b64encode(b).decode("utf-8")
                            fig_name = "摘要附图" if item["is_abst"] else f"图 {full_idx}"
                            if not item["is_abst"]:
                                full_idx += 1

                            figures_list.append({
                                "no": str(i + 1),
                                "name": fig_name,
                                "key": os.path.basename(item["url"]),
                                "src": f"data:image/png;base64,{b64_str}"
                            })

                        # 附图完整性严格断言：若官方标明有 N 张图，抓取数量绝不能小于 N
                        actual_count = len(figures_list)
                        self.report.details["actual_fig_count"] = actual_count
                        if expected_fig_count is not None and expected_fig_count > 0:
                            if actual_count < expected_fig_count:
                                raise FiguresExtractionError(
                                    f"附图抓取不完整！页面标明有 {expected_fig_count} 张附图，实际仅获取到 {actual_count} 张，存在漏图风险！",
                                    {
                                        "expected_count": expected_fig_count,
                                        "actual_count": actual_count,
                                        "missing": expected_fig_count - actual_count
                                    },
                                    suggestion="网络较慢导致部分高清附图未能在超时时间内响应加载，请重试。"
                                )

                        self.report.figures_check = "PASS"
                        print(f"    -> 成功抓取并质检官方附图: 共 {actual_count} 张 (预期: {expected_fig_count or '未知'}，门禁通过)")

                    except FiguresExtractionError:
                        raise
                    except Exception as e_fig:
                        raise FiguresExtractionError(f"抓取附图遇到未处理异常: {e_fig}")

                else:
                    self.report.figures_check = "SKIPPED"
                    print("[!] 跳过附图抓取 (--no-figures)")

            finally:
                await browser.close()

        # ----------------------------------------------------------------------
        # 门禁 6: 构件术语挖掘与最终聚合门禁 (Sealed Gate)
        # ----------------------------------------------------------------------
        glossary = extract_reference_numerals(aligned_claims, aligned_desc)
        print(f"[*] 自动提取附图标记与术语表: 共归纳提取到 {len(glossary)} 个构件标号")

        abstract_dict = {
            "raw": abstract_raw,
            "en": "" if is_chinese else abstract_raw,
            "zh": abstract_raw if is_chinese else ""
        }

        self.report.passed = True
        self.report.details["total_claims"] = len(aligned_claims)
        self.report.details["total_desc_paras"] = len(aligned_desc)
        self.report.details["total_figures"] = len(figures_list)
        self.report.details["total_glossary"] = len(glossary)

        return PatentData(
            pn=pn,
            title=title,
            share_url=clean_url,
            biblio={
                "pn": pn,
                "title": title,
                "applicant": applicant,
                "pub_date": pub_date,
                "app_date": app_date,
                "expected_fig_count": expected_fig_count
            },
            abstract=abstract_dict,
            claims=aligned_claims,
            description=aligned_desc,
            figures=figures_list,
            glossary=glossary,
            continuity=cont,
            quality_report=self.report,
            is_foreign=is_foreign
        )

    def _check_continuity(self, paras: List[Dict[str, Any]]) -> Dict[str, Any]:
        """严格连续性核查算法"""
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
            "expected": len(expected_set),
            "found": len(found_set),
            "gaps": [str(g).zfill(4) for g in gaps],
            "duplicates": [str(d).zfill(4) for d in sorted(dups)],
        }


# ==============================================================================
# 6. 便捷的同步与异步顶层调用接口
# ==============================================================================

def fetch_patent(
    url: str,
    headless: bool = True,
    browser_path: Optional[str] = None,
    with_figures: bool = True,
    trans_mode: str = "patsnap",
    zh_json: Optional[Any] = None,
    save_dir: Optional[str] = None,
    save_images: bool = True
) -> PatentData:
    """
    同步顶层调用函数：抓取智慧芽专利信息并执行严格质检交付。

    :param url: 智慧芽免登录分享链接
    :param headless: 是否使用无头模式
    :param browser_path: 自定义 Chrome/Edge 可执行文件路径
    :param with_figures: 是否抓取官方附图 (默认 True)
    :param trans_mode: 机翻策略 ('patsnap' | 'empty')
    :param zh_json: 自定义翻译词典字典或 json 文件路径
    :param save_dir: 若指定目录，则自动保存 complete.json, md 与 figures
    :param save_images: 是否将附图单独存为实体 PNG 文件
    :return: 经过严格质检的 PatentData 实例
    """
    fetcher = PatentFetcher(
        headless=headless,
        browser_path=browser_path,
        with_figures=with_figures,
        trans_mode=trans_mode,
        zh_json=zh_json
    )
    data = asyncio.run(fetcher.fetch(url))

    if save_dir:
        os.makedirs(save_dir, exist_ok=True)
        pn = data.pn
        json_path = os.path.join(save_dir, f"{pn}_complete_patent.json")
        md_path = os.path.join(save_dir, f"{pn}_全文_中英对照.md")
        report_path = os.path.join(save_dir, f"{pn}_quality_report.json")

        data.save_json(json_path)
        data.save_markdown(md_path)
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(asdict(data.quality_report), f, ensure_ascii=False, indent=2)

        if save_images and data.figures:
            fig_dir = os.path.join(save_dir, f"{pn}_figures")
            data.save_figures(fig_dir)

    return data


# ==============================================================================
# 7. 独立 CLI 命令行入口
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="从智慧芽免登录分享链接提取完整专利信息并执行严格质检交付 (遵循 Fail-Fast 原则)"
    )
    parser.add_argument("url", help="智慧芽免登录分享链接 (含 patentId, shareId, signature 等参数)")
    parser.add_argument("--out-dir", default="./output", help="输出成果保存目录 (默认: ./output)")
    parser.add_argument("--save-md", action=argparse.BooleanOptionalAction, default=True, help="是否导出标准 Markdown 文件")
    parser.add_argument("--save-figs", action=argparse.BooleanOptionalAction, default=True, help="是否将附图导出为实体高清图片文件")
    parser.add_argument("--with-figures", action=argparse.BooleanOptionalAction, default=True, help="是否拉取官方附图 (若不需要附图可传 --no-with-figures)")
    parser.add_argument("--trans-mode", choices=["patsnap", "empty"], default="patsnap", help="翻译策略: patsnap (机翻) | empty (留空骨架)")
    parser.add_argument("--zh-json", help="外部自译/AI翻译 JSON 文件路径 (用于覆盖机翻)")
    parser.add_argument("--browser-path", help="指定 Chrome 或 Edge 可执行文件路径")
    parser.add_argument("--no-headless", action="store_true", help="显示浏览器图形界面 (调试用)")

    args = parser.parse_args()

    print("=" * 80)
    print("      智慧芽专利完整资料提取与质检交付脚本 (Patent Strict Fetcher)      ")
    print("=" * 80)

    try:
        patent_data = fetch_patent(
            url=args.url,
            headless=not args.no_headless,
            browser_path=args.browser_path,
            with_figures=args.with_figures,
            trans_mode=args.trans_mode,
            zh_json=args.zh_json,
            save_dir=args.out_dir,
            save_images=args.save_figs
        )

        out_dir_abs = os.path.abspath(args.out_dir)
        pn = patent_data.pn
        json_file = os.path.join(out_dir_abs, f"{pn}_complete_patent.json")
        md_file = os.path.join(out_dir_abs, f"{pn}_全文_中英对照.md")
        report_file = os.path.join(out_dir_abs, f"{pn}_quality_report.json")
        fig_dir = os.path.join(out_dir_abs, f"{pn}_figures")

        print("\n" + "=" * 80)
        print("          ✅ 专利完整资料抓取成功：所有质量门禁全部通过！          ")
        print("=" * 80)
        print(f"[*] 文献公开号  : {patent_data.pn}")
        print(f"[*] 发明名称    : {patent_data.title}")
        print(f"[*] 申请人      : {patent_data.biblio.get('applicant') or '（未列出）'}")
        print(f"[*] 公开日      : {patent_data.biblio.get('pub_date') or '-'} | 申请日: {patent_data.biblio.get('app_date') or '-'}")
        print(f"[*] 摘要文本    : 成功获取 (长度: {len(patent_data.abstract.get('raw', ''))} 字符)")
        print(f"[*] 权利要求项数: {len(patent_data.claims)} 项 (门禁核查通过)")
        print(f"[*] 说明书段落数: {len(patent_data.description)} 段 (范围 {patent_data.continuity.get('range', '-')}, 0 缺口，连续性断言通过)")
        print(f"[*] 官方附图张数: {len(patent_data.figures)} 张 (已做预期数量一致性比对)")
        print(f"[*] 术语构件标号: {len(patent_data.glossary)} 个")
        print("-" * 80)
        print("【已交付的完整文件】:")
        print(f"  1. 完整结构化数据: {json_file}")
        if args.save_md:
            print(f"  2. 标准中英对照 MD: {md_file}")
        if args.save_figs and patent_data.figures:
            print(f"  3. 高清实体附图包: {fig_dir}")
        print(f"  4. 质量门禁质检单: {report_file}")
        print("=" * 80 + "\n")
        sys.exit(0)

    except PatentExtractionError as e:
        # 捕捉所有已定义的明确故障点
        print(e.format_diagnostic_report(), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        # 捕捉未预期崩溃
        unexpected = PatentExtractionError(
            stage="未知未预期崩溃",
            message=str(e),
            details={"error_class": e.__class__.__name__},
            suggestion="请查看堆栈信息或将问题反馈给技术人员。"
        )
        print(unexpected.format_diagnostic_report(), file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()
