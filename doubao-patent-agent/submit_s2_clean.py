import asyncio
import json
import websockets
import time

async def submit_s2():
    ws_url = "ws://localhost:9222/devtools/page/12FEA905B06C6AF8F44DEBFA6437D7AE"
    async with websockets.connect(ws_url) as ws:
        async def call(method, params=None, cid=1):
            await ws.send(json.dumps({"id": cid, "method": method, "params": params or {}}))
            while True:
                r = json.loads(await ws.recv())
                if r.get("id") == cid:
                    return r

        # 1. 查找 editor 并设置焦点
        js_focus = """
        (() => {
            const editor = document.querySelector('.div-textarea');
            if (!editor) return false;
            editor.focus();
            // 彻底清空
            editor.innerHTML = '';
            editor.innerText = '';
            return true;
        })()
        """
        await call("Runtime.evaluate", {"expression": js_focus, "returnByValue": True}, cid=1)
        await asyncio.sleep(0.3)

        # 2. 注入 S2 文本
        query_text = "S1 AND TAC:(流水线 OR 自动化)"
        js_set = f"""
        (() => {{
            const editor = document.querySelector('.div-textarea');
            editor.focus();
            document.execCommand('insertText', false, {json.dumps(query_text)});
            editor.dispatchEvent(new Event('input', {{ bubbles: true }}));
            editor.dispatchEvent(new Event('change', {{ bubbles: true }}));
            return editor.innerText;
        }})()
        """
        r_set = await call("Runtime.evaluate", {"expression": js_set, "returnByValue": True}, cid=2)
        print("[1] 文本已注入输入框:", r_set.get("result", {}).get("result", {}).get("value"))

        await asyncio.sleep(0.5)

        # 3. 寻找真实的提交按钮元素 (button 级别) 并点击
        js_click_btn = """
        (() => {
            const buttons = Array.from(document.querySelectorAll('button'));
            const submitBtn = buttons.find(b => b.innerText && b.innerText.includes('提交语句'));
            if (submitBtn) {
                // 如果被禁用，强制激活
                submitBtn.disabled = false;
                submitBtn.classList.remove('common-search-button__search--disable');
                submitBtn.click();
                return { clicked: true, className: submitBtn.className };
            }
            return { clicked: false };
        })()
        """
        r_click = await call("Runtime.evaluate", {"expression": js_click_btn, "returnByValue": True}, cid=3)
        print("[2] 点击真实提交按钮:", r_click.get("result", {}).get("result", {}).get("value"))

        # 4. 同时通过 CDP 原生底层发送真正的 Enter 键盘敲击（双重保险）
        print("[3] 触发底层物理级 Enter 键盘事件...")
        await call("Input.dispatchKeyEvent", {
            "type": "rawKeyDown",
            "windowsVirtualKeyCode": 13,
            "unmodifiedText": "\r",
            "text": "\r"
        }, cid=4)
        await call("Input.dispatchKeyEvent", {
            "type": "keyUp",
            "windowsVirtualKeyCode": 13,
            "unmodifiedText": "\r",
            "text": "\r"
        }, cid=5)

        # 等待 3 秒观察生成 S2
        await asyncio.sleep(3.0)

        # 5. 读取表格中的编号行
        js_read = """
        (() => {
            const text = document.body.innerText;
            const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);
            const sLines = lines.filter(l => /^S\\d+/.test(l));
            return {
                sLines,
                allLinesSnippet: lines.slice(35, 65)
            };
        })()
        """
        r_read = await call("Runtime.evaluate", {"expression": js_read, "returnByValue": True}, cid=6)
        res = r_read.get("result", {}).get("result", {}).get("value", {})
        print("\n[4] 当前项目已记录的所有 Set 编号:")
        print(json.dumps(res.get("sLines"), ensure_ascii=False, indent=2))
        print("\n[5] 周边文本片段:")
        print(json.dumps(res.get("allLinesSnippet"), ensure_ascii=False, indent=2))

if __name__ == "__main__":
    asyncio.run(submit_s2())
