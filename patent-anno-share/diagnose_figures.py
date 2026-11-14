import asyncio, sys, json
sys.path.insert(0, r"C:\Users\Administrator\.workbuddy\skills\patent-anno-share\scripts")
from fetch_share import get_browser_executable
from playwright.async_api import async_playwright

url = "https://analytics.zhihuiya.com/patent-view/image?patentId=66193f35-d936-4bd1-802f-ab539e7965d7&signature=ssdFdt6wE2qQUQAF51M2UHZz04ieR4Rvu6ZPC%2BRS%2Fm8%3D&shareFrom=VIEW&date=20261103T055853Z&expire=94608000&shareId=725CG56768D85CC6B5C8E660E706F32E&version=1.0"

async def diagnose():
    browser_path = get_browser_executable()
    print("[*] 探测到浏览器路径:", browser_path)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, executable_path=browser_path)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        
        all_reqs = []
        async def on_resp(resp):
            u = resp.url
            if any(x in u.lower() for x in ["zhihuiya.com", "image", ".png", ".jpg", ".jpeg"]):
                try:
                    b = await resp.body()
                    all_reqs.append({
                        "url": u,
                        "status": resp.status,
                        "size": len(b) if b else 0
                    })
                except Exception as e:
                    all_reqs.append({"url": u, "status": resp.status, "error": str(e)})

        page.on("response", on_resp)
        print("[*] 打开附图页面...")
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)
        
        # 页面 DOM 检查
        dom_info = await page.evaluate('''() => {
            const imgs = Array.from(document.querySelectorAll('img')).map(img => ({
                src: img.src,
                dataSrc: img.getAttribute('data-src') || '',
                className: img.className,
                width: img.naturalWidth,
                height: img.naturalHeight
            }));
            
            const scrollContainers = Array.from(document.querySelectorAll('*')).filter(el => {
                const s = window.getComputedStyle(el);
                return (s.overflowY === 'auto' || s.overflowY === 'scroll') && el.scrollHeight > el.clientHeight;
            }).map(el => ({
                tag: el.tagName,
                class: el.className,
                scrollHeight: el.scrollHeight,
                clientHeight: el.clientHeight,
                scrollTop: el.scrollTop
            }));

            // 查找任何包含数字或图标题的文本
            const figureLabels = Array.from(document.querySelectorAll('div, span, p, li'))
                .filter(el => el.innerText && /^(?:图|FIG|Figure|FIG\.)\s*\d+/i.test(el.innerText.trim()))
                .map(el => el.innerText.trim())
                .filter((v, i, a) => a.indexOf(v) === i);

            return {
                title: document.title,
                imgsCount: imgs.length,
                scrollContainers: scrollContainers,
                figureLabels: figureLabels.slice(0, 30)
            };
        }''')
        
        print("=== 页面标题 ===:", dom_info["title"])
        print("=== 图片标签数量 ===:", dom_info["imgsCount"])
        print("=== 图标签文字 ===:", dom_info["figureLabels"])
        print("=== 可滚动容器 ===:", dom_info["scrollContainers"])
        
        fig_reqs = [r for r in all_reqs if any(d in r["url"] for d in ["image.zhihuiya.com", "data-fulltext-image", "data-abstract-image"])]
        print(f"=== 附图 CDN 请求数量 (首屏初始捕获): {len(fig_reqs)} ===")
        for r in fig_reqs:
            print("  ", r["url"][:100], "size:", r.get("size"))
            
        # 现在做滚动测试
        print("\n[*] 开始进行滚动测试...")
        for r_i in range(8):
            scroll_res = await page.evaluate('''() => {
                const el = Array.from(document.querySelectorAll('div')).find(e => 
                    /thum-mainsection-left|commen-scroll-loader/.test(String(e.className || '')) && e.scrollHeight > e.clientHeight);
                if (el) {
                    const before = el.scrollTop;
                    el.scrollTop = el.scrollTop + Math.round(el.clientHeight * 0.8);
                    return { scrolled: true, before: before, after: el.scrollTop, max: el.scrollHeight - el.clientHeight };
                }
                const anyScroll = Array.from(document.querySelectorAll('div')).find(e => e.scrollHeight > e.clientHeight + 100 && e.clientHeight > 150);
                if (anyScroll) {
                    const before = anyScroll.scrollTop;
                    anyScroll.scrollTop = anyScroll.scrollTop + Math.round(anyScroll.clientHeight * 0.8);
                    return { scrolled: true, before: before, after: anyScroll.scrollTop, max: anyScroll.scrollHeight - anyScroll.clientHeight };
                }
                window.scrollBy(0, 800);
                return { scrolled: true, window: true };
            }''')
            await page.wait_for_timeout(2000)
            cur_reqs = [r for r in all_reqs if any(d in r["url"] for d in ["image.zhihuiya.com", "data-fulltext-image", "data-abstract-image"])]
            print(f"  第 {r_i+1} 次滚动: {scroll_res} -> 当前捕获附图数: {len(cur_reqs)}")

        await browser.close()

asyncio.run(diagnose())
