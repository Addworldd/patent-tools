import sys
from playwright.sync_api import sync_playwright

def test_connection():
    print("Connecting to Doubao browser over CDP port 9222...")
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp("http://localhost:9222")
        except Exception as e:
            print(f"FAILED to connect: {e}")
            return
            
        print("Successfully connected!")
        contexts = browser.contexts
        print(f"Contexts count: {len(contexts)}")
        
        ctx = contexts[0]
        pages = ctx.pages
        print(f"Currently open tabs count: {len(pages)}")
        for idx, page in enumerate(pages):
            try:
                print(f"  [{idx + 1}] Title: {page.title()} | URL: {page.url}")
            except Exception:
                print(f"  [{idx + 1}] (Unable to read title/url)")
                
        # 创建 Agent 独立页面测试
        print("\nCreating a dedicated Agent test page...")
        agent_page = ctx.new_page()
        print(f"Agent test page opened! URL: {agent_page.url}")
        
        # 导航到一个轻量页面，比如 about:blank 并设置标题
        agent_page.evaluate("document.title = '【Agent专属专利助手工作窗口】'")
        print("Page title set to '【Agent专属专利助手工作窗口】'. Check your Doubao browser tabs!")
        
        # 等待 3 秒观察，然后测试关闭该页面
        agent_page.wait_for_timeout(3000)
        agent_page.close()
        print("Agent test page closed cleanly. Your original tabs are intact!")

if __name__ == "__main__":
    test_connection()
