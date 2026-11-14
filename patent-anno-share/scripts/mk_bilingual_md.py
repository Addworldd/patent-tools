# -*- coding: utf-8 -*-
"""从智慧芽取回的说明书 JSON 直接生成「中英对照 md」——供本 skill 后续做可批注 HTML。

**两条硬规矩（写死在流程里，不是建议）**
  1. 译文必须**自己译**。智慧芽/浏览器自带的机翻只能当看懂大意的辅助，一个字都不许进 md。
     `--zh` 只接受自译稿；脚本会检查每段汉字占比，没译干净的段进 `zh_suspect`。
  2. 段落**必须核对，不能漏段缺段**。脚本输出 `continuity`（区间/应有/实有/缺口/重复），
     有缺口或重复时**退出码 2 中止**，必须补段后重跑。

支持三种输入形态（自动识别）：

A) 译文 JSON + 原文 JSON
   原文：{"items":[{pn, paragraphs:[{no,text}]}]}  或  {"paragraphs":[{no,text}]}
   译文：{"0001":"中文…", "0002":"中文…"}  （按段号为键，**自译**）

B) 单文件英文 JSON（不含译文）
   生成「仅原文 +（待补）」骨架。先拿它看 continuity，段落齐了再动手译。
   这是推荐顺序：不要拿一份缺段的稿子去翻译。

C) 已有中英对照 md（本脚本不处理，直接用 check_md.py → parse_md.py）

典型用法：
  # 第 1 遍：只出骨架，顺便核对段落
  python mk_bilingual_md.py --en en.json --pn US20160183596A1 --out out.md

  # 第 2 遍：合并自译稿
  python mk_bilingual_md.py --en en.json --zh zh_self.json --pn US20160183596A1 --out out.md

  # 指定分组（可选，按段号范围切分）
  python mk_bilingual_md.py --en en.json --zh zh.json --pn X --groups groups.json --out out.md
"""
import argparse
import json
import os
import re
import sys

# 智慧芽英文段末常粘着的小节标题（DOM 残留），单独列出便于提示，不删除
SECTION_TAILS = ["BACKGROUND", "SUMMARY", "BRIEF DESCRIPTION OF THE DRAWINGS",
                 "DESCRIPTION", "DETAILED DESCRIPTION", "CROSS REFERENCE TO RELATED APPLICATIONS",
                 "BACKGROUND ART", "TECHNICAL FIELD", "INDUSTRIAL APPLICABILITY"]


def is_chinese(s, thresh=0.15):
    """判断文本是否以中文为主。"""
    if not s:
        return False
    cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    return cjk / max(1, len(s)) > thresh


def load_en(path, pn=None):
    """读英文原文，返回 [(no, text)]。

    智慧芽导出的 paragraphs 常见两种情况：
      1) 只有英文
      2) 英中混排、段号重复 —— 前一半英文、后一半机翻中文
    本函数会剔除中文段（同段号优先保留英文侧）。
    """
    d = json.load(open(path, encoding="utf-8"))
    if isinstance(d, dict) and "items" in d:
        items = d["items"]
        if pn:
            hit = [x for x in items if str(x.get("pn", "")).upper() == pn.upper()]
            if not hit:
                hit = [x for x in items if pn.upper() in str(x.get("pn", "")).upper()]
            if not hit:
                raise SystemExit(f"在 {path} 中找不到 pn={pn}；可用：" +
                                 ", ".join(str(x.get("pn")) for x in items))
            item = hit[0]
        else:
            item = items[0]
        paras = item["paragraphs"]
        title = item.get("title", "")
        pn_actual = item.get("pn", pn or "")
    elif isinstance(d, dict) and "paragraphs" in d:
        paras = d["paragraphs"]
        title = d.get("title", "")
        pn_actual = d.get("pn", pn or "")
    elif isinstance(d, list):
        paras = d
        title, pn_actual = "", pn or ""
    else:
        raise SystemExit("英文 JSON 结构无法识别")

    # 先按段号分桶，只保留「非中文」的候选；同段号有多条时取第一条英文
    bucket = {}
    dups = []
    dropped_cn = 0
    for p in paras:
        no = str(p.get("no", "")).strip()
        if not re.fullmatch(r"\d{1,4}", no):
            continue
        txt = p.get("text", "")
        if "【翻译】" in txt:
            head, _, _tail = txt.partition("【翻译】")
            # 【翻译】之前几乎没内容 → 这段纯粹是机翻容器，整段丢弃。
            # （否则会留下 "[0060] " 这种空壳，把真正的英文段挤成"重复段号"）
            if len(re.sub(r"[\[\]\d\s]", "", head)) < 12:
                dropped_cn += 1
                continue
            txt = head
        txt = txt.rstrip()
        if not txt.strip():
            continue
        if is_chinese(txt):
            dropped_cn += 1
            continue
        no = no.zfill(4)
        if no not in bucket:            # 保留首次出现的英文段
            bucket[no] = txt
        elif bucket[no] != txt:
            dups.append(no)             # 同段号两份不同英文 → 真重复，必须报

    out = sorted(bucket.items(), key=lambda kv: kv[0])
    return out, title, pn_actual, dropped_cn, sorted(set(dups))


