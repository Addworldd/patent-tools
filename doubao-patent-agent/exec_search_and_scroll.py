import json
import urllib.request
import urllib.parse
import asyncio
import websockets
import time
import sys

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
        "apd": it.get("APD", "未知"),
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

async def run_pipeline(query_str):
    t0 = time.time()
    encoded_q = urllib.parse.quote(query_str)
    search_url = f"https://analytics.zhihuiya.com/search/result/pdfimage/1?sort=desc&limit=100&q={encoded_q}"
    
    # 1. 查找现有是否有智慧芽结果标签页，或者新建
    req = urllib.request.Request("http://localhost:9222/json/new?about:blank", method="PUT")
    with urllib.request.urlopen(req) as r:
        tab_info = json.loads(r.read().decode())
    
    tab_id = tab_info["id"]
    ws_url = tab_info["webSocketDebuggerUrl"]

    async with websockets.connect(ws_url, max_size=50 * 1024 * 1024) as ws:
        await cdp_call(ws, "Page.enable", msg_id=1)
        await cdp_call(ws, "Network.enable", msg_id=2)
        
        # 2. 导航到目标检索页面
        await cdp_call(ws, "Page.navigate", {"url": search_url}, msg_id=3)
        
        patents_data = None
        start_time = time.time()
        
        # 3. 截获 API 响应
        while time.time() - start_time < 12:
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

        # 4. 前端自动平滑滚动加载：在豆包浏览器中展现平滑滚动效果
        scroll_js = """
        (() => {
            let y = 0;
            const timer = setInterval(() => {
                y += 600;
                window.scrollTo({ top: y, behavior: 'smooth' });
                if (y >= 3000) clearInterval(timer);
            }, 250);
        })()
        """
        await cdp_call(ws, "Runtime.evaluate", {"expression": scroll_js}, msg_id=105)

        elapsed = time.time() - t0
        
        result_output = {
            "query": query_str,
            "elapsed_seconds": round(elapsed, 2),
            "total_count": 0,
            "group_count": 0,
            "items": []
        }

        if patents_data:
            pd = patents_data.get("data", {}).get("patent_data", [])
            count_info = patents_data.get("data", {}).get("patent_count", {})
            result_output["total_count"] = count_info.get("total_count", len(pd))
            result_output["group_count"] = count_info.get("group_count", len(pd))
            result_output["items"] = [norm_item(x) for x in pd]
        else:
            # 容灾：从已渲染的 DOM 读取文本
            dom_js = "document.body.innerText.slice(0, 5000)"
            res = await cdp_call(ws, "Runtime.evaluate", {"expression": dom_js, "returnByValue": True}, msg_id=106)
            result_output["raw_text"] = res.get("result", {}).get("result", {}).get("value", "")

        # 将结果输出到文件，避免控制台截断
        with open("search_result.json", "w", encoding="utf-8") as f:
            json.dump(result_output, f, ensure_ascii=False, indent=2)

        print(f"DONE|{result_output['elapsed_seconds']}|{result_output['total_count']}|{len(result_output['items'])}")

if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else 'TA_ALL:((样本 OR 标本) AND (流水线 OR 自动化) AND (分析仪 OR 分析设备)) and PBD:[* TO 20260928]'
    asyncio.run(run_pipeline(q))
