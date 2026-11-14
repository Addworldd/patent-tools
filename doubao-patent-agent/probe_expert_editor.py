import asyncio
import json
import websockets

async def inspect_editor_and_buttons():
    ws_url = "ws://localhost:9222/devtools/page/12FEA905B06C6AF8F44DEBFA6437D7AE"
    async with websockets.connect(ws_url) as ws:
        js = """
        (() => {
            const editor = document.querySelector('.div-textarea, .editor-container');
            const allElements = Array.from(document.querySelectorAll('*'));
            
            // 找到包含 '提交语句' 的可点击元素
            const submitTargets = allElements.filter(el => {
                return (el.tagName === 'BUTTON' || el.getAttribute('role') === 'button' || el.className.includes('btn') || el.className.includes('submit')) && el.innerText.includes('提交');
            }).map(el => ({
                tag: el.tagName,
                cls: el.className,
                text: el.innerText.trim()
            }));

            // 查找实际接收文字输入的容器
            const editable = document.querySelector('[contenteditable="true"]') || editor;

            return {
                hasEditor: !!editor,
                editorTag: editor ? editor.tagName : null,
                editorCls: editor ? editor.className : null,
                isContentEditable: editable ? editable.isContentEditable : false,
                editableCls: editable ? editable.className : null,
                submitTargets
            };
        })()
        """
        msg = {"id": 10, "method": "Runtime.evaluate", "params": {"expression": js, "returnByValue": True}}
        await ws.send(json.dumps(msg))
        while True:
            r = json.loads(await ws.recv())
            if r.get("id") == 10:
                print(json.dumps(r["result"]["result"]["value"], ensure_ascii=False, indent=2))
                break

if __name__ == "__main__":
    asyncio.run(inspect_editor_and_buttons())
