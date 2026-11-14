import asyncio
import json
import websockets

async def check():
    ws_url = "ws://localhost:9222/devtools/page/12FEA905B06C6AF8F44DEBFA6437D7AE"
    async with websockets.connect(ws_url) as ws:
        msg = {"id": 1, "method": "Runtime.evaluate", "params": {"expression": "document.querySelector('.div-textarea').outerHTML", "returnByValue": True}}
        await ws.send(json.dumps(msg))
        while True:
            r = json.loads(await ws.recv())
            if r.get("id") == 1:
                print("Result:", json.dumps(r, ensure_ascii=False, indent=2))
                break

asyncio.run(check())
