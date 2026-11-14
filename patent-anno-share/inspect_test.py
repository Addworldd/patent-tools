import asyncio, sys, json
sys.path.insert(0, r"C:\Users\Administrator\.workbuddy\skills\patent-anno-share\scripts")
from fetch_share import get_browser_executable
from playwright.async_api import async_playwright

url = "https://analytics.zhihuiya.com/patent-view/image?patentId=ccc6be96-646c-4d03-9c8a-d12398500eb1&signature=Gw7rSyCPHBxrY0qOpzYrg7t4GDs0oBmLfdu0sPBHsLE%3D&shareFrom=VIEW&date=20261103T040924Z&expire=94608000&shareId=725CG56768D85CC6B5C8E660E706F32E&version=1.0"

async def inspect():
    browser_path = get_browser_executable()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, executable_path=browser_path)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        
        captured = {}
        async def on_resp(resp):
            u = resp.url
            if "zhihuiya.com" in u and any(x in u.lower() for x in [".png", ".jpg", ".jpeg"]):
                try:
                    b = await resp.body()
                    if b and len(b) > 2000:
                        captured[u.split("?")[0]] = len(b)
                except Exception:
                    pass
        
        page.on("response", on_resp)
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)
        
        print("Captured images count:", len(captured))
        for k, v in captured.items():
            print(f"  {k} -> {v} bytes")
            
        await browser.close()

asyncio.run(inspect())
