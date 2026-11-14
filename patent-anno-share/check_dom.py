import asyncio, sys
sys.path.insert(0, r"C:\Users\Administrator\.workbuddy\skills\patent-anno-share\scripts")
from fetch_share import get_browser_executable
from playwright.async_api import async_playwright

url = "https://analytics.zhihuiya.com/patent-view/image?patentId=66193f35-d936-4bd1-802f-ab539e7965d7&signature=ssdFdt6wE2qQUQAF51M2UHZz04ieR4Rvu6ZPC%2BRS%2Fm8%3D&shareFrom=VIEW&date=20261103T055853Z&expire=94608000&shareId=725CG56768D85CC6B5C8E660E706F32E&version=1.0"

async def check():
    browser_path = get_browser_executable()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, executable_path=browser_path)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)
        await page.screenshot(path="us4191646a_page.png")
        
        details = await page.evaluate(r"""() => {
            const allText = document.body.innerText;
            const paginationMatches = allText.match(/\d+\s*\/\s*\d+/g);
            const imgs = Array.from(document.querySelectorAll('img')).map(el => ({
                src: el.src,
                cls: el.className
            }));
            const thumbItems = Array.from(document.querySelectorAll('.thum-mainsection-left li, .thum-mainsection-left div, [class*="thumb"]'))
                .filter(el => el.clientHeight > 20)
                .map(el => el.innerText.trim());
            return {
                paginationMatches: paginationMatches,
                imgs: imgs,
                thumbItems: thumbItems,
                textLines: allText.split('\n').filter(l => l.trim().length > 0 && l.trim().length < 50)
            };
        }""")
        print("Pagination:", details["paginationMatches"])
        print("Imgs:", details["imgs"])
        print("ThumbItems:", details["thumbItems"][:20])
        print("TextLines:", details["textLines"][:30])
        await browser.close()

asyncio.run(check())
