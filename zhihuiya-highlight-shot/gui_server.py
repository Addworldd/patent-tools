# -*- coding: utf-8 -*-
"""
智慧芽专利高亮截图与附图生成器 - 本地轻量 WebUI 服务端 (Zero-dependency Local Web Server)
"""

import os
import re
import sys
import json
import time
import base64
import socket
import asyncio
import threading
import subprocess
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

# 确保脚本目录在 sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_INNER = os.path.join(SCRIPT_DIR, 'scripts')
if SCRIPTS_INNER not in sys.path:
    sys.path.insert(0, SCRIPTS_INNER)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from run_highlight_shot_pipeline import run_pipeline


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


# 全局任务状态管理
class TaskManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.status = "idle"  # idle, running, completed, failed
        self.progress = 0
        self.stage = ""
        self.logs = []
        self.final_docx = ""
        self.error = None

    def reset(self):
        with self.lock:
            self.status = "running"
            self.progress = 5
            self.stage = "正在启动流水线引擎..."
            self.logs = []
            self.final_docx = ""
            self.error = None

    def add_log(self, text):
        with self.lock:
            clean_text = text.strip()
            if not clean_text:
                return
            self.logs.append(clean_text)

            # 智能解析进度与阶段
            if "阶段 1/3" in clean_text:
                self.progress = 15
                self.stage = "阶段 1/3: 正在解析报告结构与提取对比文件..."
            elif "阶段 2/3" in clean_text:
                self.progress = 30
                self.stage = "阶段 2/3: 正在连接智慧芽抓取高亮长图与附图..."
            elif "正在处理 相关文献" in clean_text:
                self.progress = min(85, self.progress + 15)
                self.stage = f"正在处理: {clean_text}"
            elif "阶段 3/3" in clean_text:
                self.progress = 90
                self.stage = "阶段 3/3: 正在生成并回填 Word 报告..."
            elif "最终 Word 报告已生成" in clean_text or "成果文档:" in clean_text:
                self.progress = 100
                self.stage = "处理完毕！"

    def get_state(self, since=0):
        with self.lock:
            return {
                "status": self.status,
                "progress": self.progress,
                "stage": self.stage,
                "logs": self.logs[since:],
                "final_docx": self.final_docx,
                "error": self.error
            }

    def complete(self, final_docx):
        with self.lock:
            self.status = "completed"
            self.progress = 100
            self.stage = "处理完成！"
            self.final_docx = final_docx

    def fail(self, err_msg):
        with self.lock:
            self.status = "failed"
            self.error = str(err_msg)
            self.stage = f"执行异常: {err_msg}"


task_manager = TaskManager()


class StdoutRedirector:
    """拦截 stdout 并广播给任务日志。"""
    def __init__(self, original_stdout, manager):
        self.original_stdout = original_stdout
        self.manager = manager

    def write(self, s):
        self.original_stdout.write(s)
        self.original_stdout.flush()
        if s.strip():
            self.manager.add_log(s)

    def flush(self):
        self.original_stdout.flush()


def run_pipeline_worker(docx_path, refs, no_headless):
    redirector = StdoutRedirector(sys.stdout, task_manager)
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = redirector
    sys.stderr = redirector

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        res = loop.run_until_complete(
            run_pipeline(
                docx_path=docx_path,
                refs=refs,
                headless=(not no_headless)
            )
        )
        if res.get('success') and res.get('final_docx'):
            task_manager.complete(res.get('final_docx'))
        else:
            task_manager.fail(res.get('message') or "执行未成功完成")
    except Exception as e:
        task_manager.fail(str(e))
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr


class RequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # 屏蔽 http server 的频繁网络访问日志

    def do_GET(self):
        if self.path == '/' or self.path.startswith('/?'):
            self.serve_html()
        elif self.path.startswith('/api/status'):
            self.handle_status()
        else:
            self.send_error(404, "Not Found")

    def do_POST(self):
        if self.path == '/api/select_file':
            self.handle_select_file()
        elif self.path == '/api/resolve_dropped_file':
            self.handle_resolve_dropped_file()
        elif self.path == '/api/upload_file_data':
            self.handle_upload_file_data()
        elif self.path == '/api/start':
            self.handle_start()
        elif self.path == '/api/open_file':
            self.handle_open_file()
        elif self.path == '/api/open_folder':
            self.handle_open_folder()
        else:
            self.send_error(404, "Not Found")

    def serve_html(self):
        html_path = os.path.join(SCRIPT_DIR, 'web', 'index.html')
        if not os.path.exists(html_path):
            self.send_error(404, "index.html not found")
            return
        with open(html_path, 'r', encoding='utf-8') as f:
            content = f.read().encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def handle_status(self):
        # 解析 query 参数 since
        since = 0
        if '?since=' in self.path:
            try:
                since = int(self.path.split('?since=')[1])
            except ValueError:
                since = 0
        data = task_manager.get_state(since=since)
        self.send_json(data)

    def handle_select_file(self):
        """调用 Windows 原生文件选择对话框。"""
        selected_path = ""
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            selected_path = filedialog.askopenfilename(
                title="选择专利查新/检索报告",
                filetypes=[
                    ("Word 报告文件 (*.docx;*.doc)", "*.docx;*.doc"),
                    ("Word 文档 (*.docx)", "*.docx"),
                    ("Word 97-2003 文档 (*.doc)", "*.doc"),
                    ("所有文件 (*.*)", "*.*")
                ]
            )
            root.destroy()
        except Exception as e:
            print("弹出文件选择对话框异常:", e)

        self.send_json({"path": selected_path})

    def handle_resolve_dropped_file(self):
        """
        根据前端拖拽得到的文件名与大小，在用户常用目录中毫秒级匹配真实物理路径。
        保留原路径生成特性。
        """
        body = self.read_json_body()
        name = body.get('name', '').strip()
        size = body.get('size')
        if not name:
            self.send_json({"found": False})
            return

        user_home = os.path.expanduser('~')
        search_dirs = [
            os.path.join(user_home, 'Desktop'),
            os.path.join(user_home, 'Downloads'),
            os.path.join(user_home, 'Documents'),
            os.getcwd()
        ]

        found_path = None
        for d in search_dirs:
            if not os.path.exists(d):
                continue
            target = os.path.join(d, name)
            if os.path.exists(target):
                if size is None or os.path.getsize(target) == size:
                    found_path = target
                    break
            # 搜索一级常用子目录
            try:
                for entry in os.scandir(d):
                    if entry.is_dir() and not entry.name.startswith('.'):
                        sub_target = os.path.join(entry.path, name)
                        if os.path.exists(sub_target):
                            if size is None or os.path.getsize(sub_target) == size:
                                found_path = sub_target
                                break
                if found_path:
                    break
            except Exception:
                pass

        if found_path:
            self.send_json({"found": True, "path": os.path.abspath(found_path)})
        else:
            self.send_json({"found": False})

    def handle_upload_file_data(self):
        """
        拖拽兜底：若本地深层目录未能快速命中，则将 Base64 数据保存至桌面，
        确保在桌面原路径可追踪并生成产物。
        """
        body = self.read_json_body()
        name = body.get('name', '未命名报告.docx')
        b64_str = body.get('base64', '')
        if not b64_str:
            self.send_json({"success": False, "error": "数据流为空"})
            return

        try:
            file_bytes = base64.b64decode(b64_str)
            desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
            save_path = os.path.join(desktop, name)
            # 避免覆盖重名
            if os.path.exists(save_path):
                # 检查内容是否相同
                with open(save_path, 'rb') as f:
                    if f.read() == file_bytes:
                        self.send_json({"success": True, "path": save_path})
                        return

            with open(save_path, 'wb') as f:
                f.write(file_bytes)

            self.send_json({"success": True, "path": save_path})
        except Exception as e:
            self.send_json({"success": False, "error": str(e)})

    def handle_start(self):
        if task_manager.status == "running":
            self.send_json({"success": False, "error": "当前已有任务正在执行中，请等待完成"})
            return

        body = self.read_json_body()
        docx_path = (body.get('docx_path') or '').strip()
        refs = body.get('refs')
        no_headless = bool(body.get('no_headless'))

        if not docx_path or not os.path.exists(docx_path):
            self.send_json({"success": False, "error": f"报告文件路径不存在: {docx_path}"})
            return

        task_manager.reset()
        t = threading.Thread(target=run_pipeline_worker, args=(docx_path, refs, no_headless), daemon=True)
        t.start()
        self.send_json({"success": True})

    def handle_open_file(self):
        body = self.read_json_body()
        p = body.get('path', '')
        if p and os.path.exists(p):
            try:
                os.startfile(p)
                self.send_json({"success": True})
            except Exception as e:
                self.send_json({"success": False, "error": str(e)})
        else:
            self.send_json({"success": False, "error": "文件不存在"})

    def handle_open_folder(self):
        body = self.read_json_body()
        p = body.get('path', '')
        if p and os.path.exists(p):
            try:
                norm_p = os.path.normpath(p)
                subprocess.Popen(['explorer', '/select,', norm_p])
                self.send_json({"success": True})
            except Exception as e:
                self.send_json({"success": False, "error": str(e)})
        else:
            self.send_json({"success": False, "error": "文件不存在"})

    def read_json_body(self):
        length = int(self.headers.get('Content-Length', 0))
        if length > 0:
            raw = self.rfile.read(length).decode('utf-8')
            try:
                return json.loads(raw)
            except Exception:
                return {}
        return {}

    def send_json(self, data):
        content = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def find_free_port(start_port=8765):
    for port in range(start_port, start_port + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', port)) != 0:
                return port
    return start_port


def main():
    port = find_free_port(8765)
    server_address = ('127.0.0.1', port)
    httpd = ThreadingHTTPServer(server_address, RequestHandler)
    url = f"http://127.0.0.1:{port}"

    print("================================================================================")
    print("      智慧芽专利高亮截图与附图生成器 - 可视化界面已就绪 (WebUI Server)       ")
    print("================================================================================")
    print(f"[*] 服务运行地址: {url}")
    print("[*] 正在自动为您打开默认浏览器...")
    print("[*] 提示: 请保持此窗口运行；使用完毕后可直接关闭本窗口。")
    print("================================================================================\n")

    # 自动在默认浏览器中打开页面
    try:
        webbrowser.open(url)
    except Exception as e:
        print(f"自动打开浏览器失败，请手动访问: {url} ({e})")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止服务...")
        httpd.server_close()


if __name__ == '__main__':
    main()
