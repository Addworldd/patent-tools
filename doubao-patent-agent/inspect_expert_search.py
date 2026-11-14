import asyncio
import json
import websockets
import urllib.request

async def inspect_expert_page():
    req = urllib.request.Request("http://localhost:9222/json/list")
    with urllib.request.urlopen(req) as r:
        targets = json.loads(r.read().decode())
        
    expert_tab = None
    for t in targets:
        if "analytics.zhihuiya.com" in t.get("url", "") and "field/project" in t.get("url", ""):
            expert_tab = t
            break
            
    if not expert_tab:
        for t in targets:
            if "analytics.zhihuiya.com" in t.get("url", "") and "专家搜索" in t.get("title", ""):
                expert_tab = t
                break

    if not expert_tab:
        print("[-] 未找到专家搜索标签页，寻找所有智慧芽标签页...")
        for t in targets:
            if "analytics.zhihuiya.com" in t.get("url", ""):
                expert_tab = t
                break

    if not expert_tab:
        print("[-] 完全没有智慧芽标签页！")
        return

    print(f"[+] 找到专家搜索标签页: ID={expert_tab['id']} | Title={expert_tab['title']} | URL={expert_tab['url']}")
    ws_url = expert_tab["webSocketDebuggerUrl"]

    async with websockets.connect(ws_url, max_size=50 * 1024 * 1024) as ws:
        async def call_cdp(method, params=None, cid=1):
            await ws.send(json.dumps({"id": cid, "method": method, "params": params or {}}))
            while True:
                r = json.loads(await ws.recv())
                if r.get("id") == cid:
                    return r

        # 深入探查专家搜索页面的 DOM：
        # 1. 查找项目名称是否有 P2026073CN1-P
        # 2. 查找输入框（textarea, Monaco, CodeMirror, contenteditable 等）
        # 3. 查找执行按钮、历史检索式表格（包含 #1, #2 编号的列表）
        probe_js = """
        (() => {
            const bodyText = document.body.innerText;
            const hasProject = bodyText.includes('P2026073CN1-P');
            
            // 查找所有可能的输入框
            const inputs = Array.from(document.querySelectorAll('textarea, input, [contenteditable="true"]')).map(el => ({
                tag: el.tagName,
                className: el.className,
                id: el.id,
                placeholder: el.placeholder || '',
                value: (el.value || el.innerText || '').slice(0, 100),
                rect: el.getBoundingClientRect()
            })).filter(x => x.rect.width > 0 && x.rect.height > 0);

            // 查找按钮
            const buttons = Array.from(document.querySelectorAll('button, [role="button"], a')).map(el => ({
                text: el.innerText.trim(),
                className: el.className,
                rect: el.getBoundingClientRect()
            })).filter(b => b.rect.width > 0 && (
                b.text.includes('检') || b.text.includes('查') || b.text.includes('执') || 
                b.text.includes('搜') || b.text.includes('项') || b.text.includes('提')
            ));

            // 查找包含 #1, #2 或 检索式表格的区域
            const historyRows = Array.from(document.querySelectorAll('tr, .history-row, .table-row, [data-row-key]')).map(el => el.innerText.trim()).filter(t => /#\d+/.test(t) || t.includes('TAC:'));

            // 页面标题和当前 URL
            return {
                title: document.title,
                url: location.href,
                hasProject,
                inputsCount: inputs.length,
                inputs: inputs.slice(0, 5),
                buttons: buttons.slice(0, 8),
                historySample: historyRows.slice(0, 5),
                bodySample: bodyText.slice(0, 1500)
            };
        })()
        """
        res = await call_cdp("Runtime.evaluate", {"expression": probe_js, "returnByValue": True}, cid=1)
        val = res.get("result", {}).get("result", {}).get("value", {})
        
        with open("expert_page_probe.json", "w", encoding="utf-8") as f:
            json.dump(val, f, ensure_ascii=False, indent=2)

        print("[+] 页面探针探测完成！")
        print(f"    当前页面: {val.get('title')}")
        print(f"    是否检测到项目 'P2026073CN1-P': {val.get('hasProject')}")
        print(f"    输入框数量: {val.get('inputsCount')}")
        print(f"    按钮样例: {[b['text'] for b in val.get('buttons', [])]}")
        print(f"    历史检索式样例: {val.get('historySample')}")

if __name__ == "__main__":
    asyncio.run(inspect_expert_page())
