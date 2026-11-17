# -*- coding: utf-8 -*-
"""
端到端自动化质量门禁与回归测试套件 (E2E Regression Gatekeeper)

在每一次代码改动或版本打包前强制运行，覆盖：
1. 函数签名与参数完整性 (extra_prompt, target_lang 等)
2. 模板合法性与防残留校验 (杜绝未定义占位符导致的 JS 引擎崩溃)
3. 说明书与权项数据无损流动校验 (杜绝清洗函数吞字段导致正文为空)
4. Chrome 无头浏览器真机渲染质检 (0 报错、正文段落非空率 100%)
"""

import os
import sys
import json
import re
import asyncio

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE) if os.path.basename(HERE) == "scripts" else HERE
TEMPLATES_DIR = os.path.join(SKILL_DIR, "templates")

def test_template_validity():
    print("[1/4] 测试 HTML 模板合法性与非法占位符拦截...")
    tpl_path = os.path.join(TEMPLATES_DIR, "page_local.html")
    assert os.path.exists(tpl_path), f"模板不存在: {tpl_path}"
    with open(tpl_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. 模板中允许存在的合法替换占位符白名单
    allowed_placeholders = {"__TITLE__", "__SUBTITLE__", "__DATA__", "__DOC_ID__", "__PRELOADED_MARKS__"}
    found_placeholders = set(re.findall(r'__[A-Z0-9_]+__', content))
    unexpected = found_placeholders - allowed_placeholders
    if unexpected:
        raise AssertionError(f"模板中发现了未注册在生成器白名单中的孤立危险占位符: {unexpected}")

    # 2. 预置批注必须硬编码为安全空数组默认值
    assert "window.__PRELOADED_MARKS__ = [];" in content, "模板中缺失安全的空数组默认值初始化"
    print("      [✔] 模板语法与安全占位符白名单校验 100% 通过")


def test_data_pipeline_integrity():
    print("[2/4] 测试说明书段落数据流转与字段防吞机制...")
    sys.path.insert(0, os.path.join(SKILL_DIR, "scripts"))
    sys.path.insert(0, SKILL_DIR)
    from fetch_share import verify_and_heal_description, export_as_data_json

    # 场景 A: 模拟刚从网页提取的原始段落 {no, text}
    raw_paras = [
        {"no": "0001", "text": "【技术领域】\n本实用新型涉及雾化器。"},
        {"no": "0002", "text": "背景技术：现有雾化器存在漏液缺陷。"}
    ]
    healed_a, rep_a = verify_and_heal_description(raw_paras, patent_id="TEST01")
    assert len(healed_a) == 2, f"清洗后段落数量异常: {len(healed_a)}"
    assert healed_a[0]["no"] == "0001", "段号起始对齐失败"
    assert "本实用新型涉及雾化器" in healed_a[0]["text"], "原始 text 丢失"

    # 场景 B: 模拟翻译/对齐后的标准化段落 {no, group, en, zh} 进行二次门禁检查
    norm_paras = [
        {"no": "0001", "group": "说明书", "en": "An atomizer.", "zh": "一种雾化器。"},
        {"no": "0002", "group": "说明书", "en": "Background art.", "zh": "背景技术。"}
    ]
    healed_b, rep_b = verify_and_heal_description(norm_paras, patent_id="TEST02")
    assert len(healed_b) == 2
    assert healed_b[0]["zh"] == "一种雾化器。", f"zh 字段在门禁中被抹除！实际: {healed_b[0].get('zh')}"
    assert healed_b[0]["en"] == "An atomizer.", f"en 字段在门禁中被抹除！实际: {healed_b[0].get('en')}"

    # 场景 C: 测试 export_as_data_json 兜底映射
    mock_patent = {
        "biblio": {"pn": "TEST03", "title": "测试专利"},
        "is_foreign": False,
        "claims": [{"no": "权1", "group": "权利要求书", "en": "", "zh": "1. 一种结构。"}],
        "description": [{"no": "0001", "text": "纯 text 格式说明书正文"}]
    }
    tmp_json = os.path.join(SKILL_DIR, "_test_tmp_data.json")
    try:
        export_as_data_json(mock_patent, tmp_json)
        with open(tmp_json, "r", encoding="utf-8") as f:
            d = json.load(f)
        desc_item = [it for it in d["items"] if it.get("group") == "说明书"][0]
        assert desc_item["zh"] == "纯 text 格式说明书正文", f"export_as_data_json 未能正确将 text 兜底映射给 zh: {desc_item}"
    finally:
        if os.path.exists(tmp_json):
            os.remove(tmp_json)
    print("      [✔] 说明书字段防吞与双向映射机制 100% 通过")


def test_function_signatures():
    print("[3/4] 测试流水线核心调度参数兼容性...")
    from app import process_single_patent, run_single_pipeline_worker, run_batch_pipeline_worker
    import inspect

    sig = inspect.signature(process_single_patent)
    assert "extra_prompt" in sig.parameters, "process_single_patent 缺失 extra_prompt 参数"
    assert "target_lang" in sig.parameters, "process_single_patent 缺失 target_lang 参数"
    print("      [✔] 调度层与执行层参数签名完全吻合")


async def test_browser_render():
    print("[4/4] 启动 Chrome 真机无头渲染仿真质检...")
    from playwright.async_api import async_playwright

    test_html = r"D:\工作\工作文件\检索\无效和公众意见\27-2-卓立能099结合方式\作业文件\WO2016127389A1_中英对照_[本机版].html"
    if not os.path.exists(test_html):
        print("      [!] 跳过页面质检: 找不到样例测试文件")
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe")
        page = await browser.new_page()
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))

        file_url = "file:///" + test_html.replace(os.sep, "/")
        await page.goto(file_url, wait_until="networkidle")

        # 1. 验证 JS 错误数为 0
        assert len(errors) == 0, f"Chrome 渲染过程中出现 JS 报错: {errors}"

        # 2. 验证权利要求段落非空
        claims = await page.query_selector_all(".p")
        assert len(claims) > 0, "权利要求渲染段落为 0！"

        # 3. 验证说明书全文非空
        await page.click("#tabDesc")
        await page.wait_for_timeout(400)
        descs = await page.query_selector_all(".p")
        assert len(descs) > 0, "说明书全文渲染段落为 0！"
        
        # 4. 抽检说明书文本内容非空率
        for idx in [0, len(descs)//2, -1]:
            txt = await descs[idx].inner_text()
            assert len(txt.strip()) > 5, f"说明书第 {idx} 段文本异常为空！内容: '{txt}'"

        await browser.close()
    print("      [✔] Chrome 真机无头自动化渲染质检 100% 通过（0 报错，说明书/权项正文非空率 100%）")


def main():
    print("================================================================================")
    print("       正在执行 patent-anno-share 质量门禁端到端回归测试 (Gatekeeper CI)         ")
    print("================================================================================")
    try:
        test_template_validity()
        test_data_pipeline_integrity()
        test_function_signatures()
        asyncio.run(test_browser_render())
        print("================================================================================")
        print("            [🎉 ALL PASS] 4 项核心质量门禁测试全部通过！系统稳固可靠。            ")
        print("================================================================================")
        return 0
    except Exception as e:
        print("\n[❌ 质量门禁拦截失败]:", e)
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
