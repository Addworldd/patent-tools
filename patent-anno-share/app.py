# -*- coding: utf-8 -*-
"""
智慧芽专利中英对照可批注 HTML 生成器 - 主入口 (app.py)

功能升级：
1. 单篇极速生成 + 批量批量导出（支持多行链接粘贴 / 上传 Excel 表格自动提取超链接）；
2. 内置 DeepSeek 大模型高精度专利专业翻译与质量质检门禁；
3. 支持官方高清附图下载内嵌 (路线B) 与滚动条高亮指示滑轨；
4. 支持双模运行：本地 WebUI 控制面板 + 命令行 CLI 批量调用。
"""

import os
import re
import sys
import io
import json
import time
import socket
import asyncio
import base64
import tempfile
import threading
import subprocess
import webbrowser

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs, unquote

# 资源路径自适应（兼容 PyInstaller 打包环境 sys._MEIPASS）
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
    APP_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    APP_DIR = BASE_DIR

SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import openpyxl
from fetch_share import fetch_patent_from_share_url, export_as_markdown, export_as_data_json, verify_and_heal_description
from deepseek_translator import DeepSeekTranslator, translate_patent_data_sync, load_config, save_config
from batch_parser import parse_excel_file, parse_text_links


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class GlobalState:
    def __init__(self):
        self.lock = threading.Lock()
        self.mode = "single"  # single or batch
        self.status = "idle"  # idle, running, completed, failed
        self.progress = 0
        self.stage = ""
        self.logs = []
        self.final_html = ""
        self.error = None
        
        # 批量任务状态
        self.batch_tasks = []
        self.batch_total = 0
        self.batch_completed = 0
        self.batch_current_idx = 0

    def reset_single(self):
        with self.lock:
            self.mode = "single"
            self.status = "running"
            self.progress = 5
            self.stage = "正在准备启动浏览器引擎..."
            self.logs = []
            self.final_html = ""
            self.error = None

    def reset_batch(self, tasks):
        with self.lock:
            self.mode = "batch"
            self.status = "running"
            self.progress = 0
            self.stage = f"正在启动批量导出任务（共 {len(tasks)} 件）..."
            self.logs = []
            self.batch_tasks = tasks
            self.batch_total = len(tasks)
            self.batch_completed = 0
            self.batch_current_idx = 0
            self.final_html = ""
            self.error = None

    def add_log(self, text):
        with self.lock:
            clean = text.strip()
            if not clean:
                return
            self.logs.append(clean)
            if self.mode == "single":
                if "访问摘要页" in clean:
                    self.progress = 15
                    self.stage = "1/4: 访问摘要页建立鉴权会话并提取著录项..."
                elif "访问权利要求页" in clean:
                    self.progress = 30
                    self.stage = "2/4: 访问权利要求页提取原文及权项..."
                elif "访问说明书页" in clean:
                    self.progress = 45
                    self.stage = "3/4: 访问说明书页提取原文段落及段号..."
                elif "DeepSeek" in clean and "启动" in clean:
                    self.progress = 60
                    self.stage = "4/4: 正在调用 DeepSeek 进行大模型高精专利翻译..."
                elif "HTML 单文件生成完成" in clean:
                    self.progress = 100
                    self.stage = "全部完成！"

    def update_single_progress(self, current, total, item_type="项"):
        with self.lock:
            if self.mode == "single":
                ratio = current / max(1, total)
                self.progress = int(60 + ratio * 30)
                self.stage = f"正在进行 DeepSeek 专利翻译: {current}/{total} {item_type}..."

    def update_batch_task(self, idx, status, html_path="", error=""):
        with self.lock:
            if 0 <= idx < len(self.batch_tasks):
                self.batch_tasks[idx]["status"] = status
                if html_path:
                    self.batch_tasks[idx]["html"] = html_path
                if error:
                    self.batch_tasks[idx]["error"] = str(error)
                if status in ["completed", "failed"]:
                    self.batch_completed += 1
                ratio = self.batch_completed / max(1, self.batch_total)
                self.progress = int(ratio * 100)
                self.stage = f"批量进度: {self.batch_completed}/{self.batch_total} 件已完成 ({self.progress}%)"

    def get_state(self, since=0):
        with self.lock:
            return {
                "mode": self.mode,
                "status": self.status,
                "progress": self.progress,
                "stage": self.stage,
                "logs": self.logs[since:],
                "final_html": self.final_html,
                "error": self.error,
                "batch_tasks": self.batch_tasks,
                "batch_total": self.batch_total,
                "batch_completed": self.batch_completed,
                "batch_current_idx": self.batch_current_idx
            }

    def complete(self, final_html=""):
        with self.lock:
            self.status = "completed"
            self.progress = 100
            self.stage = "全部任务生成完成！"
            self.final_html = final_html

    def fail(self, err_msg):
        with self.lock:
            self.status = "failed"
            self.error = str(err_msg)


