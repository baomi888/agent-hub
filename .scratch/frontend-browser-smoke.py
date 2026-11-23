import json
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
session=dict(id="smoke",title="Smoke session",kb_id=None,created_at=1,updated_at=1)
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True,executable_path=str(Path.home()/"AppData/Local/ms-playwright/chromium-1223/chrome-win64/chrome.exe"))
 for mode in ["stop","create","late"]:
  ctx=browser.new_context();ctx.add_init_script("localStorage.setItem('baomi.guide.v1.smoke-user', '1'); localStorage.setItem('baomi.announce.v1.smoke-user.2026-11-d', '1')");page=ctx.new_page();errors=[];calls=[];state=dict(details=0,stopped=False,created=False)
  page.on("pageerror",lambda e:errors.append(str(e)))
  def handle(route):
   req=route.request;path=req.url.split("/api/",1)[1].split("?",1)[0].strip("/");calls.append((req.method,path,req.url));body={}
   if path=="auth/me":body=dict(user=dict(id="smoke-user",username="smoke"),quota={})
   elif path=="auth/config":body=dict(allow_signup=False,require_email=False,email_ready=False)
   elif path=="sessions" and req.method=="POST":state["created"]=True;body=session
   elif path=="sessions":body=dict(sessions=[] if mode in ["create","late"] and not state["created"] else [{**session,"generating":mode not in ["create","late"] and not state["stopped"]}])
   elif path=="sessions/smoke":
    state["details"]+=1;ready=mode=="refresh" or mode=="pending" and state["details"]>=3
    body={**session,"active_run":None if state["stopped"] else dict(message_id=42 if ready else None,started_at=1),"messages":[dict(id=42,role="assistant",content="Recovered history",status="streaming")] if ready else []}
    if mode=="late" and state["stopped"]:body={**session,"active_run":None,"messages":[dict(id=42,role="assistant",content="完成回答"*500,status="done")]}
   elif path.startswith("chat/stream"):
    text="完成回答"*500 if mode=="late" else "Fresh answer" if mode=="create" else "Recovered history"
    events=[("meta",dict(message_id=42,started_at=1)),("token",dict(cursor=1,text=text)),("replay_end",{}),("done",dict(cursor=2,message_id=42,answer=text,status="done"))]
    route.fulfill(status=200,content_type="text/event-stream",body="".join("event: "+e+"\ndata: "+json.dumps(d)+"\n\n" for e,d in events));return
   elif path=="chat/smoke/stop":state["stopped"]=True;body=dict(ok=True)
   elif path in ["kb","kb/public","kb/favorites"]:body=dict(kbs=[],favorites=[])
   elif path=="defaults":body=dict(top_k=3,chunk_size=500,chunk_overlap=50)
   elif "pending" in path:body=dict(proposal=None)
   else:body=dict(items=[],notices=[],announcements=[],unread=0,count=0)
   route.fulfill(status=200,content_type="application/json",body=json.dumps(body))
  page.route("**/api/**",handle);page.goto("http://localhost:3108",wait_until="domcontentloaded",timeout=20000)
  try:page.wait_for_load_state("networkidle",timeout=5000)
  except Exception:print("networkidle bounded wait reached",mode,"requests",len(calls),flush=True)
  page.locator(".zs-boot-splash").wait_for(state="detached",timeout=10000)
  print(mode,"DOM",page.locator("body").inner_text()[:350],flush=True)
  if mode in ["create","late"]:
   page.get_by_placeholder("随便问点什么…").fill("Hello smoke");page.get_by_role("button",name="发送",exact=True).click()
   if mode=="create":expect(page.get_by_text("Fresh answer",exact=True)).to_be_visible(timeout=15000)
   else:
    page.get_by_role("button",name="停止生成，截断当前输出",exact=True).click(timeout=10000)
    expect(page.get_by_text("完成回答"*500,exact=True)).to_be_visible(timeout=15000)
    assert page.get_by_text("已停止生成",exact=True).count()==0
    assert state["stopped"]
   assert sum(m=="POST" and path=="chat/stream" for m,path,_ in calls)==1
  elif mode=="stop":
   page.get_by_role("button",name="停止生成，截断当前输出",exact=True).click()
   page.wait_for_timeout(500)
   assert state["stopped"]
  else:
   expect(page.get_by_text("Recovered history",exact=True)).to_be_visible(timeout=15000)
   if mode=="pending":assert state["details"]>=3
   assert any(m=="GET" and "message_id=42" in u for m,path,u in calls if path.startswith("chat/stream"))
   assert not any(m=="POST" and path=="chat/stream" for m,path,_ in calls)
  assert not errors,errors
  print("PASS",mode,"details",state["details"],"stream calls",[(m,path) for m,path,_ in calls if path.startswith("chat/")],flush=True);ctx.close()
 browser.close()

