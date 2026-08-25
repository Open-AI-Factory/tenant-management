const {chromium}=require('playwright');
const {spawn}=require('child_process');
const assert=require('assert/strict');
const path=require('path');
(async()=>{
  const server=spawn('python',['-B','-X','utf8','test_web_demo_support.py'],{cwd:__dirname,windowsHide:true});
  let browser;
  try{
    const url=await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error('startup timeout')),10000);
      server.stdout.on('data',c=>{const match=String(c).match(/http:\/\/127\.0\.0\.1:\d+/);if(match){clearTimeout(timer);resolve(match[0]);}});
      server.on('error',reject);server.stderr.on('data',c=>process.stderr.write(c));
    });
    browser=await chromium.launch({headless:true,channel:'chrome'});
    const ctx=await browser.newContext({viewport:{width:1440,height:1100}});
    await ctx.addCookies([{name:'grafana_session',value:'admin',url}]);
    const page=await ctx.newPage(),errors=[],mutations=[];
    page.on('pageerror',e=>errors.push(String(e)));
    page.on('request',r=>{if(r.method()!=='GET')mutations.push(new URL(r.url()).pathname);});
    await page.goto(url);await page.locator('#workspace').waitFor({state:'visible'});
    const menu=page.getByRole('link',{name:'예측 쿼터 데모'});
    assert.equal(await menu.isVisible(),true);await menu.click();
    await page.locator('#quota-a').waitFor();assert.equal(await page.locator('#quota-a').innerText(),'1');
    assert.equal(new URL(page.url()).pathname,'/quota-demo');
    assert.match(await page.locator('.notice').innerText(),/공유/);
    const next=async i=>{await page.locator('#next').click();await page.waitForFunction(n=>document.querySelector('#progress').textContent.startsWith(n+' /'),i);};
    for(let i=1;i<=13;i++){
      await next(i);
      if(i===2){assert.equal(await page.locator('#quota-a').innerText(),'3');assert.equal(await page.locator('#quota-b').innerText(),'1');}
      if(i===3)assert.equal(await page.locator('#shortfall-b').innerText(),'2');
      if(i===7){assert.equal(await page.locator('#quota-a').innerText(),'1');assert.equal(await page.locator('#quota-b').innerText(),'3');}
      if(i===11)assert.match(await page.locator('#reason-a').innerText(),/작업 중/);
    }
    assert.match(await page.locator('#reason-a').innerText(),/오래된 예측/);
    await page.locator('#reset').click();await page.waitForFunction(()=>document.querySelector('#progress').textContent.startsWith('0 /'));
    await page.locator('summary').click();await page.locator('#packet').fill('{"unexpected":true}');await page.locator('#send').click();
    await page.waitForFunction(()=>document.querySelector('#message').textContent.includes('Invalid forecast fields'));
    await page.screenshot({path:path.join(__dirname,'work','web-demo-desktop.png'),fullPage:true});
    const peer=await ctx.newPage();await peer.goto(url+'/quota-demo');await peer.locator('#quota-a').waitFor();
    await next(1);await peer.waitForFunction(()=>document.querySelector('#progress').textContent.startsWith('1 /'));
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.screenshot({path:path.join(__dirname,'work','web-demo-mobile.png'),fullPage:true});
    await page.getByRole('link',{name:'테넌트 관리로 돌아가기'}).click();await page.locator('#workspace').waitFor({state:'visible'});
    assert.equal(await page.locator('#deployment-mode').isVisible(),true);
    await ctx.clearCookies();await ctx.addCookies([{name:'grafana_session',value:'member',url}]);
    await page.reload();await page.locator('#workspace').waitFor({state:'visible'});
    assert.equal(await page.getByRole('link',{name:'예측 쿼터 데모'}).isVisible(),false);
    const denial=await ctx.request.get(url+'/api/quota-demo/state');assert.equal(denial.status(),403);
    assert.equal(mutations.every(p=>p.startsWith('/api/quota-demo/')),true);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:13,scenarioSteps:13,pageErrors:errors,viewports:[1440,390],checks:['admin menu','authenticated page','shared warning','growth','shortage','reassign','busy defer','stale hold','CSRF reset','invalid input','shared poll','mobile fit','return/member denial']},null,2));
  }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
