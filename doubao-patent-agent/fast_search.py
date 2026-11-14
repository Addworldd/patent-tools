import json
import urllib.request
import urllib.parse
import asyncio
import websockets
import time

def strip_html(s):
    if not s: return ""
    if isinstance(s, list): return " ".join([strip_html(x) for x in s if x])
    import re
    return re.sub(r'<[^>]+>', ' ', str(s)).strip()

def norm_item(it):
    anc = it.get("ANC") or {}
    official = [x for x in anc.get("OFFICIAL", []) if x]
    cn = [x for x in anc.get("CN", []) if x]
    en = [x for x in anc.get("EN", []) if x]
    assignee = (official + cn + en + ["未知"])[0]

    return {
        "pn": it.get("PN", "未知"),
        "title": strip_html(it.get("TITLE") or it.get("TI")),
        "assignee": assignee,
        "pbd": it.get("PBD", "未知"),
        "ipc": " ".join(it.get("IPC", [])) if isinstance(it.get("IPC"), list) else it.get("IPC", ""),
        "abstract": strip_html(it.get("ABST") or it.get("AB")),
        "tech_problem": strip_html(it.get("AI_TECHNICAL_PROBLEM")),
        "tech_method": strip_html(it.get("AI_TECHNICAL_METHOD")),
        "tech_benefit": strip_html(it.get("AI_TECHNICAL_BENEFIT")),
    }

async def cdp_call(ws, method, params=None, msg_id=1):
    msg = {"id": msg_id, "method": method, "params": params or {}}
    await ws.send(json.dumps(msg))
    while True:
        resp = await ws.recv()
        data = json.loads(resp)
        if data.get("id") == msg_id:
            return data

async def quick_search_and_scroll(query_str, auto_scroll=True):
    t0 = time.time()
    encoded_q = urllib.parse.quote(query_str)
    search_url = f"https://analytics.zhihuiya.com/search/result/pdfimage/1?sort=desc&limit=100&q={encoded_q}"
    
    # 1. 创建全新标签页
    req = urllib.request.Request("http://localhost:9222/json/new?about:blank", method="PUT")
    with urllib.request.urlopen(req) as r:
        tab_info = json.loads(r.read().decode())
    
    tab_id = tab_info["id"]
    ws_url = tab_info["webSocketDebuggerUrl"]

    async with websockets.connect(ws_url, max_size=50 * 1024 * 1024) as ws:
        await cdp_call(ws, "Page.enable", msg_id=1)
        await cdp_call(ws, "Network.enable", msg_id=2)
        
        # 导航
        await cdp_call(ws, "Page.navigate", {"url": search_url}, msg_id=3)
        
        patents_data = None
        start_time = time.time()
        
        # 监听 srp API 响应
        while time.time() - start_time < 8:
            try:
                resp_str = await asyncio.wait_for(ws.recv(), timeout=1.0)
                msg = json.loads(resp_str)
                if msg.get("method") == "Network.responseReceived":
                    resp = msg.get("params", {}).get("response", {})
                    req_id = msg.get("params", {}).get("requestId")
                    if "/search/srp/patents" in resp.get("url", ""):
                        body_res = await cdp_call(ws, "Network.getResponseBody", {"requestId": req_id}, msg_id=99)
                        body_text = body_res.get("result", {}).get("body", "")
                        if body_text:
                            patents_data = json.loads(body_text)
                            break
            except asyncio.TimeoutError:
                pass

        # 触发前端平滑自动滚动：让用户肉眼看到页面自动滚屏加载卡片
        if auto_scroll:
            scroll_js = """
            (() => {
                let y = 0;
                const timer = setInterval(() => {
                    y += 800;
                    window.scrollTo({ top: y, behavior: 'smooth' });
                    if (y >= 4000) clearInterval(timer);
                }, 200);
            })()
            """
            await cdp_call(ws, "Runtime.evaluate", {"expression": scroll_js}, msg_id=105)

        elapsed = time.time() - t0
        if patents_data:
            pd = patents_data.get("data", {}).get("patent_data", [])
            count_info = patents_data.get("data", {}).get("patent_count", {})
            total = count_info.get("total_count", len(pd))
            items = [norm_item(x) for x in pd]
            print(f"⚡ [极速完成] 耗时: {elapsed:.2f} 秒！直取到本页完整 {len(items)} 篇专利数据（总库命中 {total} 件）！")
            return items
        else:
            print(f"[-] 耗时: {elapsed:.2f} 秒，未能直取 API")
            return []

if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "TAC:(样本分析仪) AND PBD:[20240101 TO 20241231]"
    items = asyncio.run(quick_search_and_scroll(q))
    if items:
        print("\n前 5 篇速览:")
        for i, it in enumerate(items[:5], 1):
            print(f"{i}. [{it['pn']}] {it['title']} - {it['assignee']} ({it['pbd']})")
