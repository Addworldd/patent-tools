# -*- coding: utf-8 -*-
"""
PyInstaller 打包脚本 (build_exe.py)

功能：将整个 patent-anno-share 工具打包为独立的 Windows 可执行文件 (.exe)
包含：
  - 核心 Python 代码与脚本库 (scripts/)
  - HTML 渲染模板 (templates/page_local.html)
  - Web 控制面板 (web/index.html)
  - 配置文件 (config.json)
  - Playwright 驱动支持
"""

import os
import sys
import shutil
import subprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_ENTRY = os.path.join(BASE_DIR, "app.py")
DIST_DIR = os.path.join(BASE_DIR, "dist")
BUILD_DIR = os.path.join(BASE_DIR, "build")

# 资源数据包含
datas = [
    (os.path.join(BASE_DIR, "templates"), "templates"),
    (os.path.join(BASE_DIR, "web"), "web"),
    (os.path.join(BASE_DIR, "scripts"), "scripts"),
    (os.path.join(BASE_DIR, "config.json"), "."),
]

def build():
    print("================================================================================")
    print("        正在使用 PyInstaller 打包【智慧芽专利中英对照生成器.exe】...          ")
    print("================================================================================")
    
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name", "PatentAnnoShare",
        "--onedir",
    ]

    # 添加数据文件
    for src, dst in datas:
        if os.path.exists(src):
            cmd.extend(["--add-data", f"{src};{dst}"])

    # 收集 playwright 驱动
    cmd.extend(["--collect-all", "playwright"])
    cmd.extend(["--collect-all", "httpx"])

    cmd.append(APP_ENTRY)

    print(f"[*] 执行命令: {' '.join(cmd)}")
    ret = subprocess.run(cmd, cwd=BASE_DIR)

    if ret.returncode == 0:
        raw_dir = os.path.join(DIST_DIR, "PatentAnnoShare")
        old_exe = os.path.join(raw_dir, "PatentAnnoShare.exe")
        new_exe = os.path.join(raw_dir, "智慧芽专利中英对照生成器.exe")
        if os.path.exists(old_exe):
            try:
                shutil.copy2(old_exe, new_exe)
            except Exception:
                pass

        exe_path = os.path.join(raw_dir, "PatentAnnoShare.exe")
        print("\n================================================================================")
        print("                           [+] 打包成功！                                       ")
        print("================================================================================")
        print(f"[*] 可执行程序路径: {exe_path}")
        print("--------------------------------------------------------------------------------")
        print("【使用方式】:")
        print("  1. 双击 PatentAnnoShare.exe 或 智慧芽专利中英对照生成器.exe 即可打开控制台；")
        print("  2. 在界面中输入分享链接与 DeepSeek API Key，即可一键生成中英对照 HTML；")
        print("  3. 也可在命令行运行：PatentAnnoShare.exe \"<链接>\"")
        print("================================================================================")
    else:
        print("\n[!] 打包失败，请检查上方 PyInstaller 报错信息。")


if __name__ == "__main__":
    build()
