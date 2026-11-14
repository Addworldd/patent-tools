import json
import urllib.request
import asyncio
import websockets
import sys

async def send_cdp_cmd(ws, method, params=None, msg_id=1):
    msg = {"id": msg_id, "method": method, "params": params or {}}
    await ws.send(json.dumps(msg))
    while True:
        resp = await ws.recv()
        data = json.loads(resp)
        if data.get("id") == msg_id:
            return data

async def inspect_zhihuiya():
    req = urllib.request.Request("http://localhost:9222/json/list")
    with urllib.request.urlopen(req) as r:
        targets = json.loads(r.read().decode())
        
    page_target = None
    for t in targets:
        if "analytics.zhihuiya.com" in t.get("url", ""):
            page_target = t
            break
            
    if not page_target:
        print("No Zhihuiya tab found!")
        return

    print("Found Zhihuiya tab:", page_target["title"], page_target["id"])
    ws_url = page_target["webSocketDebuggerUrl"]
    
    async with websockets.connect(ws_url) as ws:
        # 探测当前页面元素
        probe_js = """
        (() => {
            const textareas = Array.from(document.querySelectorAll('textarea')).map(t => ({
                placeholder: t.placeholder,
                className: t.className,
                id: t.id,
                value: t.value
            }));
            const buttons = Array.from(document.querySelectorAll('button')).map(b => ({
                text: b.innerText.trim(),
                className: b.className
            })).filter(b => b.text.includes('检') || b.text.includes('查') || b.text.includes('搜'));
            return { textareas, buttons, url: location.href, title: document.title };
        })()
        """
        res = await send_cdp_cmd(ws, "Runtime.evaluate", {
            "expression": probe_js,
            "returnByValue": True
        })
        print("DOM Probe Result:", json.dumps(res.get("result", {}).get("value", {}), ensure_ascii=False, indent=2))

if __name__ == "__main__":
    asyncio.run(inspect_zhihuiya())
