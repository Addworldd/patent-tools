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

async def run_search(query_str):
    encoded_q = urllib.parse.quote(query_str)
    search_url = f"https://analytics.zhihuiya.com/search/result/pdfimage/1?sort=desc&limit=100&q={encoded_q}"
    
    req = urllib.request.Request("http://localhost:9222/json/new?about:blank", method="PUT")
    with urllib.request.urlopen(req) as r:
        tab_info = json.loads(r.read().decode())
    
    tab_id = tab_info["id"]
    ws_url = tab_info["webSocketDebuggerUrl"]

    try:
        async with websockets.connect(ws_url, max_size=50 * 1024 * 1024) as ws:
            await cdp_call(ws, "Page.enable", msg_id=1)
            await cdp_call(ws, "Network.enable", msg_id=2)
            await cdp_call(ws, "Page.navigate", {"url": search_url}, msg_id=3)
            
            patents_data = None
            start_time = time.time()
            while time.time() - start_time < 15:
                try:
                    resp_str = await asyncio.wait_for(ws.recv(), timeout=2.0)
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

            if not patents_data:
                print("[-] 未能捕获到 SRP 响应")
                return []

            pd = patents_data.get("data", {}).get("patent_data", [])
            count_info = patents_data.get("data", {}).get("patent_count", {})
            total = count_info.get("total_count", len(pd))
            group = count_info.get("group_count", total)
            
            print(f"📊 智慧芽检索完成 | 命中公开件数: {total} | 同族合并件数: {group}")
            
            clean_list = [norm_item(x) for x in pd]
            return clean_list

    finally:
        pass

if __name__ == "__main__":
    q = "TAC:(样本分析仪) AND PBD:[20240101 TO 20241231]"
    items = asyncio.run(run_search(q))
    print(f"\n================ 提取前 5 篇专利明细 ================")
    for i, it in enumerate(items[:5], 1):
        print(f"\n[{i}] 公开号: {it['pn']}")
        print(f"    专利名称: {it['title']}")
        print(f"    申请人: {it['assignee']} | 公开日: {it['pbd']} | IPC: {it['ipc']}")
        print(f"    摘要: {it['abstract'][:120]}...")
        if it['tech_method']:
            print(f"    技术手段: {it['tech_method'][:100]}...")
