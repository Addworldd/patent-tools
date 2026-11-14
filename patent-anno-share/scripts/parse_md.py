# -*- coding: utf-8 -*-
"""解析「中英对照」md → data.json（供可批注页面使用）。

输入 md 结构（段号四位）：
    ## G3 · 系列一            <- 可选分组标题
    ### 【0054】

    > 英文原文（多行以 "> " 开头）

    　　**译**：中文译文

切分策略：先按 `### 【dddd】` 把所有段切开，再在每段内部提取英文块与译块。
这样不会因为正则贪婪而把多段吞成一段。

`==…==` 会被解析成预置高亮区间（enMarks / zhMarks），纯文本另存。

用法：
    python parse_md.py <md路径> --out data.json [--title "标题"] [--doc-id ID]
"""
import argparse
import json
import os
import re
import sys

HEAD_RE = re.compile(r"^###\s*【([^】]+)】\s*$", re.M)
GROUP_RE = re.compile(r"^##\s+(.+?)\s*$", re.M)


def split_marks(text):
    """把含 ==…== 的文本拆成 (纯文本, [(s,e)...])，区间基于纯文本偏移。"""
    if text is None:
        return "", []
    parts = text.split("==")
    plain_parts, ranges = [], []
    pos = 0
    for idx, seg in enumerate(parts):
        plain_parts.append(seg)
        if idx % 2 == 1:
            ranges.append((pos, pos + len(seg)))
        pos += len(seg)
    return "".join(plain_parts), ranges


def clean_en(block):
    """把英文块（多行 '> '）还原为文本，保留原有换行。"""
    lines = []
    for ln in block.split("\n"):
        if ln.startswith("> "):
            lines.append(ln[2:])
        elif ln.rstrip() == ">":
            lines.append("")
        elif ln.startswith(">"):
            lines.append(ln[1:])
        else:
            lines.append(ln)
    return "\n".join(lines).strip("\n")


def parse_body(body):
    """在一段内部提取英文与中文。返回 (en_raw, zh_raw)。"""
    # 中文：第一个 "　　**译**：" 或 "**译**：" 之后到段末
    m = re.search(r"　*\*\*译\*\*[：:]\s*", body)
    if m:
        en_part = body[:m.start()]
        zh_part = body[m.end():]
    else:
        en_part, zh_part = body, ""

    # 英文：所有 "> " 开头的连续行（取第一段连续块）
    en_lines = []
    started = False
    for ln in en_part.split("\n"):
        if ln.startswith(">") or (started and ln.strip() == ""):
            started = True
            en_lines.append(ln)
        elif started and ln.strip() and not ln.startswith(">"):
            break
    en_raw = clean_en("\n".join(en_lines)) if en_lines else ""

    # 中文：去掉可能的 "**注**" 等尾注行，只留第一段正文
    zh_lines = []
    for ln in zh_part.split("\n"):
        if ln.strip().startswith("**注**"):
            break
        zh_lines.append(ln)
    zh_raw = "\n".join(zh_lines).strip()
    return en_raw, zh_raw


def key_of(no, side, s, e):
    return f"{no}|{side}|{s}|{e}"


def parse(md_text):
    # 记录每个段号归属的分组
    group_spans = [(m.start(), m.group(1).strip()) for m in GROUP_RE.finditer(md_text)]
    heads = [(m.start(), m.group(1)) for m in HEAD_RE.finditer(md_text)]

    items, dup = [], []
    seen = set()
    for idx, (start, no) in enumerate(heads):
        end = heads[idx + 1][0] if idx + 1 < len(heads) else len(md_text)
        body = md_text[md_text.find("\n", start) + 1: end]

        cur_group = ""
        for gpos, gname in group_spans:
            if gpos < start:
                cur_group = gname
            else:
                break

        if no in seen:
            dup.append(no)
            continue
        seen.add(no)

        en_raw, zh_raw = parse_body(body)
        rec = {"no": no, "group": cur_group}
        for side, src in (("en", en_raw), ("zh", zh_raw)):
            plain, ranges = split_marks(src)
            rec[side] = plain
            rec[side + "Marks"] = [
                {"s": s, "e": e, "key": key_of(no, side, s, e),
                 "text": plain[s:e], "note": ""}
                for s, e in ranges
            ]
        items.append(rec)

    # 保留文档中的先后顺序（权利要求与说明书段落自然排列）
    stats = {
        "paragraphs": len(items),
        "duplicates": dup,
        "marks_en": sum(len(i["enMarks"]) for i in items),
        "marks_zh": sum(len(i["zhMarks"]) for i in items),
        "empty_en": [i["no"] for i in items if not i["en"].strip()],
        "empty_zh": [i["no"] for i in items if not i["zh"].strip()],
    }
    return items, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("md")
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="")
    ap.add_argument("--subtitle", default="")
    ap.add_argument("--doc-id", default="")
    a = ap.parse_args()

    if not os.path.exists(a.md):
        print(json.dumps({"ok": False, "error": f"文件不存在: {a.md}"}, ensure_ascii=False))
        sys.exit(1)

    md = open(a.md, encoding="utf-8").read()
    items, stats = parse(md)

    title = a.title
    if not title:
        m = re.search(r"^#\s+(.+)$", md, re.M)
        title = m.group(1).strip() if m else os.path.basename(a.md)

    data = {
        "title": title,
        "subtitle": a.subtitle or "划选文字 → 添加高亮 → 写备注。批注自动同步到云端，多人可同时标注。",
        "docId": a.doc_id or os.path.splitext(os.path.basename(a.md))[0],
        "count": len(items),
        "items": items,
    }

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    stats.update({"ok": True, "out": a.out, "title": title})
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
