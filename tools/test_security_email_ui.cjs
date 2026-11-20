/* 浏览器交互验收：只启动本地前端，所有 /api 请求均由测试接管，不连接后端或真实邮箱。 */
const {spawn} = require("node:child_process");
const assert = require("node:assert/strict");
const path = require("node:path");
const os = require("node:os");
const fs = require("node:fs");
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || path.join(os.homedir(),
  ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright"));
(async () => {
  const child = spawn(process.execPath, ["node_modules/next/dist/bin/next","start","--hostname","127.0.0.1","--port","43128"],
    {cwd:path.resolve(__dirname,"../frontend"),windowsHide:true,
     env:{...process.env,NEXT_TELEMETRY_DISABLED:"1"},stdio:["ignore","pipe","pipe"]});
  let log = "", browser;
  child.stdout.on("data", b=>log+=b); child.stderr.on("data",b=>log+=b);
  try {
    for(let i=0;i<100&&!log.includes("Ready in");i++){
      if(child.exitCode!==null) throw Error(log);
      await new Promise(r=>setTimeout(r,200));
    }
    assert.ok(log.includes("Ready in"),log);
    browser=await chromium.launch({headless:true,channel:process.env.ZHISUI_BROWSER_CHANNEL || "msedge"});
    const output=process.env.ZHISUI_UI_OUTPUT || fs.mkdtempSync(path.join(os.tmpdir(),"zhisui-step4-ui-"));
    fs.mkdirSync(output,{recursive:true});
    let scenarios=0;
    for(const width of [1440,390]){
      const context=await browser.newContext({viewport:{width,height:900}});
      const page=await context.newPage();
      const errors=[];
      page.on("pageerror",e=>errors.push(e.message));
      let bound=false, count=0, unavailable=false;
      const requests=new Map(), grants=new Map();
      const state=()=>({ready:true,bound,masked_email:bound?"ne***@example.com":null});
      await page.route("**/api/**",async route=>{
        const request=route.request(), url=new URL(request.url()), data=request.postDataJSON()||{};
        const reply=(body,status=200)=>route.fulfill({status,contentType:"application/json",body:JSON.stringify(body)});
        if(url.pathname==="/api/auth/me")return reply({user:{id:"synthetic-ui",username:"HZL_test",nickname:"",avatar:""},quota:{},is_admin:false});
        if(url.pathname==="/api/auth/config")return reply({allow_signup:true});
        if(url.pathname==="/api/auth/security-email")return unavailable?reply({detail:"测试邮件服务不可用"},503):reply(state());
        if(url.pathname==="/api/auth/email/code/send"){
          const id="request-"+String(++count).padStart(24,"0");
          requests.set(id,data);
          return reply({request_id:id,flow:data.flow,expires_in:600,resend_after:60});
        }
        if(url.pathname==="/api/auth/email/code/verify"){
          if(data.code!=="482619")return reply({detail:"验证码不正确"},400);
          const grant="grant-"+data.request_id;
          grants.set(grant,requests.get(data.request_id));
          return reply({grant});
        }
        if(url.pathname==="/api/auth/security-email/bind"){
          assert.equal(grants.get(data.grant).purpose,"bind_email");
          bound=true; return reply(state());
        }
        if(url.pathname==="/api/auth/security-email/change"){
          const old=grants.get(data.old_grant), next=grants.get(data.new_grant);
          assert.equal(old.purpose,"change_email_old"); assert.equal(next.purpose,"change_email_new");
          assert.equal(old.flow,next.flow);
          assert.equal(old.address,undefined);
          bound=true;return reply(state());
        }
        return reply({detail:"Not Found"},404);
      });
      await page.goto("http://127.0.0.1:43128/settings");
      // 新浏览器有首次引导，使用其正常关闭按钮，不强制点击被遮挡元素。
      await page.getByRole("button",{name:"跳过",exact:true}).click();

      await page.getByRole("button",{name:"绑定邮箱",exact:true}).click();
      let dialog=page.getByRole("dialog",{name:"绑定安全邮箱"});
      const done=dialog.getByRole("button",{name:"确认完成"});
      assert.equal(await done.isDisabled(),true);
      await dialog.getByLabel("新邮箱",{exact:true}).fill("new@example.com");
      await dialog.getByRole("button",{name:"发送验证码",exact:true}).click();
      await dialog.getByLabel("新邮箱验证码",{exact:true}).fill("000000");
      await dialog.getByRole("button",{name:"验证新邮箱"}).click();
      await dialog.getByRole("alert").filter({hasText:"验证码不正确"}).waitFor();
      await dialog.getByLabel("新邮箱验证码",{exact:true}).fill("482619");
      await dialog.getByRole("button",{name:"验证新邮箱"}).click();
      await page.getByText("新邮箱已验证，请在 5 分钟内确认完成。",{exact:true}).waitFor();
      assert.equal(await done.isEnabled(),true);
      await dialog.getByLabel("新邮箱",{exact:true}).fill("edited@example.com");
      assert.equal(await done.isDisabled(),true);
      assert.equal(await dialog.getByText("新邮箱已验证，请在 5 分钟内确认完成。").count(),0);
      // 关闭重开必须清除证明和输入，避免带入上一次流程。
      await dialog.getByRole("button",{name:"取消",exact:true}).click();
      await page.getByRole("button",{name:"绑定邮箱",exact:true}).click();
      dialog=page.getByRole("dialog",{name:"绑定安全邮箱"});
      assert.equal(await dialog.getByLabel("新邮箱",{exact:true}).inputValue(),"");
      await dialog.getByLabel("新邮箱",{exact:true}).fill("new@example.com");
      await dialog.getByRole("button",{name:"发送验证码",exact:true}).click();
      await dialog.getByLabel("新邮箱验证码",{exact:true}).fill("482619");
      await dialog.getByRole("button",{name:"验证新邮箱"}).click();
      await dialog.getByRole("button",{name:"确认完成"}).click();
      await page.getByText("安全邮箱已更新",{exact:true}).waitFor();
      await page.getByRole("button",{name:"换绑邮箱",exact:true}).click();
      dialog=page.getByRole("dialog",{name:"换绑安全邮箱"});
      assert.equal(await dialog.getByRole("button",{name:"解绑",exact:true}).count(),0);
      for(const label of ["原邮箱","新邮箱"]){
        const field=dialog.getByRole("group",{name:label,exact:true});
        if(label==="新邮箱")await field.getByLabel("新邮箱",{exact:true}).fill("next@example.com");
        await field.getByRole("button",{name:"发送验证码",exact:true}).click();
        await field.getByLabel(label+"验证码",{exact:true}).fill("482619");
        await field.getByRole("button",{name:"验证"+label,exact:true}).click();
        await field.getByText(label+"已验证，请在 5 分钟内确认完成。",{exact:true}).waitFor();
      }
      assert.equal(await dialog.getByRole("button",{name:"确认完成"}).isEnabled(),true);
      // 焦点应保留在弹窗内，Esc 可关闭并将焦点还给打开按钮。
      await page.keyboard.press("Tab");
      assert.equal(await dialog.evaluate(el=>el.contains(document.activeElement)),true);
      await page.screenshot({path:path.join(output,"settings-email-"+width+".png"),fullPage:true});
      await page.keyboard.press("Escape");
      await page.getByRole("button",{name:"换绑邮箱",exact:true}).click();
      dialog=page.getByRole("dialog",{name:"换绑安全邮箱"});
      for(const label of ["原邮箱","新邮箱"]){
        const field=dialog.getByRole("group",{name:label,exact:true});
        if(label==="新邮箱")await field.getByLabel("新邮箱",{exact:true}).fill("next@example.com");
        await field.getByRole("button",{name:"发送验证码",exact:true}).click();
        await field.getByLabel(label+"验证码",{exact:true}).fill("482619");
        await field.getByRole("button",{name:"验证"+label,exact:true}).click();
        await field.getByText(label+"已验证，请在 5 分钟内确认完成。",{exact:true}).waitFor();
      }
      await dialog.getByRole("button",{name:"确认完成"}).click();
      await page.getByText("安全邮箱已更新",{exact:true}).waitFor();
      unavailable=true; await page.reload();
      await page.locator("#account-security").getByRole("alert").waitFor();
      assert.equal(await page.getByRole("button",{name:"绑定邮箱",exact:true}).isDisabled(),true);
      assert.deepEqual(errors,[]);
      console.log("PASS bind/change/error/invalidation/keyboard at width="+width);
      scenarios++;
      await context.close();
    }
    console.log("SCENARIOS="+scenarios+" SCREENSHOTS="+output);
  } finally {
    if(browser)await browser.close();
    child.kill();
    if(child.exitCode===null)await new Promise(r=>child.once("exit",r));
  }
})().catch(e=>{console.error(e);process.exitCode=1;});






