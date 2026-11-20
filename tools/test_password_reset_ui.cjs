/* 密码找回与显示开关：所有API被接管，不启动后端、不连接真实邮箱。 */
const {spawn}=require("node:child_process");
const assert=require("node:assert/strict"),path=require("node:path"),os=require("node:os"),fs=require("node:fs");
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||path.join(os.homedir(),".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright"));
(async()=>{
 const child=spawn(process.execPath,["node_modules/next/dist/bin/next","start","--hostname","127.0.0.1","--port","43130"],{
  cwd:path.resolve(__dirname,"../frontend"),windowsHide:true,env:{...process.env,NEXT_TELEMETRY_DISABLED:"1"},stdio:["ignore","pipe","pipe"]});
 let log="",browser;
 child.stdout.on("data",b=>log+=b);child.stderr.on("data",b=>log+=b);
 try{
  for(let i=0;i<100&&!log.includes("Ready in");i++){if(child.exitCode!==null)throw Error(log);await new Promise(r=>setTimeout(r,200));}
  assert.ok(log.includes("Ready in"),log);
  browser=await chromium.launch({headless:true,channel:process.env.ZHISUI_BROWSER_CHANNEL||"msedge"});
  const output=fs.mkdtempSync(path.join(os.tmpdir(),"zhisui-step6-ui-"));
  for(const width of [1440,390]){
   const context=await browser.newContext({viewport:{width,height:1000},reducedMotion:"reduce"});
   const page=await context.newPage();let resetCount=0,submitCount=0;const errors=[];
   page.on("pageerror",e=>errors.push(e.message));
   await page.route("**/api/**",async route=>{
    const request=route.request(),url=new URL(request.url()),data=request.postDataJSON()||{};
    const reply=(body,status=200)=>route.fulfill({status,contentType:"application/json",body:JSON.stringify(body)});
    if(url.pathname==="/api/auth/config")return reply({allow_signup:true,require_email:true,email_ready:true});
    if(url.pathname==="/api/auth/me")return reply({detail:"请登录"},401);
    if(url.pathname==="/api/auth/password/reset/email/send")return reply({
      request_id:"reset-request-1234567890",resend_after:60,
      message:"如果该邮箱已绑定可用账号，验证码将发送到该邮箱，请检查收件箱或稍后重试。"});
    if(url.pathname==="/api/auth/password/reset/email/verify"){
      if(data.code!=="482619")return reply({detail:"验证码不正确"},400);
      return reply({grant:"reset-grant-123456789012345"});
    }
    if(url.pathname==="/api/auth/password/reset"){
      assert.equal(data.grant,"reset-grant-123456789012345");
      assert.equal(data.new_password,data.confirm_password);
      resetCount++;
      if(data.new_password==="12345678")return reply({detail:"密码不能全是数字"},400);
      return reply({ok:true});
    }
    if(url.pathname==="/api/auth/login"||url.pathname==="/api/auth/register"){submitCount++;return reply({detail:"不应由显示按钮提交"},400);}
    return reply({detail:"Not Found"},404);
   });
   await page.goto("http://127.0.0.1:43130/login");
   await page.locator(".zs-boot-splash").waitFor({state:"detached"});
   const password=page.getByLabel("密码",{exact:true});
   await password.fill("Visible_pass_9");
   assert.equal(await password.getAttribute("type"),"password");
   await page.getByRole("button",{name:"显示密码",exact:true}).click();
   assert.equal(await password.getAttribute("type"),"text");
   assert.equal(await password.inputValue(),"Visible_pass_9");
   await page.getByRole("button",{name:"隐藏密码",exact:true}).click();
   assert.equal(await password.getAttribute("type"),"password");
   await page.getByRole("tab",{name:"注册",exact:true}).click();
   assert.equal(await page.getByLabel("密码",{exact:true}).getAttribute("type"),"password");
   await page.getByRole("button",{name:"显示密码",exact:true}).click();
   assert.equal(await page.getByLabel("密码",{exact:true}).inputValue(),"Visible_pass_9");
   await page.getByRole("tab",{name:"登录",exact:true}).click();
   assert.equal(await page.getByLabel("密码",{exact:true}).getAttribute("type"),"password");
   assert.equal(submitCount,0);
   await page.getByRole("link",{name:"找回密码",exact:true}).click();
   await page.waitForURL("**/reset-password");
   assert.equal(await page.getByRole("button",{name:"重置密码",exact:true}).count(),0);
   await page.getByLabel("已绑定的安全邮箱",{exact:true}).fill("recover@example.com");
   await page.getByRole("button",{name:"发送验证码",exact:true}).click();
   await page.getByLabel("邮箱验证码",{exact:true}).fill("000000");
   await page.getByRole("button",{name:"验证邮箱",exact:true}).click();
   await page.getByText("验证码不正确",{exact:true}).waitFor();
   await page.getByLabel("邮箱验证码",{exact:true}).fill("482619");
   await page.getByRole("button",{name:"验证邮箱",exact:true}).click();
   await page.getByLabel("新密码",{exact:true}).fill("Changed_pass_8");
   await page.getByLabel("再次输入新密码",{exact:true}).fill("Other_pass_9");
   await page.getByRole("button",{name:"显示新密码",exact:true}).click();
   assert.equal(await page.getByLabel("新密码",{exact:true}).getAttribute("type"),"text");
   assert.equal(await page.getByLabel("再次输入新密码",{exact:true}).getAttribute("type"),"password");
   await page.getByRole("button",{name:"显示再次输入新密码",exact:true}).focus();
   await page.keyboard.press("Enter");
   assert.equal(await page.getByLabel("再次输入新密码",{exact:true}).getAttribute("type"),"text");
   assert.equal(resetCount,0);
   await page.getByRole("button",{name:"重置密码",exact:true}).click();
   await page.getByText("两次输入的新密码不一致",{exact:true}).waitFor();
   assert.equal(resetCount,0);
   await page.getByLabel("新密码",{exact:true}).fill("12345678");
   await page.getByLabel("再次输入新密码",{exact:true}).fill("12345678");
   await page.getByRole("button",{name:"重置密码",exact:true}).click();
   await page.getByText("密码不能全是数字",{exact:true}).waitFor();
   await page.getByLabel("新密码",{exact:true}).fill("Changed_pass_8");
   await page.getByLabel("再次输入新密码",{exact:true}).fill("Changed_pass_8");
   await page.getByRole("button",{name:"隐藏新密码",exact:true}).click();
   await page.getByRole("button",{name:"隐藏再次输入新密码",exact:true}).click();
   await page.screenshot({path:path.join(output,"reset-"+width+".png"),fullPage:true});
   await page.getByRole("button",{name:"重置密码",exact:true}).click();
   await page.waitForURL("**/login");
   await page.getByRole("button",{name:"登录",exact:true}).waitFor();
   assert.equal(await page.getByLabel("密码",{exact:true}).inputValue(),"");
   assert.equal(resetCount,2);assert.equal(submitCount,0);
   await page.goto("http://127.0.0.1:43130/reset-password");
   assert.equal(await page.getByRole("button",{name:"重置密码",exact:true}).count(),0);
   assert.equal(await page.getByLabel("已绑定的安全邮箱",{exact:true}).inputValue(),"");
   assert.deepEqual(errors,[]);
   console.log("PASS reset+eyes+keyboard+redirect width="+width);
   await context.close();
  }
  console.log("SCREENSHOTS="+output);
 }finally{if(browser)await browser.close();child.kill();if(child.exitCode===null)await new Promise(r=>child.once("exit",r));}
})().catch(e=>{console.error(e);process.exitCode=1;});
