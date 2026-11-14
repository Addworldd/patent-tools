import asyncio
import json
import websockets

async def submit_combination():
    query_text = "S1 AND TAC:(流水线 OR 自动化)"
    ws_url = "ws://localhost:9222/devtools/page/12FEA905B06C6AF8F44DEBFA6437D7AE"
    async with websockets.connect(ws_url) as ws:
        async def call(method, params=None, cid=1):
            await ws.send(json.dumps({"id": cid, "method": method, "params": params or {}}))
            while True:
                r = json.loads(await ws.recv())
                if r.get("id") == cid:
                    return r

        # 1. 模拟在 contenteditable 中输入组合语句
        js_input = f"""
        (() => {{
            const editor = document.querySelector('.div-textarea');
            if (!editor) return {{ ok: false, err: 'no editor' }};
            editor.focus();
            document.execCommand('selectAll', false, null);
            document.execCommand('delete', false, null);
            document.execCommand('insertText', false, '{query_text}');
            return {{ ok: true, text: editor.innerText }};
        }})()
        """
        r1 = await call("Runtime.evaluate", {"expression": js_input, "returnByValue": True}, cid=1)
        print("Input combination step:", r1.get("result", {}).get("result", {}).get("value"))

        await asyncio.sleep(0.5)

        # 2. 点击提交语句按钮
        js_click = """
        (() => {
            const btns = Array.from(document.querySelectorAll('button, [role="button"], span, div'));
            const submitBtn = btns.find(b => b.innerText && b.innerText.trim() === '提交语句');
            if (submitBtn) {
                submitBtn.click();
                return { clicked: true };
            }
            return { clicked: false };
        })()
        """
        r2 = await call("Runtime.evaluate", {"expression": js_click, "returnByValue": True}, cid=2)
        print("Click submit step:", r2.get("result", {}).get("result", {}).get("value"))

        # 等待 3 秒观察生成 S2
        await asyncio.sleep(3.5)

        # 3. 提取所有已生成的检索式记录
        js_read = """
        (() => {
            const text = document.body.innerText;
            const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);
            const idxHeader = lines.indexOf('编号');
            if (idxHeader !== -1) {
                return lines.slice(idxHeader, idxHeader + 25);
            }
            return lines.slice(30, 70);
        })()
        """
        r3 = await call("Runtime.evaluate", {"expression": js_read, "returnByValue": True}, cid=3)
        records = r3.get("result", {}).get("result", {}).get("value", [])
        print("\nAll Expert Records:", json.dumps(records, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    asyncio.run(submit_combination())
