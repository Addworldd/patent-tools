import asyncio, sys
sys.path.insert(0, r"C:\Users\Administrator\.workbuddy\skills\patent-anno-share\scripts")
from fetch_share import get_browser_executable
from playwright.async_api import async_playwright

url = "https://analytics.zhihuiya.com/patent-view/image?patentId=66193f35-d936-4bd1-802f-ab539e7965d7&signature=ssdFdt6wE2qQUQAF51M2UHZz04ieR4Rvu6ZPC%2BRS%2Fm8%3D&shareFrom=VIEW&date=20261103T055853Z&expire=94608000&shareId=725CG56768D85CC6B5C8E660E706F32E&version=1.0"

async def examine():
    browser_path = get_browser_executable()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, executable_path=browser_path)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)
        
        info = await page.evaluate(r"""() => {
            const imgs = Array.from(document.querySelectorAll('.thum-mainsection-left img, img.src-img, img.image-img'))
                .map((img, i) => ({
                    idx: i,
                    src: img.src,
                    w: img.naturalWidth,
                    h: img.naturalHeight,
                    alt: img.alt,
                    title: img.title
                }));
            return imgs;
        }""")
        print("Total thumb images in page:", len(info))
        for item in info:
            print(" ", item)
        await browser.close()

asyncio.run(examine())
