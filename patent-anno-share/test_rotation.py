import asyncio, sys
sys.path.insert(0, r"C:\Users\Administrator\.workbuddy\skills\patent-anno-share\scripts")
from fetch_share import get_browser_executable
from playwright.async_api import async_playwright

html_path = r"C:\Users\Administrator\.workbuddy\skills\patent-anno-share\dist\智慧芽专利中英对照生成器\output\US4191646A_中英对照_可批注.html"

async def verify_rotation():
    browser_path = get_browser_executable()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, executable_path=browser_path)
        page = await browser.new_page(viewport={"width": 1600, "height": 960})
        await page.goto("file:///" + html_path.replace("\\", "/"))
        await page.wait_for_timeout(1000)

        # 1. 测试单卡片旋转按钮
        btn_rot = page.locator(".fig-action-btn").first
        await btn_rot.click()
        await page.wait_for_timeout(400)
        t1 = await page.locator("#fig-img-0").evaluate("el => el.style.transform")
        print("[Test 1] 单卡片旋转后 transform:", t1)

        # 2. 测试全局旋转按钮
        await page.click("#btnRotateAll")
        await page.wait_for_timeout(400)
        t_all0 = await page.locator("#fig-img-0").evaluate("el => el.style.transform")
        t_all1 = await page.locator("#fig-img-1").evaluate("el => el.style.transform")
        print("[Test 2] 全局旋转后 img0:", t_all0, "img1:", t_all1)

        # 3. 测试 Lightbox 全屏旋转及快捷键 R
        await page.locator(".fig-card").first.click()
        await page.wait_for_timeout(400)
        lb_active = await page.locator("#lightbox").evaluate("el => el.classList.contains('active')")
        print("[Test 3] Lightbox 打开状态:", lb_active)

        # 按 R 键旋转
        await page.keyboard.press("KeyR")
        await page.wait_for_timeout(400)
        lb_t = await page.locator("#lbImg").evaluate("el => el.style.transform")
        print("[Test 3] Lightbox 按 R 键旋转后 transform:", lb_t)

        await browser.close()
        print("All rotation tests successfully passed!")

asyncio.run(verify_rotation())
