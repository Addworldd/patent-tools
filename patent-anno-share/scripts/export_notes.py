# -*- coding: utf-8 -*-
"""从云端导出批注 → Markdown 汇总表。

用法：
    python export_notes.py \
        --endpoint "https://xxx.workbuddy.link" \
        --key      "pk_xxx" \
        --doc-id   "US20160183596A1" \
        --out      annotations.md

调用的是云端 Database REST 接口（/.cloud/database/rest），
不需要浏览器环境。
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

SIDE_ZH = {"en": "原文", "zh": "译文"}


def fetch_all(endpoint, key, doc_id, table="annotations", page=500):
    """分页拉取全部批注。"""
    base = endpoint.rstrip("/")
    url = f"{base}/.cloud/database/rest/v1/{table}"
    rows, offset = [], 0
    while True:
        qs = urllib.parse.urlencode({
            "select": "*",
            "doc_id": f"eq.{doc_id}",
            "order": "para_no.asc",
            "limit": page,
            "offset": offset,
        })
        req = urllib.request.Request(
            f"{url}?{qs}",
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore")[:400]
            return None, f"HTTP {e.code}: {body}"
        except Exception as e:
            return None, f"{type(e).__name__}: {e}"

        # 兼容两种返回形态：直接数组 / {data:[...]}
        items = data if isinstance(data, list) else (data.get("data") or data.get("rows") or [])
        if not items:
            break
        rows.extend(items)
        if len(items) < page:
            break
        offset += page
    return rows, None


def to_markdown(rows, title, doc_id):
    noted = [r for r in rows if (r.get("note") or "").strip()]
    noted.sort(key=lambda r: (str(r.get("para_no", "")),
                              r.get("side") != "en",
                              int(r.get("start_off") or 0)))

    L = [f"# {title}", "",
         f"- 文档：`{doc_id}`",
         f"- 批注总数：**{len(rows)}**（其中已写备注 **{len(noted)}**）",
         f"- 导出时间：{__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}",
         "",
         "## 批注明细", "",
         "| # | 段号 | 侧 | 位置 | 选中文字 | 备注 | 批注人 |",
         "|---|---|---|---|---|---|---|"]

    for i, r in enumerate(noted, 1):
        txt = (r.get("sel_text") or "").replace("|", "\\|").replace("\n", " ")
        if len(txt) > 90:
            txt = txt[:90] + "…"
        note = (r.get("note") or "").replace("|", "\\|").replace("\n", " ")
        side = SIDE_ZH.get(r.get("side"), r.get("side") or "")
        L.append(f"| {i} | 【{r.get('para_no','')}】 | {side} | "
                 f"{r.get('start_off')}–{r.get('end_off')} | {txt} | {note} | "
                 f"{r.get('author') or ''} |")

    if not noted:
        L.append("| — | — | — | — | — | 暂无备注 | — |")

    # 只标了高亮但没写备注的
    plain_marks = [r for r in rows if not (r.get("note") or "").strip()]
    if plain_marks:
        L += ["", "## 仅高亮、未写备注", "",
              "| 段号 | 侧 | 位置 | 选中文字 |", "|---|---|---|---|"]
        for r in sorted(plain_marks, key=lambda x: (str(x.get("para_no", "")), int(x.get("start_off") or 0))):
            txt = (r.get("sel_text") or "").replace("|", "\\|").replace("\n", " ")
            if len(txt) > 70:
                txt = txt[:70] + "…"
            side = SIDE_ZH.get(r.get("side"), r.get("side") or "")
            L.append(f"| 【{r.get('para_no','')}】 | {side} | "
                     f"{r.get('start_off')}–{r.get('end_off')} | {txt} |")

    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--doc-id", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="批注汇总")
    ap.add_argument("--table", default="annotations")
    ap.add_argument("--json-out", default="", help="同时存一份原始 JSON")
    a = ap.parse_args()

    rows, err = fetch_all(a.endpoint, a.key, a.doc_id, a.table)
    if err:
        print(json.dumps({"ok": False, "error": err}, ensure_ascii=False))
        sys.exit(1)

    md = to_markdown(rows, a.title, a.doc_id)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(md)

    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)

    noted = sum(1 for r in rows if (r.get("note") or "").strip())
    print(json.dumps({
        "ok": True, "total": len(rows), "noted": noted,
        "out": a.out, "json": a.json_out or None,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
