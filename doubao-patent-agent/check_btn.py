import asyncio
import json
import websockets

async def check():
    ws_url = "ws://localhost:9222/devtools/page/12FEA905B06C6AF8F44DEBFA6437D7AE"
    async with websockets.connect(ws_url) as ws:
        js = """
        (() => {
            const btns = Array.from(document.querySelectorAll('*')).filter(el => {
                const t = el.innerText ? el.innerText.trim() : '';
                return t.includes('提交语句') && el.children.length <= 2;
            }).map(el => el.outerHTML);
            return btns;
        })()
        """
        msg = {"id": 2, "method": "Runtime.evaluate", "params": {"expression": js, "returnByValue": True}}
        await ws.send(json.dumps(msg))
        while True:
            r = json.loads(await ws.recv())
            if r.get("id") == 2:
                print("Submit button:", json.dumps(r, ensure_ascii=False, indent=2))
                break

asyncio.run(check())
