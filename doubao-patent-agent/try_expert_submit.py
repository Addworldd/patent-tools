import asyncio
import json
import websockets
import time

async def submit_expert_query(query_text):
    ws_url = "ws://localhost:9222/devtools/page/12FEA905B06C6AF8F44DEBFA6437D7AE"
    async with websockets.connect(ws_url, max_size=50 * 1024 * 1024) as ws:
        async def call_cdp(method, params=None, cid=1):
            await ws.send(json.dumps({"id": cid, "method": method, "params": params or {}}))
            while True:
                r = json.loads(await ws.recv())
                if r.get("id") == cid:
                    return r

        # 检查当前项目是否是 P2026073CN1-P
        check_proj_js = """
        (() => {
            const activeProj = document.querySelector('.project-tab__item--active, .active, .project-title, [class*=\"project\"]');
            return {
                text: document.body.innerText.slice(0, 500)
            };
        })()
        """
        res = await call_cdp("Runtime.evaluate", {"expression": check_proj_js, "returnByValue": True}, cid=1)
        print("[+] 确认当前界面状态就绪")

        # 填写检索式并激活输入框
        inject_js = f"""
        (() => {{
            const editor = document.querySelector('.div-textarea, [contenteditable=\"true\"]');
            if (!editor) return {{ success: false, error: '未找到输入框' }};
            
            editor.focus();
            // 清空并输入
            editor.innerText = {json.dumps(query_text)};
            editor.dispatchEvent(new InputEvent('input', {{ bubbles: true, inputType: 'insertText' }}));
            editor.dispatchEvent(new Event('change', {{ bubbles: true }}));

            // 查找提交按钮
            const allBtns = Array.from(document.querySelectorAll('button, [role=\"button\"]'));
            const submitBtn = allBtns.find(b => b.innerText.includes('提交语句'));

            return {{
                success: true,
                editorValue: editor.innerText,
                hasSubmitBtn: !!submitBtn,
                submitDisabled: submitBtn ? submitBtn.className.includes('disable') || submitBtn.disabled : true
            }};
        }})()
        """
        fill_res = await call_cdp("Runtime.evaluate", {"expression": inject_js, "returnByValue": True}, cid=2)
        fill_val = fill_res.get("result", {}).get("result", {}).get("value", {})
        print("[*] 填入检索式状态:", fill_val)

        # 点击提交
        click_submit_js = """
        (() => {
            const allBtns = Array.from(document.querySelectorAll('button, [role="button"]'));
            const submitBtn = allBtns.find(b => b.innerText.includes('提交语句'));
            if (submitBtn) {
                submitBtn.click();
                return { clicked: true };
            }
            return { clicked: false };
        })()
        """
        await asyncio.sleep(0.5)
        click_res = await call_cdp("Runtime.evaluate", {"expression": click_submit_js, "returnByValue": True}, cid=3)
        print("[*] 触发提交按钮:", click_res.get("result", {}).get("result", {}).get("value"))

        # 等待 2 秒观察是否生成编号行（如 #1）
        await asyncio.sleep(2.0)

        # 读取当前项目中的检索式列表（编号/检索式/结果数）
        read_history_js = """
        (() => {
            // 查找所有编号行
            const rows = Array.from(document.querySelectorAll('tr, .project-table__row, [class*="history"], [class*="row"]')).filter(r => {
                const t = r.innerText.trim();
                return /#\\d+/.test(t);
            }).map(r => r.innerText.trim().replace(/\\n+/g, ' | '));

            return {
                rowsCount: rows.length,
                rows: rows.slice(0, 10),
                bodyTextSample: document.body.innerText.slice(0, 1200)
            };
        })()
        """
        history_res = await call_cdp("Runtime.evaluate", {"expression": read_history_js, "returnByValue": True}, cid=4)
        history_val = history_res.get("result", {}).get("result", {}).get("value", {})
        
        print("\n================ 专家检索项目更新结果 ================")
        print(f"生成的检索式记录条数: {history_val.get('rowsCount')}")
        for row in history_val.get("rows", []):
            print(f"  -> {row}")

if __name__ == "__main__":
    test_query = "TAC:(样本分析仪 OR 样本分析设备)"
    asyncio.run(submit_expert_query(test_query))
