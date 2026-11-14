import asyncio, json, websockets

async def get_rendered_data():
    ws_url = 'ws://localhost:9222/devtools/page/2EE25872733F1240EB95A170BC54644A'
    async with websockets.connect(ws_url) as ws:
        js = "document.body.innerText.slice(0, 3000)"
        msg = {'id': 101, 'method': 'Runtime.evaluate', 'params': {'expression': js, 'returnByValue': True}}
        await ws.send(json.dumps(msg))
        while True:
            resp_str = await ws.recv()
            data = json.loads(resp_str)
            if data.get('id') == 101:
                text = data.get('result', {}).get('result', {}).get('value', '')
                print("--- 网页已渲染文本内容 ---")
                print(text[:1500])
                break

if __name__ == '__main__':
    asyncio.run(get_rendered_data())