def check_continuity(paras, expect=None):
    """段落连续性核对——外文文献不许漏段缺段。

    返回 {range, expected, found, gaps, duplicates_external}；
    gaps 非空即代表原文区间内有段没取到，必须补。
    """
    nos = [int(no) for no, _ in paras]
    if not nos:
        return {"range": "", "expected": 0, "found": 0, "gaps": ["ALL"]}
    lo, hi = min(nos), max(nos)
    have = set(nos)
    gaps = ["%04d" % n for n in range(lo, hi + 1) if n not in have]
    return {
        "range": "%04d-%04d" % (lo, hi),
        "expected": expect if expect else hi - lo + 1,
        "found": len(have),
        "gaps": gaps,
    }


def load_zh(path):
    """读译文，返回 {no: 中文}。"""
    if not path:
        return {}
    d = json.load(open(path, encoding="utf-8"))
    if isinstance(d, dict):
        # 可能是 {"items":...} 包装
        if "items" in d and isinstance(d["items"], dict):
            d = d["items"]
        return {str(k).zfill(4): str(v).strip() for k, v in d.items()}
    if isinstance(d, list):
        out = {}
        for p in d:
            no = str(p.get("no", "")).zfill(4)
            out[no] = str(p.get("text", "")).strip()
        return out
    raise SystemExit("译文 JSON 结构无法识别")


def detect_tail(text):
    """检测段末是否粘着小节标题，返回 (clean_text, tail)。"""
    for t in SECTION_TAILS:
        if text.rstrip().endswith(t):
            return text[: text.rfind(t)].rstrip(), t
    return text, ""


