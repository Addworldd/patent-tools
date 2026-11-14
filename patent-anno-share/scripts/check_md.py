# -*- coding: utf-8 -*-
"""中英对照 md 的「段落核对闸」——漏段 / 缺段 / 重复 / 未译，一次全查。

这是 patent-anno-share 的强制前置检查：**外文文献务必核对段落，不能漏段缺段**。
不通过就不允许往下做 HTML（有缺口时退出码 2）。

查六项：
  1. range       段号区间 0001-0125
  2. expected    应有段数 = 上限 - 下限 + 1（或 --expect 指定值）
  3. found       实有唯一段数
  4. gaps        区间内缺失的段号（真正意义上的「漏段」）
  5. duplicates  重复段号
  6. untranslated / zh_suspect   未译与疑似没译干净的段（中文占比过低、或与英文完全相同）
  另查 placeholder：正文里残留的「（待补）」占位符

用法：
    python check_md.py "US20160183596A1_全文_中英对照.md" --expect 125
    python check_md.py xxx.md --allow-gap      # 明知原文本段号不连续时才用

退出码：0 全绿；2 有问题；1 文件不存在/解析失败
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parse_md import parse  # noqa: E402  复用同一套段落切分逻辑

HEAD_RE = re.compile(r"^###\s*【(\d{4})】\s*$", re.M)
PLACEHOLDER = "（待补）"


def cjk_ratio(s):
    """汉字占比。译文里汉字太少 = 没译或只译了一半。"""
    if not s:
        return 0.0
    cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    return cjk / max(1, len(s))


def check(md_text, expect=None):
    items, stats = parse(md_text)
    nos = sorted(int(i["no"]) for i in items)
    heads = HEAD_RE.findall(md_text)

    res = {"ok": True}
    if not nos:
        return {"ok": False, "error": "md 里没有任何 ### 【dddd】 段号块"}, 2

    lo, hi = nos[0], nos[-1]
    have = set(nos)
    gaps = ["%04d" % n for n in range(lo, hi + 1) if n not in have]
    expected = expect if expect else hi - lo + 1

    # 未译：空、占位符、与英文完全相同、汉字占比过低
    untranslated, zh_suspect = [], []
    for it in items:
        zh, en = it["zh"].strip(), it["en"].strip()
        if (not zh) or zh == PLACEHOLDER or zh == en:
            untranslated.append(it["no"])
        elif cjk_ratio(zh) < 0.10:
            zh_suspect.append(it["no"])

    placeholders = md_text.count(PLACEHOLDER)
    empty_en = stats["empty_en"]

    res.update({
        "range": "%04d-%04d" % (lo, hi),
        "expected": expected,
        "found": len(have),
        "head_count": len(heads),
        "gaps": gaps,
        "duplicates": stats["duplicates"],
        "untranslated": len(untranslated),
        "first_untranslated": untranslated[:10],
        "zh_suspect": zh_suspect[:10],
        "zh_suspect_count": len(zh_suspect),
        "empty_en": empty_en[:10],
        "placeholder_residue": placeholders,
    })

    problems = []
    if len(have) != expected:
        problems.append("段数不符：expected %d / found %d" % (expected, len(have)))
    if gaps:
        problems.append("有 %d 个段号缺口：%s" % (len(gaps), ", ".join(gaps[:20])))
    if stats["duplicates"]:
        problems.append("重复段号：%s" % ", ".join(stats["duplicates"][:20]))
    if untranslated:
        problems.append("%d 段未译：%s" % (len(untranslated), ", ".join(untranslated[:10])))
    if zh_suspect:
        problems.append("%d 段译文汉字占比过低（疑似未译/半译）：%s"
                        % (len(zh_suspect), ", ".join(zh_suspect[:10])))
    if empty_en:
        problems.append("%d 段英文为空：%s" % (len(empty_en), ", ".join(empty_en[:10])))
    if placeholders:
        problems.append("正文残留 %d 处「%s」" % (placeholders, PLACEHOLDER))

    res["problems"] = problems
    res["ok"] = not problems
    return res, (0 if not problems else 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("md")
    ap.add_argument("--expect", type=int, default=0,
                    help="原文应有段数（确知时才填）；不填则按段号区间推算")
    ap.add_argument("--allow-gap", action="store_true",
                    help="明知原文本段号不连续时放行（仍会打印缺口）")
    a = ap.parse_args()

    if not os.path.exists(a.md):
        print(json.dumps({"ok": False, "error": "文件不存在: %s" % a.md}, ensure_ascii=False))
        sys.exit(1)

    md_text = open(a.md, encoding="utf-8").read()
    res, code = check(md_text, a.expect or None)
    res["file"] = a.md

    if a.allow_gap and code == 2:
        only_gap = all(k.startswith("有 ") or k.startswith("段数不符") for k in res.get("problems", []))
        if only_gap:
            res["ok"] = True
            res["note"] = "--allow-gap：已放行段号缺口，请确认这是原文本身不连续"
            code = 0

    print(json.dumps(res, ensure_ascii=False))
    sys.exit(code)


if __name__ == "__main__":
    main()
