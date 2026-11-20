/* 第5步浏览器注册测试：模拟所有API，不连接后端或真实邮箱。 */
const {spawn}=require("node:child_process");
const assert=require("node:assert/strict"), path=require("node:path"), os=require("node:os"), fs=require("node:fs");
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || path.join(os.homedir(),".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright"));
(async()=>{
 const child=spawn(process.execPath,["node_modules/next/dist/bin/next","start","--hostname","127.0.0.1","--port","43129"],{
  cwd:path.resolve(__dirname,"../frontend"),windowsHide:true,env:{...process.env,NEXT_TELEMETRY_DISABLED:"1"},stdio:["ignore","pipe","pipe"]});
 let log="",browser;
 child.stdout.on("data",b=>log+=b);child.stderr.on("data",b=>log+=b);
 try{
  for(let i=0;i<100&&!log.includes("Ready in");i++){if(child.exitCode!==null)throw Error(log);await new Promise(r=>setTimeout(r,200));}
  assert.ok(log.includes("Ready in"),log);
  browser=await chromium.launch({headless:true,channel:process.env.ZHISUI_BROWSER_CHANNEL||"msedge"});
  const output=fs.mkdtempSync(path.join(os.tmpdir(),"zhisui-step5-ui-"));
  for(const [width,entry] of [[1440,"/login"],[390,"/settings"],[390,"/library"]]){
   const context=await browser.newContext({viewport:{width,height:1000},reducedMotion:"reduce"});
   const page=await context.newPage();let signupCount=0,ready=true,authed=false,allowSuccess=false;const errors=[];
   page.on("pageerror",e=>errors.push(e.message));
   await page.route("**/api/**",async route=>{
    const request=route.request(),url=new URL(request.url()),data=request.postDataJSON()||{};
    const reply=(body,status=200)=>route.fulfill({status,contentType:"application/json",body:JSON.stringify(body)});
    if(url.pathname==="/api/auth/config")return reply({allow_signup:true,require_email:true,email_ready:ready});
    if(url.pathname==="/api/auth/me")return authed ? reply({user:{id:"synthetic",username:"new_user",nickname:"",avatar:""},quota:{},is_admin:false}) : reply({detail:"请登录"},401);
    if(url.pathname==="/api/auth/register/email/send")return reply({request_id:"signup-request-id-123456789",expires_in:600,resend_after:60});
    if(url.pathname==="/api/auth/register/email/verify"){
     if(data.code!=="482619")return reply({detail:"验证码不正确"},400);
     return reply({grant:"verified-signup-grant-123456789"});
    }
    if(url.pathname==="/api/auth/register"){
     assert.equal(data.email,"valid@example.com");
     assert.equal(data.email_grant,"verified-signup-grant-123456789");
     assert.equal(data.username,"new_user");assert.equal(data.password,"New_user_pass_9");
     signupCount++; if(allowSuccess){authed=true;return reply({id:"synthetic",username:"new_user",token:null});} return reply({detail:"该登录名已被占用"},409);
    }
    return reply({detail:"Not Found"},404);
   });
   await page.goto("http://127.0.0.1:43129"+entry);
   await page.locator(".zs-boot-splash").waitFor({state:"detached"});
   await page.getByRole("tab",{name:"注册",exact:true}).click();
   await page.getByLabel("登录名",{exact:true}).fill("new_user");
   await page.getByLabel("密码",{exact:true}).fill("New_user_pass_9");
   await page.getByLabel("邮箱",{exact:true}).fill("valid@example.com");
   await page.getByRole("button",{name:"注册并登录",exact:true}).click();
   await page.getByText("请先验证注册邮箱",{exact:true}).waitFor();
   assert.equal(signupCount,0);
   await page.getByRole("button",{name:"发送邮箱验证码",exact:true}).click();
   await page.getByLabel("邮箱验证码",{exact:true}).fill("000000");
   await page.getByRole("button",{name:"验证邮箱",exact:true}).click();
   await page.getByText("验证码不正确",{exact:true}).waitFor();
   await page.getByLabel("邮箱验证码",{exact:true}).fill("482619");
   await page.getByRole("button",{name:"验证邮箱",exact:true}).click();
   await page.getByText("邮箱已验证，请在 5 分钟内完成注册。",{exact:true}).waitFor();
   await page.getByLabel("邮箱",{exact:true}).fill("edited@example.com");
   await page.getByRole("button",{name:"注册并登录",exact:true}).click();
   assert.equal(signupCount,0);
   await page.getByRole("tab",{name:"登录",exact:true}).click();
   await page.getByRole("tab",{name:"注册",exact:true}).click();
   assert.equal(await page.getByLabel("邮箱",{exact:true}).inputValue(),"");
   await page.getByLabel("邮箱",{exact:true}).fill("valid@example.com");
   await page.getByRole("button",{name:"发送邮箱验证码",exact:true}).click();
   await page.getByLabel("邮箱验证码",{exact:true}).fill("482619");
   await page.getByRole("button",{name:"验证邮箱",exact:true}).click();
   await page.getByText("邮箱已验证，请在 5 分钟内完成注册。",{exact:true}).waitFor();
   await page.screenshot({path:path.join(output,"register-"+width+"-"+entry.slice(1)+".png"),fullPage:true});
   await page.getByRole("button",{name:"注册并登录",exact:true}).click();
   await page.getByText("该登录名已被占用",{exact:true}).waitFor();
   assert.equal(signupCount,1);
   assert.equal(await page.getByLabel("邮箱",{exact:true}).inputValue(),"valid@example.com");
   ready=false;
   await page.getByRole("tab",{name:"登录",exact:true}).click();
   await page.getByRole("tab",{name:"注册",exact:true}).click();
   await page.getByText("邮箱验证暂不可用，请稍后再注册。已有账号可正常登录。",{exact:true}).waitFor();
   assert.equal(await page.getByRole("button",{name:"发送邮箱验证码",exact:true}).isDisabled(),true);
   ready=true; allowSuccess=true;
   await page.getByRole("tab",{name:"登录",exact:true}).click();
   await page.getByRole("tab",{name:"注册",exact:true}).click();
   await page.getByLabel("邮箱",{exact:true}).fill("valid@example.com");
   await page.getByRole("button",{name:"发送邮箱验证码",exact:true}).click();
   await page.getByLabel("邮箱验证码",{exact:true}).fill("482619");
   await page.getByRole("button",{name:"验证邮箱",exact:true}).click();
   await page.getByText("邮箱已验证，请在 5 分钟内完成注册。",{exact:true}).waitFor();
   await page.getByRole("button",{name:"注册并登录",exact:true}).click();
   await page.getByRole("button",{name:"注册并登录",exact:true}).waitFor({state:"detached"});
   assert.equal(signupCount,2);
   assert.deepEqual(errors,[]);
   console.log("PASS registration at "+width+" "+entry);
   await context.close();
  }
  console.log("SCREENSHOTS="+output);
 }finally{if(browser)await browser.close();child.kill();if(child.exitCode===null)await new Promise(r=>child.once("exit",r));}
})().catch(e=>{console.error(e);process.exitCode=1;});