def build_md(paras, zh_map, pn, title, groups=None, keep_tail=True):
    lines = []
    head = f"# {pn} — 说明书（中英对照）"
    if title:
        head += f" · {title}"
    lines += [head, "",
              f"**专利号** {pn}　|　**段数** {len(paras)}　|　"
              f"**原文** 逐字照抄　|　**译文** "
              + ("自译（非机翻）" if zh_map else "待补"),
              "",
              "> 本文件由 `mk_bilingual_md.py` 从智慧芽取数 JSON 生成："
              "原文逐字照抄英文侧，译文为自译稿。"
              "供 `check_md.py` 核对段落 → `parse_md.py` → `build_single.py` 制作可批注 HTML。",
              ""]

    # 分组
    def group_of(no):
        if not groups:
            return ""
        n = int(no)
        for g in groups:
            lo, hi = int(g["from"]), int(g["to"])
            if lo <= n <= hi:
                return g.get("name", f"G{g['from']}-{g['to']}")
        return ""

    cur_g = None
    torn = []
    for no, en_raw in paras:
        g = group_of(no)
        if g and g != cur_g:
            cur_g = g
            lines += ["", f"## {g}", ""]

        en, tail = detect_tail(en_raw)
        if tail:
            torn.append((no, tail))
            if not keep_tail:
                en_raw = en

        lines.append(f"### 【{no}】")
        lines.append("")
        # 英文：多行时每行加 "> "
        for ln in en_raw.split("\n"):
            lines.append("> " + ln if ln.strip() else ">")
        lines.append("")
        zh = zh_map.get(no, "")
        if zh:
            lines.append(f"　　**译**：{zh}")
        else:
            lines.append("　　**译**：（待补）")
        lines.append("")

    if torn:
        lines += ["---", "",
                  "**提示：以下段末粘有说明书小节标题（智慧芽 DOM 残留），已照原文保留**：", ""]
        for no, t in torn:
            lines.append(f"- 【{no}】末尾 `{t}`")
        lines.append("")

    return "\n".join(lines), torn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--en", required=True, help="英文原文 JSON")
    ap.add_argument("--zh", default="",
                    help="【自译】译文 JSON（{段号:中文}）。智慧芽机翻稿不要塞进来；"
                         "不给则译文留「（待补）」骨架")
    ap.add_argument("--pn", default="", help="专利公开号，用于在 items 中定位")
    ap.add_argument("--out", required=True, help="输出 md 路径")
    ap.add_argument("--groups", default="", help="分组 JSON：[{from,to,name}]")
    ap.add_argument("--strip-tail", action="store_true",
                    help="删除段末粘着的小节标题（默认保留）")
    ap.add_argument("--expect", type=int, default=0,
                    help="原文应有段数；确知时填，用于核对 found 是否相等")
    ap.add_argument("--allow-gap", action="store_true",
                    help="明知原文本段号不连续时放行（仍会打印缺口）")
    a = ap.parse_args()

    if not os.path.exists(a.en):
        print(json.dumps({"ok": False, "error": f"文件不存在: {a.en}"}, ensure_ascii=False))
        sys.exit(1)

    paras, title, pn, dropped_cn, dups = load_en(a.en, a.pn or None)
    zh = load_zh(a.zh) if a.zh else {}
    groups = json.load(open(a.groups, encoding="utf-8")) if a.groups else None

    cont = check_continuity(paras, a.expect or None)
    cont["duplicates"] = dups

    md, torn = build_md(paras, zh, pn, title, groups, keep_tail=not a.strip_tail)

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(md)

    missing = [no for no, _ in paras if not zh.get(no)]
    # 译文汉字占比过低 = 没译干净（机翻直塞 / 只译了一半）
    suspect = [no for no, _ in paras
               if zh.get(no) and is_chinese(zh[no], 0.10) is False]

    report = {
        "ok": True,
        "out": a.out,
        "pn": pn,
        "paragraphs": len(paras),
        "translated": len(paras) - len(missing),
        "untranslated": len(missing),
        "first_untranslated": missing[:5],
        "zh_suspect": suspect[:10],
        "zh_suspect_count": len(suspect),
        "continuity": cont,
        "section_tails": [no for no, _ in torn],
        "dropped_chinese_paragraphs": dropped_cn,
        "size": os.path.getsize(a.out),
    }

    problems = []
    if cont["gaps"]:
        problems.append("段号缺口 %d 处：%s"
                        % (len(cont["gaps"]), ", ".join(cont["gaps"][:20])))
    if cont["found"] != cont["expected"]:
        problems.append("段数不符 expected %d / found %d"
                        % (cont["expected"], cont["found"]))
    if dups:
        problems.append("重复段号：%s" % ", ".join(dups[:20]))

    if problems:
        report["ok"] = False
        report["problems"] = problems
        if not a.allow_gap:
            report["hint"] = ("外文文献不许漏段缺段。补齐后重跑；"
                              "若原文本身段号不连续，加 --allow-gap 并在此说明理由。")
            print(json.dumps(report, ensure_ascii=False))
            sys.exit(2)
        report["note"] = "--allow-gap：已放行段落缺口，请确认这是原文本身不连续"

    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
