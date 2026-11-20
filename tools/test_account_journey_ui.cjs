/* 真正前后端联调；只使用回环测试后端、临时库和本机 SMTP。 */
const {spawn}=require("node:child_process");
const assert=require("node:assert/strict"),path=require("node:path"),os=require("node:os"),fs=require("node:fs");
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||path.join(os.homedir(),".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright"));
const wait=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
 const root=path.resolve(__dirname,".."),children=[];let browser,scenarios=0;
 const start=(exe,args,cwd)=>{
  const p=spawn(exe,args,{cwd,windowsHide:true,env:{...process.env,PYTHONUTF8:"1",NEXT_TELEMETRY_DISABLED:"1"},stdio:["ignore","pipe","pipe"]});
  children.push(p);let log="";p.stdout.on("data",b=>log+=b);p.stderr.on("data",b=>log+=b);return{p,log:()=>log};
 };
 try{
  var back=start(path.join(root,".venv/Scripts/python.exe"),["tools/account_journey_backend.py"],root);
  let healthy=false;
  for(let i=0;i<150;i++){
   if(back.p.exitCode!==null)throw Error(back.log());
   try{if((await fetch("http://127.0.0.1:43131/health")).ok){healthy=true;break;}}catch{}
   await wait(200);
  }
  assert.ok(healthy,back.log());
  const front=start(process.execPath,["node_modules/next/dist/bin/next","start","--hostname","127.0.0.1","--port","43132"],path.join(root,"frontend"));
  for(let i=0;i<100&&!front.log().includes("Ready in");i++){if(front.p.exitCode!==null)throw Error(front.log());await wait(200);}
  assert.ok(front.log().includes("Ready in"),front.log());
  browser=await chromium.launch({headless:true,channel:process.env.ZHISUI_BROWSER_CHANNEL||"msedge"});
  const output=fs.mkdtempSync(path.join(os.tmpdir(),"zhisui-step7-ui-"));
  async function advance(seconds=61,failure=false){
   const response=await fetch("http://127.0.0.1:43131/__test__/control",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({advance:seconds,failure})});
   assert.equal(response.status,200);
  }
  const readCounts=new Map();
  async function code(address){
   const expected=(readCounts.get(address)||0)+1;
   for(let attempt=0;attempt<60;attempt++){
    const response=await fetch("http://127.0.0.1:43131/__test__/mail?address="+encodeURIComponent(address));
    assert.equal(response.status,200);
    const data=await response.json();
    if(data.messages.length>=expected){readCounts.set(address,expected);return data.messages[expected-1].code;}
    await wait(200);
   }
   throw Error("本机SMTP未收到对应的新验证码邮件");
  }
  for(const [width,theme,mobile] of [[1440,"light",false],[390,"dark",true]]){
   const context=await browser.newContext({viewport:{width,height:1000},isMobile:mobile,hasTouch:mobile,serviceWorkers:"block",reducedMotion:"reduce",colorScheme:theme});
   await context.addInitScript(({theme,mobile})=>{
    localStorage.setItem("theme",theme);if(mobile)Object.defineProperty(navigator,"standalone",{get:()=>true});
   },{theme,mobile});
   var page=await context.newPage();const errors=[],loginStatuses=[];let sendCount=0;page.on("pageerror",e=>errors.push(e.message));
   await page.route("**/api/**",async route=>{
    const url=new URL(route.request().url());
    if(url.pathname==="/api/auth/register/email/send")sendCount++;
    const response=await route.fetch({url:"http://127.0.0.1:43131"+url.pathname+url.search,timeout:8000}).catch(error=>{errors.push("FORWARD "+url.pathname+": "+error.message);return null;});
    if(response && url.pathname==="/api/auth/login")loginStatuses.push(response.status());
    if(response)await route.fulfill({response}).catch(error=>errors.push(error.message));else await route.abort().catch(()=>{});
   });
   async function settleOverlays(){
    await page.locator(".zs-boot-splash").waitFor({state:"detached"});
    await page.waitForLoadState("networkidle");
    for(let attempt=0;attempt<3;attempt++){
     const announcement=page.locator(".ann-shell");
     if(await announcement.count()){
      await announcement.locator(".ann-body").focus();
      await page.keyboard.press("End");
      await announcement.getByRole("button",{name:"开始使用",exact:true}).click();
     }
     const guideButton=page.getByRole("button",{name:"跳过",exact:true});
     if(await guideButton.count())await guideButton.click();
     await wait(150);
    }
   }
   const email="journey"+width+"@example.com",next="changed"+width+"@example.com",username="journey_"+width;
   await page.goto("http://127.0.0.1:43132/login");
   await page.locator(".zs-boot-splash").waitFor({state:"detached"});
   await page.getByRole("tab",{name:"注册",exact:true}).click();
   await page.getByLabel("登录名",{exact:true}).fill(username);
   await page.getByLabel("密码",{exact:true}).fill("Journey_pass_9");
   await page.getByLabel("邮箱",{exact:true}).fill(email);
   await advance(0,true);
   await page.getByRole("button",{name:"发送邮箱验证码",exact:true}).dblclick();
   await page.getByRole("alert").filter({hasText:"邮件未发送成功或请求已失效"}).waitFor();
   await advance(61,false);
   await page.getByRole("button",{name:"发送邮箱验证码",exact:true}).dblclick();
   await page.getByLabel("邮箱验证码",{exact:true}).fill(await code(email));
   await page.getByRole("button",{name:"验证邮箱",exact:true}).click();
   await page.getByText("邮箱已验证，请在 5 分钟内完成注册。",{exact:true}).waitFor();
   await page.getByRole("button",{name:"注册并登录",exact:true}).click();
   await page.waitForURL(url=>url.pathname==="/");
   await page.goto("http://127.0.0.1:43132/settings");await settleOverlays();
   const guide=page.getByRole("button",{name:"跳过",exact:true});
   if(await guide.count())await guide.click();
   await page.getByRole("button",{name:"换绑邮箱",exact:true}).click();
   const dialog=page.getByRole("dialog",{name:"换绑安全邮箱"});
   await advance();
   for(const [label,address] of [["原邮箱",email],["新邮箱",next]]){
    const field=dialog.getByRole("group",{name:label,exact:true});
    if(label==="新邮箱")await field.getByLabel("新邮箱",{exact:true}).fill(address);
    await field.getByRole("button",{name:"发送验证码",exact:true}).click();
    await field.getByLabel(label+"验证码",{exact:true}).fill(await code(address));
    await field.getByRole("button",{name:"验证"+label,exact:true}).click();
    await field.getByText(label+"已验证，请在 5 分钟内确认完成。",{exact:true}).waitFor();
   }
   await dialog.getByRole("button",{name:"确认完成",exact:true}).click();
   await page.getByText("安全邮箱已更新",{exact:true}).waitFor();
   await page.screenshot({path:path.join(output,"settings-"+theme+"-"+width+".png"),fullPage:true});
   await page.getByRole("button",{name:"退出登录",exact:true}).click();
   await page.waitForURL(url=>url.pathname==="/");
   await page.getByRole("link",{name:"登录",exact:true}).first().waitFor();
   await page.getByText("注册时需要验证邮箱；登录仍使用登录名和密码。",{exact:true}).waitFor();
   await page.goto("http://127.0.0.1:43132/reset-password");await advance();
   await page.getByLabel("已绑定的安全邮箱",{exact:true}).fill(next);
   await page.getByRole("button",{name:"发送验证码",exact:true}).click();
   await page.getByLabel("邮箱验证码",{exact:true}).fill(await code(next));
   await page.getByRole("button",{name:"验证邮箱",exact:true}).click();
   await page.getByLabel("新密码",{exact:true}).fill("New_journey_pass_8");
   await page.getByLabel("再次输入新密码",{exact:true}).fill("New_journey_pass_8");
   await page.getByRole("button",{name:"重置密码",exact:true}).click();
   await page.waitForURL("**/login");
   await page.getByLabel("登录名",{exact:true}).fill(username);
   await page.getByLabel("密码",{exact:true}).fill("Journey_pass_9");
   await page.getByRole("button",{name:"登录",exact:true}).click();
   await page.getByRole("alert").filter({hasText:"未登录或登录已过期"}).waitFor();
   assert.equal(loginStatuses.at(-1),401);
   await page.getByLabel("密码",{exact:true}).fill("New_journey_pass_8");
   await page.getByRole("button",{name:"登录",exact:true}).click();
   await page.waitForURL(url=>url.pathname==="/");
   await page.goto("http://127.0.0.1:43132/settings");await settleOverlays();
   await page.getByRole("button",{name:"换绑邮箱",exact:true}).waitFor();
   await page.reload();await settleOverlays();await page.getByRole("button",{name:"换绑邮箱",exact:true}).waitFor();
   assert.equal(await page.evaluate(()=>document.documentElement.classList.contains("dark")),theme==="dark");
   if(mobile)assert.equal(await page.evaluate(()=>navigator.standalone),true);
   // 合成历史用户登录、首次补绑，不能把新注册账号冒充历史账号。
   await page.getByRole("button",{name:"退出登录",exact:true}).click();
   await page.waitForURL(url=>url.pathname==="/");
   await page.getByRole("link",{name:"登录",exact:true}).first().waitFor();
   await page.getByText("注册时需要验证邮箱；登录仍使用登录名和密码。",{exact:true}).waitFor();
   await page.goto("http://127.0.0.1:43132/login");
   await page.getByLabel("登录名",{exact:true}).fill("journey_legacy_"+width);
   await page.getByLabel("密码",{exact:true}).fill("Historical_pass_9");
   await page.getByRole("button",{name:"登录",exact:true}).click();
   await page.waitForURL(url=>url.pathname==="/");
   await page.goto("http://127.0.0.1:43132/settings");await settleOverlays();
   if(await guide.count())await guide.click();
   await page.getByRole("button",{name:"绑定邮箱",exact:true}).click();
   const bind=page.getByRole("dialog",{name:"绑定安全邮箱"}),legacy="legacy"+width+"@example.com";
   await bind.getByLabel("新邮箱",{exact:true}).fill(legacy);
   await bind.getByRole("button",{name:"发送验证码",exact:true}).click();
   await bind.getByLabel("新邮箱验证码",{exact:true}).fill(await code(legacy));
   await bind.getByRole("button",{name:"验证新邮箱",exact:true}).click();
   await bind.getByText("新邮箱已验证，请在 5 分钟内确认完成。",{exact:true}).waitFor();
   await bind.getByRole("button",{name:"确认完成",exact:true}).click();
   await page.getByText("安全邮箱已更新",{exact:true}).waitFor();
   assert.equal(sendCount,2); // 超时一次、成功重试一次，快速连点不增加请求。
   assert.deepEqual(loginStatuses,[401,200,200]);
   assert.deepEqual(errors,[]);
   console.log("PASS actual API+SMTP journey "+width+" "+theme+" PWA-emulated="+mobile);
   scenarios++;await context.close();
  }
  assert.equal(scenarios,2);console.log("SCENARIOS="+scenarios+" SCREENSHOTS="+output);
 }catch(error){if(page&&!page.isClosed())console.log("PAGE_STATE",await page.locator("body").innerText());throw error;}finally{if(scenarios!==2)console.log("ISOLATED_BACKEND_DIAGNOSTIC",back?.log());if(browser)await browser.close();for(const p of children.reverse()){p.kill();if(p.exitCode===null)await new Promise(r=>p.once("exit",r));}}
})().catch(e=>{console.error(e);process.exitCode=1;});









