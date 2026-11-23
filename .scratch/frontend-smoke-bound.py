from pathlib import Path
p=Path(".scratch/frontend-browser-smoke.py");s=p.read_text()
s=s.replace('ctx=browser.new_context();page=ctx.new_page();', 'ctx=browser.new_context();ctx.add_init_script("localStorage.setItem(\'baomi.guide.v1.smoke-user\', \'1\')");page=ctx.new_page();')
s=s.replace('page.goto("http://localhost:3108",wait_until="networkidle",timeout=120000)', '''page.goto("http://localhost:3108",wait_until="domcontentloaded",timeout=20000)
  try:page.wait_for_load_state("networkidle",timeout=5000)
  except Exception:print("networkidle bounded wait reached",mode,"requests",len(calls),flush=True)''')
p.write_text(s)
