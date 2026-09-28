# -*- coding: utf-8 -*-
"""对比 3000 / 3100 的 JS bundle 是否包含计划功能标记"""
import re, urllib.request

for port in (3000, 3100):
    try:
        html = urllib.request.urlopen(f"http://localhost:{port}/", timeout=8).read().decode("utf-8", "ignore")
    except Exception as e:
        print(port, "html err", e); continue
    js_urls = re.findall(r'src="([^"]*?/_next/[^"]+\.js)"', html)
    if not js_urls:
        # Next 可能内联或路径不同
        js_urls = re.findall(r'href="([^"]*?/_next/[^"]+\.js)"', html)
    found = {"formatHint": False, "topic": False, "attach": False, "planCard": False}
    total = 0
    for u in js_urls:
        full = f"http://localhost:{port}" + u if u.startswith("/") else u
        try:
            js = urllib.request.urlopen(full, timeout=15).read().decode("utf-8", "ignore")
        except Exception:
            continue
        total += len(js)
        if "【格式要求】" in js: found["formatHint"] = True
        if "营销活动" in js: found["topic"] = True
        if "attach-strip" in js: found["attach"] = True
        if "plan-card" in js: found["planCard"] = True
    print(port, "js_total", total, found)
