@echo off
title 智慧芽专利高亮截图与附图生成器
echo ================================================================================
echo         智慧芽专利高亮截图与附图生成器 (PatSnap Visual Tool)
echo ================================================================================
echo.
echo [*] 正在启动可视化界面并在默认浏览器中打开...
cd /d "C:\Users\Administrator\.workbuddy\skills\zhihuiya-highlight-shot"
"C:\ProgramData\miniconda3\python.exe" "C:\Users\Administrator\.workbuddy\skills\zhihuiya-highlight-shot\gui_server.py"
if errorlevel 1 pause
