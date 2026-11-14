import asyncio
import json
import websockets
import urllib.parse
import urllib.request
import re
import sys

def strip_html(s):
    if not s: return ""
    if isinstance(s, list): return " ".join([strip_html(x) for x in s if x])
    return re.sub(r'<[^>]+>', ' ', str(s)).strip()

def get_kwic_text(k):
    if not k or k.get("empty"): return "无关键词上下文"
    p = []
    for f in k.get("fields", []):
        for s in f.get("sentences", []):
            if s and s.get("text"):
                p.append(strip_html(s.get("text")))
    return "  …  ".join(p).strip() if p else "无关键词上下文"

async def get_100th_patent():
    query_str = "TA_ALL:((样本 OR 标本) AND (流水线 OR 自动化) AND (分析仪 OR 分析设备)) and PBD:[* TO 20260928]"
    encoded_q = urllib.parse.quote(query_str)
    target_url = f"https://analytics.zhihuiya.com/search/result/pdfimage/1?sort=desc&limit=100&q={encoded_q}"
    
    req = urllib.request.Request("http://localhost:9222/json/list")
    with urllib.request.urlopen(req) as r:
        targets = json.loads(r.read().decode())
        
    ws_url = None
    for t in targets:
        if "analytics.zhihuiya.com" in t.get("url", ""):
            ws_url = t["webSocketDebuggerUrl"]
            break
            
    if not ws_url:
        req_new = urllib.request.Request("http://localhost:9222/json/new?about:blank", method="PUT")
        with urllib.request.urlopen(req_new) as r:
            ws_url = json.loads(r.read().decode())["webSocketDebuggerUrl"]

    async with websockets.connect(ws_url, max_size=100 * 1024 * 1024) as ws:
        async def call_cdp(method, params=None, cid=1):
            await ws.send(json.dumps({"id": cid, "method": method, "params": params or {}}))
            while True:
                r = json.loads(await ws.recv())
                if r.get("id") == cid:
                    return r

        await call_cdp("Page.enable", cid=1)
        await call_cdp("Network.enable", cid=2)

        print("[*] 正在导航（零滚动，纯网络接口拦截）...")
        await call_cdp("Page.navigate", {"url": target_url}, cid=3)

        srp_req_id = None
        srp_body = None
        start_t = asyncio.get_event_loop().time()

        while asyncio.get_event_loop().time() - start_t < 25:
            try:
                resp_str = await asyncio.wait_for(ws.recv(), timeout=2.0)
                msg = json.loads(resp_str)
                method = msg.get("method")
                
                # 步骤 1: 发现 /search/srp/patents 响应头
                if method == "Network.responseReceived":
                    resp = msg.get("params", {}).get("response", {})
                    url = resp.get("url", "")
                    if "/search/srp/patents" in url:
                        srp_req_id = msg.get("params", {}).get("requestId")
                        print(f"[+] 检测到 /search/srp/patents 响应到达 (ReqID: {srp_req_id})，等待传输结束...")

                # 步骤 2: 等待该请求 loadingFinished
                elif method == "Network.loadingFinished":
                    req_id = msg.get("params", {}).get("requestId")
                    if srp_req_id and req_id == srp_req_id:
                        print(f"[+] 数据传输完成 (loadingFinished)！正在提取第 100 篇数据...")
                        body_res = await call_cdp("Network.getResponseBody", {"requestId": srp_req_id}, cid=99)
                        body_text = body_res.get("result", {}).get("body", "")
                        if body_text:
                            srp_body = json.loads(body_text)
                            break
            except asyncio.TimeoutError:
                pass

        if not srp_body:
            print("[-] 未能解析出 srp 响应体")
            return

        patent_data = srp_body.get("data", {}).get("patent_data", [])
        total_fetched = len(patent_data)
        print(f"[+] 成功直接截取整页 {total_fetched} 篇专利的完整 JSON 报文（页面零滚动！）")

        if total_fetched < 100:
            idx = total_fetched
            p100 = patent_data[-1]
            print(f"[!] 实际返回 {total_fetched} 篇，核对第 {total_fetched} 篇")
        else:
            idx = 100
            p100 = patent_data[99]

        pn = p100.get("PN", "未知")
        title = strip_html(p100.get("TITLE") or p100.get("TI"))
        anc = p100.get("ANC") or {}
        assignee = (anc.get("OFFICIAL", []) + anc.get("CN", []) + anc.get("EN", []) + ["未知"])[0]
        pbd = p100.get("PBD", "未知")
        kwic = p100.get("KWIC")
        kwic_text = get_kwic_text(kwic)

        print("\n" + "="*60)
        print(f"🎯 第 {idx} 篇专利提取成功（零滚动·纯网络接口直取）")
        print(f"公开号 (PN): {pn}")
        print(f"专利名称: {title}")
        print(f"申请人: {assignee} | 公开日: {pbd}")
        print("-" * 60)
        print(f"【关键词上下文 (KWIC) 原文】:\n{kwic_text}")
        print("="*60 + "\n")

if __name__ == "__main__":
    asyncio.run(get_100th_patent())