STATE = GlobalState()


def process_single_patent(url, api_key=None, out_dir=None, with_figures=True, target_lang=None, extra_prompt=None, log_cb=None, prog_cb=None):
    """单篇专利全流程处理：抓取 -> DeepSeek 翻译 -> 导出 MD -> 导出 Data.json -> 渲染 HTML"""
    output_dir = out_dir or os.path.join(APP_DIR, "output")
    os.makedirs(output_dir, exist_ok=True)

    def log(msg):
        if log_cb:
            log_cb(msg)
        else:
            print(msg)

    log(f"[*] 开始处理: {url[:70]}...")

    # 1. 抓取专利原文与元数据
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    class LogCapturer:
        def write(self, s):
            if s.strip():
                log(s.strip())
        def flush(self): pass

    old_stdout = sys.stdout
    sys.stdout = LogCapturer()
    try:
        patent_data = loop.run_until_complete(fetch_patent_from_share_url(
            url, 
            headless=True,
            with_figures=with_figures
        ))
    finally:
        sys.stdout = old_stdout
        loop.close()

    bib = patent_data.get('biblio', {})
    pn = bib.get('pn') or 'PATENT'
    title = bib.get('title') or ''

    # 2. 调用 DeepSeek 翻译（若可用）
    translator = DeepSeekTranslator(api_key=api_key, target_lang=target_lang, extra_prompt=extra_prompt)
    if translator.is_configured() and patent_data.get('is_foreign', True):
        extra_hint = f" (含附加提示词: {len(translator.extra_prompt)} 字)" if translator.extra_prompt else ""
        log(f"[+] [{pn}] 检测到 DeepSeek 配置，正在启动大模型高精专利翻译 (目标语言: {translator.target_lang}{extra_hint})...")
        def on_prog(cur, tot, itype):
            if prog_cb:
                prog_cb(cur, tot, itype)
        patent_data = translate_patent_data_sync(patent_data, api_key=api_key, progress_callback=on_prog, target_lang=translator.target_lang, extra_prompt=translator.extra_prompt)
        qual = patent_data.get("translation_quality", {})
        log(f"[✔] [{pn}] 翻译门禁质检报告: 权项覆盖率 {qual.get('claim_coverage', '-')}, 说明书覆盖率 {qual.get('desc_coverage', '-')}")
        from fetch_share import extract_reference_numerals
        patent_data['glossary'] = extract_reference_numerals(patent_data.get('claims', []), patent_data.get('description', []))

    # 2.2 说明书段号一致性校验与门禁自检（Gatekeeper）
    desc_list = patent_data.get('description', [])
    if desc_list:
        healed_desc, heal_report = verify_and_heal_description(desc_list, patent_id=pn)
        patent_data['description'] = healed_desc
        patent_data['continuity'] = heal_report
        log(f"[✔] [{pn}] 智慧芽段号门禁质检: 范围={heal_report.get('range')}, 总段数={heal_report.get('count')}, 缺口={heal_report.get('gaps') or '无'}")
        if heal_report.get('actions'):
            for act in heal_report['actions']:
                log(f"    -> 说明书段号纠偏: {act}")

    inter_dir = os.path.join(output_dir, "_intermediates")
    os.makedirs(inter_dir, exist_ok=True)

    # 2.5 导出原始抓取 JSON (存入 _intermediates 子目录)
    raw_path = os.path.join(inter_dir, f"{pn}_patsnap_raw.json")
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(patent_data, f, ensure_ascii=False, indent=2)

    # 3. 导出 Markdown (存入 _intermediates 子目录)
    md_path = os.path.join(inter_dir, f"{pn}_全文_中英对照.md")
    export_as_markdown(patent_data, md_path)
    log(f"[+] [{pn}] Markdown 已生成: {md_path}")

    # 4. 导出 data.json (存入 _intermediates 子目录)
    data_path = os.path.join(inter_dir, f"{pn}_data.json")
    export_as_data_json(patent_data, data_path)

    # 5. 生成单文件 HTML (输出至根目录，命名为 [本机版])
    html_path = os.path.join(output_dir, f"{pn}_中英对照_[本机版].html")
    tpl_path = os.path.join(BASE_DIR, "templates", "page_local.html")
    if not os.path.exists(tpl_path):
        raise FileNotFoundError(f"找不到 HTML 模板: {tpl_path}")

    with open(tpl_path, "r", encoding="utf-8") as f:
        tpl = f.read()

    with open(data_path, "r", encoding="utf-8") as f:
        data_obj = json.load(f)

    # 安全清洗 data_obj 中所有字符串的非法控制字符（除 \n, \t 外的 ASCII < 32），杜绝 JSON 解析报错
    def sanitize_obj(obj):
        if isinstance(obj, str):
            return "".join(c for c in obj if c in ('\n', '\t') or ord(c) >= 32)
        elif isinstance(obj, list):
            return [sanitize_obj(x) for x in obj]
        elif isinstance(obj, dict):
            return {k: sanitize_obj(v) for k, v in obj.items()}
        return obj

    data_obj = sanitize_obj(data_obj)
    data_js = json.dumps(data_obj, ensure_ascii=False, indent=1).replace("</", "<\\/")

    def h(s):
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    full_title = f"{pn} - {title}" if title else pn
    full_sub = data_obj.get("subtitle", "")
    if full_sub:
        full_sub = re.sub(r'[\r\n]+', '、', full_sub).strip('、 ')
    html = (tpl
            .replace("__TITLE__", h(full_title))
            .replace("__SUBTITLE__", h(full_sub))
            .replace("__DATA__", data_js)
            .replace("__DOC_ID__", json.dumps(pn, ensure_ascii=False)))

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    html_size_kb = round(os.path.getsize(html_path) / 1024, 1)
    log(f"[✔] [{pn}] HTML 单文件生成完成: {html_path} ({html_size_kb} KB)")

    return {
        "ok": True,
        "pn": pn,
        "title": title,
        "html_path": html_path,
        "size_kb": html_size_kb
    }


