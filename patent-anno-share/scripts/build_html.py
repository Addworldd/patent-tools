# -*- coding: utf-8 -*-
"""把 data.json + 云端配置 注入模板，生成可批注页面。

用法：
    python build_html.py \
        --data data.json \
        --out  site/index.html \
        --cloud-endpoint "https://xxx.workbuddy.link" \
        --cloud-key      "pk_xxx" \
        --doc-id         "US20160183596A1"

若不给 --cloud-*，则生成「纯本地版」（批注只存浏览器 localStorage，无法汇总）。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.join(os.path.dirname(HERE), "templates", "page.html")

SDK_CDN = "https://cdn.jsdelivr.net/npm/@tencent-ai/workbuddy-cloud-sdk@dev/lib/index.global.js"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cloud-endpoint", default="")
    ap.add_argument("--cloud-key", default="")
    ap.add_argument("--doc-id", default="")
    ap.add_argument("--title", default="")
    ap.add_argument("--subtitle", default="")
    a = ap.parse_args()

    if not os.path.exists(a.data):
        print(json.dumps({"ok": False, "error": f"data 不存在: {a.data}"}, ensure_ascii=False))
        sys.exit(1)
    if not os.path.exists(TPL):
        print(json.dumps({"ok": False, "error": f"模板缺失: {TPL}"}, ensure_ascii=False))
        sys.exit(1)

    data = json.load(open(a.data, encoding="utf-8"))
    title = a.title or data.get("title") or "中英对照"
    subtitle = a.subtitle or data.get("subtitle") or ""
    doc_id = a.doc_id or data.get("docId") or "doc"

    cloud = {}
    if a.cloud_endpoint and a.cloud_key:
        cloud = {"endpoint": a.cloud_endpoint, "publishableKey": a.cloud_key}

    tpl = open(TPL, encoding="utf-8").read()

    # 注入数据（用 JSON 字面量，注意转义 </script>）
    data_js = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    cloud_js = json.dumps(cloud, ensure_ascii=False)

    html = (tpl
            .replace("__TITLE__", title.replace("<", "&lt;").replace("&", "&amp;"))
            .replace("__SUBTITLE__", subtitle.replace("<", "&lt;").replace("&", "&amp;"))
            .replace("__DATA__", data_js)
            .replace("__DOC_ID__", json.dumps(doc_id, ensure_ascii=False))
            .replace("__CLOUD_CFG__", cloud_js)
            .replace("__INLINED_MARKS_JSON__", "[]"))

    # 有云端配置时插入 SDK script
    if cloud:
        html = html.replace(
            '<script>\nconst DATA =',
            f'<script src="{SDK_CDN}"></script>\n<script>\nconst DATA ='
        )

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(html)

    print(json.dumps({
        "ok": True,
        "out": a.out,
        "paragraphs": data.get("count"),
        "doc_id": doc_id,
        "cloud": bool(cloud),
        "size": os.path.getsize(a.out),
        "note": "有 cloud=true 时可跨设备汇总批注；false 时仅本机 localStorage",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
