import os, json, re

out_dir = r'D:\工作\工作文件\检索\无效和公众意见\27-2-卓立能099结合方式\权27_客户参考版_报告与JQ批注-最新\权27_客户参考版\直接阅览HTML\可迁移'

html_files = [
    ('D1', 'US20160183596A1', 'D1-US20160183596A1.html', 'D1-US20160183596A1.json'),
    ('D2', 'US9386805B2', 'D2-US9386805B2.html', 'D2-US9386805B2.json'),
    ('D3', 'US20160227841A1', 'D3-US20160227841A1.html', 'D3-US20160227841A1.json'),
    ('D4', 'WO2014015461A1', 'D4-WO2014015461A1.html', 'D4-WO2014015461A1.json'),
    ('D5', 'CN202456410U', 'D5-CN202456410U.html', 'D5-CN202456410U.json'),
    ('D6', 'CN104000305A', 'D6-CN104000305A.html', 'D6-CN104000305A.json'),
    ('D7', 'CN102106611A', 'D7-CN102106611A.html', 'D7-CN102106611A.json'),
    ('D8', 'KR2020120007263U', 'D8-KR2020120007263U.html', 'D8-KR2020120007263U.json'),
    ('D9', 'CN204232296U', 'D9-CN204232296U.html', 'D9-CN204232296U.json'),
    ('D10', 'WO2016054580A1', 'D10-WO2016054580A1.html', 'D10-WO2016054580A1.json'),
    ('D11', 'CN202697716U', 'D11-CN202697716U.html', 'D11-CN202697716U.json'),
]

print("=== 模拟质检脚本校验 HTML vs JSON 镜像一致性 ===")
all_pass = True

for tag, pn, hname, jname in html_files:
    hpath = os.path.join(out_dir, hname)
    jpath = os.path.join(out_dir, jname)
    
    with open(hpath, 'r', encoding='utf-8') as f:
        html = f.read()
    with open(jpath, 'r', encoding='utf-8') as f:
        jdata = json.load(f)
        
    m_data = re.search(r'const DATA = (\{.*?\});\s*const DOC_ID', html, re.DOTALL)
    hdata = json.loads(m_data.group(1))
    
    m_marks = re.search(r'window\.__PRELOADED_MARKS__\s*=\s*(\[.*?\]);', html, re.DOTALL)
    marks = json.loads(m_marks.group(1))
    
    # 1. 条目数一致
    item_match = len(hdata['items']) == len(jdata['items'])
    # 2. 中文一致
    zh_match = all(h['zh'] == j['zh'] for h, j in zip(hdata['items'], jdata['items']))
    # 3. 英文一致
    en_match = all(h['en'] == j['en'] for h, j in zip(hdata['items'], jdata['items']))
    
    # 4. 批注切片校验 (bad marks 检查)
    item_map = {it['no']: it for it in hdata['items']}
    bad_marks = []
    for m in marks:
        it = item_map.get(m['no'])
        if not it:
            bad_marks.append((m['key'], 'no not found'))
            continue
        text_field = it['zh'] if m['side'] == 'zh' else it['en']
        sub = text_field[m['s']:m['e']]
        if sub != m['text']:
            bad_marks.append((m['key'], f"expected [{m['text']}] vs actual [{sub}]"))
            
    passed = item_match and zh_match and en_match and (len(bad_marks) == 0)
    if not passed:
        all_pass = False
        print(f"❌ [{tag}] 校验失败: items={item_match}, zh={zh_match}, en={en_match}, bad_marks={len(bad_marks)}")
    else:
        print(f"✔ [{tag}] 100% PASS: 条目={len(hdata['items'])}, 批注={len(marks)}, bad_marks=0, 中英完全镜像")

print("-" * 70)
print(f"整体质检结果: {'全部通过 🎉' if all_pass else '存在未通过项目 ❌'}")
