import asyncio
import json
import websockets

async def submit_query():
    query_text = "TAC:(样本分析仪 OR 样本分析设备)"
    ws_url = "ws://localhost:9222/devtools/page/12FEA905B06C6AF8F44DEBFA6437D7AE"
    async with websockets.connect(ws_url) as ws:
        async def call(method, params=None, cid=1):
            await ws.send(json.dumps({"id": cid, "method": method, "params": params or {}}))
            while True:
                r = json.loads(await ws.recv())
                if r.get("id") == cid:
                    return r

        # 1. 模拟在 contenteditable 中输入文字并触发 input
        js_input = """
        (() => {
            const editor = document.querySelector('.div-textarea');
            if (!editor) return { ok: false, err: 'no editor' };
            editor.focus();
            document.execCommand('selectAll', false, null);
            document.execCommand('delete', false, null);
            document.execCommand('insertText', false, 'TAC:(样本分析仪 OR 样本分析设备)');
            return { ok: true, text: editor.innerText };
        })()
        """
        r1 = await call("Runtime.evaluate", {"expression": js_input, "returnByValue": True}, cid=1)
        val1 = r1.get("result", {}).get("result", {}).get("value")
        print("Input step:", val1)

        await asyncio.sleep(0.5)

        # 2. 点击提交语句按钮
        js_click = """
        (() => {
            const btns = Array.from(document.querySelectorAll('button, [role="button"], span, div'));
            const submitBtn = btns.find(b => b.innerText && b.innerText.trim() === '提交语句');
            if (submitBtn) {
                submitBtn.click();
                return { clicked: true, tag: submitBtn.tagName, cls: submitBtn.className };
            }
            return { clicked: false };
        })()
        """
        r2 = await call("Runtime.evaluate", {"expression": js_click, "returnByValue": True}, cid=2)
        val2 = r2.get("result", {}).get("result", {}).get("value")
        print("Click step:", val2)

        # 等待 3 秒观察生成
        await asyncio.sleep(3.0)

        # 3. 检查是否有新的 # 记录生成
        js_check = """
        (() => {
            const text = document.body.innerText;
            const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);
            const numLines = lines.filter(l => /^#\\d+/.test(l));
            return {
                numLines,
                sampleText: lines.slice(30, 80)
            };
        })()
        """
        r3 = await call("Runtime.evaluate", {"expression": js_check, "returnByValue": True}, cid=3)
        val3 = r3.get("result", {}).get("result", {}).get("value")
        print("\nResult step:", json.dumps(val3, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    asyncio.run(submit_query())