def run_single_pipeline_worker(url, api_key=None, out_dir=None, with_figures=True, target_lang=None, extra_prompt=None):
    """单篇后台任务执行器"""
    try:
        STATE.reset_single()
        res = process_single_patent(
            url,
            api_key=api_key,
            out_dir=out_dir,
            with_figures=with_figures,
            target_lang=target_lang,
            extra_prompt=extra_prompt,
            log_cb=STATE.add_log,
            prog_cb=STATE.update_single_progress
        )
        STATE.complete(res["html_path"])
    except Exception as e:
        STATE.fail(str(e))
        STATE.add_log(f"[!] 处理异常: {e}")


def run_batch_pipeline_worker(tasks, api_key=None, out_dir=None, with_figures=True, target_lang=None, extra_prompt=None):
    """批量后台任务执行器"""
    try:
        STATE.reset_batch(tasks)
        total = len(tasks)
        STATE.add_log(f"============================================================")
        STATE.add_log(f"[*] 开始批量执行流水线，待处理总数: {total} 件 (目标语言: {target_lang or '中文'})")
        STATE.add_log(f"============================================================")

        for i, item in enumerate(tasks):
            url = item.get("url", "").strip()
            pn_hint = item.get("pn", f"#{i+1}")
            if not url:
                STATE.update_batch_task(i, "failed", error="URL 为空")
                continue

            STATE.batch_current_idx = i
            STATE.update_batch_task(i, "running")
            STATE.add_log(f"\n---> [{i+1}/{total}] 正在提取: {pn_hint}...")

            try:
                res = process_single_patent(
                    url,
                    api_key=api_key,
                    out_dir=out_dir,
                    with_figures=with_figures,
                    target_lang=target_lang,
                    extra_prompt=extra_prompt,
                    log_cb=STATE.add_log,
                    prog_cb=None
                )
                STATE.update_batch_task(i, "completed", html_path=res["html_path"])
                STATE.add_log(f"[✔] [{i+1}/{total}] {res['pn']} 成功完成！")
            except Exception as e:
                STATE.update_batch_task(i, "failed", error=str(e))
                STATE.add_log(f"[!] [{i+1}/{total}] {pn_hint} 处理失败: {e}")

        STATE.complete()
        STATE.add_log(f"\n============================================================")
        STATE.add_log(f"🎉 批量任务全部处理完毕！共处理 {total} 件，详情见下方清单。")
        STATE.add_log(f"============================================================")

    except Exception as e:
        STATE.fail(str(e))
        STATE.add_log(f"[!] 批量任务异常中断: {e}")


class RequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            html_file = os.path.join(BASE_DIR, "web", "index.html")
            if os.path.exists(html_file):
                with open(html_file, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(content)
            else:
                self.send_error(404, "web/index.html not found")
        elif path == "/api/config":
            cfg = load_config()
            safe_cfg = cfg.copy()
            k = safe_cfg.get("api_key", "")
            if k and len(k) > 10:
                safe_cfg["api_key_masked"] = k[:6] + "..." + k[-4:]
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(safe_cfg).encode("utf-8"))
        elif path == "/api/status":
            qs = parse_qs(parsed.query)
            since = int(qs.get("since", [0])[0])
            state = STATE.get_state(since=since)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(state).encode("utf-8"))
        elif path == "/api/open":
            qs = parse_qs(parsed.query)
            target = unquote(qs.get("target", [""])[0])
            if target and os.path.exists(target):
                webbrowser.open("file://" + os.path.abspath(target))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        elif path == "/api/open-folder":
            qs = parse_qs(parsed.query)
            target = unquote(qs.get("target", [""])[0])
            out_dir = target if (target and os.path.isdir(target)) else os.path.join(APP_DIR, "output")
            os.makedirs(out_dir, exist_ok=True)
            if sys.platform == 'win32':
                subprocess.Popen(f'explorer "{os.path.abspath(out_dir)}"', shell=True)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length) if length > 0 else b'{}'
        try:
            data = json.loads(body.decode('utf-8'))
        except Exception:
            data = {}

        if path == "/api/config":
            new_key = data.get("api_key", "").strip()
            new_lang = data.get("target_lang", "").strip()
            new_prompt = data.get("extra_prompt", None)
            cfg = load_config()
            if new_key:
                cfg["api_key"] = new_key
            if new_lang:
                cfg["target_lang"] = new_lang
            if new_prompt is not None:
                cfg["extra_prompt"] = new_prompt.strip()
            save_config(cfg)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        elif path == "/api/test-deepseek":
            req_key = data.get("api_key", "").strip() or None
            translator = DeepSeekTranslator(api_key=req_key)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                res = loop.run_until_complete(translator.test_connection())
            finally:
                loop.close()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))

        elif path == "/api/start":
            url = data.get("url", "").strip()
            api_key = data.get("api_key", "").strip() or None
            with_figures = bool(data.get("with_figures", True))
            target_lang = data.get("target_lang", "中文").strip() or "中文"
            extra_prompt = data.get("extra_prompt", "").strip() or None
            if not url:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"ok":false,"error":"URL cannot be empty"}')
                return

            t = threading.Thread(target=run_single_pipeline_worker, args=(url, api_key, None, with_figures, target_lang, extra_prompt))
            t.daemon = True
            t.start()

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        elif path == "/api/parse-batch":
            text = data.get("text", "").strip()
            file_base64 = data.get("file_base64", "")
            filename = data.get("filename", "")

            items = []
            if file_base64:
                try:
                    file_bytes = base64.b64decode(file_base64)
                    suffix = os.path.splitext(filename)[1] or ".xlsx"
                    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                        tmp.write(file_bytes)
                        tmp_path = tmp.name

                    if suffix.lower() in [".xlsx", ".xls"]:
                        items = parse_excel_file(tmp_path)
                    else:
                        txt_content = file_bytes.decode("utf-8", errors="ignore")
                        items = parse_text_links(txt_content)
                    
                    try: os.unlink(tmp_path)
                    except: pass
                except Exception as e:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": False, "error": f"解析文件失败: {str(e)}"}, ensure_ascii=False).encode("utf-8"))
                    return
            elif text:
                items = parse_text_links(text)

            # 结构格式化
            formatted = []
            for i, it in enumerate(items):
                formatted.append({
                    "id": i + 1,
                    "pn": it.get("pn", f"PATENT_{i+1}"),
                    "url": it.get("url", ""),
                    "status": "waiting",
                    "html": "",
                    "error": ""
                })

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "items": formatted, "count": len(formatted)}, ensure_ascii=False).encode("utf-8"))

        elif path == "/api/start-batch":
            tasks = data.get("tasks", [])
            api_key = data.get("api_key", "").strip() or None
            with_figures = bool(data.get("with_figures", True))
            target_lang = data.get("target_lang", "中文").strip() or "中文"
            if not tasks:
                self.send_response(400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": "任务列表不能为空"}, ensure_ascii=False).encode("utf-8"))
                return

            t = threading.Thread(target=run_batch_pipeline_worker, args=(tasks, api_key, None, with_figures, target_lang))
            t.daemon = True
            t.start()

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        else:
            self.send_error(404)

    def log_message(self, format, *args):
        pass


def find_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main():
    if len(sys.argv) > 1 and not sys.argv[1].startswith("--gui"):
        import argparse
        parser = argparse.ArgumentParser(description="智慧芽专利中英对照可批注 HTML 生成器")
        parser.add_argument("url", nargs="?", help="智慧芽分享链接 (若指定则执行单篇)")
        parser.add_argument("--batch-file", help="批量链接文本文件 (每行一个 URL)")
        parser.add_argument("--batch-excel", help="批量 Excel 表格路径")
        parser.add_argument("--api-key", help="DeepSeek API Key")
        parser.add_argument("--target-lang", default="中文", help="译文目标语言 (例如: 中文, 英文, 日语, 德语, 韩语 等)")
        parser.add_argument("--with-figures", action=argparse.BooleanOptionalAction, default=True,
                            help="是否拉取官方附图")
        parser.add_argument("--out-dir", default=os.path.join(APP_DIR, "output"), help="输出目录")
        args = parser.parse_args()

        if args.batch_excel:
            items = parse_excel_file(args.batch_excel)
            run_batch_pipeline_worker(items, api_key=args.api_key, out_dir=args.out_dir, with_figures=args.with_figures, target_lang=args.target_lang)
        elif args.batch_file:
            with open(args.batch_file, "r", encoding="utf-8") as f:
                items = parse_text_links(f.read())
            run_batch_pipeline_worker(items, api_key=args.api_key, out_dir=args.out_dir, with_figures=args.with_figures, target_lang=args.target_lang)
        elif args.url:
            run_single_pipeline_worker(args.url, api_key=args.api_key, out_dir=args.out_dir, with_figures=args.with_figures, target_lang=args.target_lang)
        else:
            parser.print_help()
        return

    # 默认启动 WebUI
    port = find_free_port()
    server = ThreadingHTTPServer(('127.0.0.1', port), RequestHandler)
    url = f"http://127.0.0.1:{port}"

    print("================================================================================")
    print(" 智慧芽专利中英对照生成器 - 支持单篇与批量导出 (内置 DeepSeek 专业专利翻译) ")
    print("================================================================================")
    print(f"[*] 本地 WebUI 控制台运行于: {url}")
    print("[*] 正在启动并打开默认浏览器...")

    threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] 服务已停止。")


if __name__ == "__main__":
    main()
