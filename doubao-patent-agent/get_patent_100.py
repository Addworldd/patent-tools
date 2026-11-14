import asyncio
import json
import websockets

async def fetch_100th_patent():
    tab_id = "BE6D108EFDB1ABB25E80DC072482B336"
    ws_url = f"ws://localhost:9222/devtools/page/{tab_id}"
    
    async with websockets.connect(ws_url, max_size=50 * 1024 * 1024) as ws:
        async def call_cdp(method, params=None, cid=1):
            await ws.send(json.dumps({"id": cid, "method": method, "params": params or {}}))
            while True:
                r = json.loads(await ws.recv())
                if r.get("id") == cid:
                    return r

        # 1. 尝试从页面全局变量查找专利缓存
        probe_cache_js = """
        (() => {
            // 查找 window 下可能的专利列表缓存
            let found = null;
            for (let k of Object.keys(window)) {
                try {
                    let v = window[k];
                    if (v && v.patent_data && Array.isArray(v.patent_data)) return { key: k, count: v.patent_data.length, item100: v.patent_data[99] };
                } catch(e) {}
            }
            return null;
        })()
        """
        cache_res = await call_cdp("Runtime.evaluate", {"expression": probe_cache_js, "returnByValue": True}, cid=1)
        cache_val = cache_res.get("result", {}).get("result", {}).get("value")
        print("Cache probe:", cache_val)

        # 2. 检查 DOM 中目前有多少卡片
        dom_probe_js = """
        (() => {
            // 收集所有卡片
            const items = Array.from(document.querySelectorAll('.table-row, [data-row-key], .table-body-row, .card-item, .list-item'));
            return {
                cards_count: items.length
            };
        })()
        """
        dom_res = await call_cdp("Runtime.evaluate", {"expression": dom_probe_js, "returnByValue": True}, cid=2)
        print("DOM cards currently:", dom_res.get("result", {}).get("result", {}).get("value"))

        # 3. 如果没到 100，让页面持续滚动触底触发加载
        scroll_to_100_js = """
        (async () => {
            let rounds = 0;
            while (rounds < 30) {
                window.scrollBy(0, 3000);
                await new Promise(r => setTimeout(r, 300));
                rounds++;
            }
            return true;
        })()
        """
        print("[*] 正在滚动页面以加载后续卡片...")
        await call_cdp("Runtime.evaluate", {"expression": scroll_to_100_js, "awaitPromise": True}, cid=3)

        # 4. 再次获取页面卡片及第 100 篇内容
        extract_js = """
        (() => {
            // 查找包含序号或者具体卡片的元素
            const fullText = document.body.innerText;
            return {
                textLength: fullText.length
            };
        })()
        """
        ext_res = await call_cdp("Runtime.evaluate", {"expression": extract_js, "returnByValue": True}, cid=4)
        print("Scrolled DOM info:", ext_res.get("result", {}).get("result", {}).get("value"))

if __name__ == "__main__":
    asyncio.run(fetch_100th_patent())
