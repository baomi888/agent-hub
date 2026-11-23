from pathlib import Path
p=Path(".scratch/frontend-browser-smoke.py");s=p.read_text().replace('["refresh","pending","stop","create"]','["stop","create","late"]')
s=s.replace('localStorage.setItem(\'baomi.guide.v1.smoke-user\', \'1\')','localStorage.setItem(\'baomi.guide.v1.smoke-user\', \'1\'); localStorage.setItem(\'baomi.announce.v1.smoke-user.2026-11-d\', \'1\')')
s=s.replace('mode=="create" and not state["created"]','mode in ["create","late"] and not state["created"]')
s=s.replace('mode!="create" and not state["stopped"]','mode not in ["create","late"] and not state["stopped"]')
s=s.replace('    body={**session,"active_run":None if state["stopped"] else dict(message_id=42 if ready else None,started_at=1),"messages":[dict(id=42,role="assistant",content="Recovered history",status="streaming")] if ready else []}', '''    body={**session,"active_run":None if state["stopped"] else dict(message_id=42 if ready else None,started_at=1),"messages":[dict(id=42,role="assistant",content="Recovered history",status="streaming")] if ready else []}
    if mode=="late" and state["stopped"]:body={**session,"active_run":None,"messages":[dict(id=42,role="assistant",content="完成回答"*500,status="done")]}''')
s=s.replace('text="Fresh answer" if mode=="create" else "Recovered history"','text="完成回答"*500 if mode=="late" else "Fresh answer" if mode=="create" else "Recovered history"')
s=s.replace('  print(mode,"DOM"', '  page.locator(".zs-boot-splash").wait_for(state="detached",timeout=10000)\n  print(mode,"DOM"')
s=s.replace('  if mode=="create":','  if mode in ["create","late"]:')
s=s.replace('   expect(page.get_by_text("Fresh answer",exact=True)).to_be_visible(timeout=15000)', '''   if mode=="create":expect(page.get_by_text("Fresh answer",exact=True)).to_be_visible(timeout=15000)
   else:
    page.get_by_role("button",name="停止生成，截断当前输出",exact=True).click(timeout=10000)
    expect(page.get_by_text("完成回答"*500,exact=True)).to_be_visible(timeout=15000)
    assert page.get_by_text("已停止生成",exact=True).count()==0
    assert state["stopped"]''')
p.write_text(s)
