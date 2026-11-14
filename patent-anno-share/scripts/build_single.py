# -*- coding: utf-8 -*-
"""生成「单文件 HTML」——数据内嵌、无任何外部依赖。

产物特点：
  - 一个 .html 文件，双击即可打开，可直接发给别人
  - 无 CDN、无网络请求、无后端
  - 批注存浏览器 localStorage；用「导出/导入」在设备间搬运

用法：
    python build_single.py --data data.json --out "某某_可批注.html" [--doc-id ID] [--title 标题]
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.join(os.path.dirname(HERE), "templates", "page_local.html")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--doc-id", default="")
    ap.add_argument("--title", default="")
    ap.add_argument("--subtitle", default="")
    ap.add_argument("--compact", action="store_true",
                    help="数据压成一行（体积略小，但文件不便人工查看）")
    a = ap.parse_args()

    if not os.path.exists(a.data):
        print(json.dumps({"ok": False, "error": f"data 不存在: {a.data}"}, ensure_ascii=False))
        sys.exit(1)
    if not os.path.exists(TPL):
        print(json.dumps({"ok": False, "error": f"模板缺失: {TPL}"}, ensure_ascii=False))
        sys.exit(1)

    data = json.load(open(a.data, encoding="utf-8"))
    title = a.title or data.get("title") or "中英对照"
    subtitle = a.subtitle or data.get("subtitle") or "划选文字 → 加高亮 → 写备注。批注存本机浏览器，用「导出/导入」在设备间搬运。"
    doc_id = a.doc_id or data.get("docId") or "doc"

    tpl = open(TPL, encoding="utf-8").read()

    sep = (",", ":") if a.compact else (",", ": ")
    indent = None if a.compact else 1
    data_js = json.dumps(data, ensure_ascii=False, separators=sep, indent=indent)
    # 防止数据里出现 </script> 提前闭合脚本
    data_js = data_js.replace("</", "<\\/")

    def h(s):
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    if subtitle:
        import re
        subtitle = re.sub(r'[\r\n]+', '、', subtitle).strip('、 ')

    html = (tpl
            .replace("__TITLE__", h(title))
            .replace("__SUBTITLE__", h(subtitle))
            .replace("__DATA__", data_js)
            .replace("__DOC_ID__", json.dumps(doc_id, ensure_ascii=False)))

    # 自检：不应残留占位符，不应有外部资源引用
    leftovers = [p for p in ("__TITLE__", "__SUBTITLE__", "__DATA__", "__DOC_ID__") if p in html]
    if leftovers:
        print(json.dumps({"ok": False, "error": f"占位符未替换: {leftovers}"}, ensure_ascii=False))
        sys.exit(1)

    import re
    ext = []
    for pat in (r'<script[^>]+src="(https?://[^"]+)"',
                r'<link[^>]+href="(https?://[^"]+)"',
                r'<img[^>]+src="(https?://[^"]+)"'):
        ext += re.findall(pat, html)
    if ext:
        print(json.dumps({"ok": False, "error": f"仍含外部引用: {ext[:3]}"}, ensure_ascii=False))
        sys.exit(1)

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(html)

    size = os.path.getsize(a.out)
    print(json.dumps({
        "ok": True,
        "out": a.out,
        "size_kb": round(size / 1024, 1),
        "paragraphs": data.get("count"),
        "doc_id": doc_id,
        "external_refs": 0,
        "note": "单文件，双击即可打开；批注存 localStorage，用导出/导入在设备间搬运",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
