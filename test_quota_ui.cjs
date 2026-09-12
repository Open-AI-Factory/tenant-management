const {chromium}=require('playwright');
const {spawn}=require('child_process');
const fs=require('fs');
const path=require('path');
const assert=require('assert/strict');
(async()=>{
  const server=spawn('python',['-B','-X','utf8','quota_simulator.py','--port','0'],{cwd:__dirname,windowsHide:true});
  let browser;
  try {
    const url=await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error('Server startup timeout')),10000);
      server.stdout.on('data',chunk=>{const match=String(chunk).match(/http:\/\/127\.0\.0\.1:\d+/);if(match){clearTimeout(timer);resolve(match[0]);}});
      server.on('error',reject);server.stderr.on('data',c=>process.stderr.write(c));
    });
    browser=await chromium.launch({headless:true,channel:'chrome'});
    const page=await browser.newPage({viewport:{width:1440,height:1100}});const errors=[];
    page.on('pageerror',e=>errors.push(String(e)));
    await page.goto(url);await page.getByRole('heading',{name:'예측으로 조절하는 테넌트 쿼터'}).waitFor();
    assert.equal(await page.locator('#quota-a').innerText(),'1');
    const next=async index=>{await page.getByRole('button',{name:'다음 단계'}).click();await page.waitForFunction(i=>document.querySelector('#progress').textContent.startsWith(i+' /'),index);};
    await next(1);assert.match(await page.locator('#reason-a').innerText(),/증설 시점 대기/);
    await next(2);assert.equal(await page.locator('#quota-a').innerText(),'3');assert.equal(await page.locator('#quota-b').innerText(),'1');
    await next(3);assert.equal(await page.locator('#shortfall-b').innerText(),'2');
    await next(4);assert.match(await page.locator('#receipt').innerText(),/Duplicate/);
    for(let i=5;i<=7;i++)await next(i);
    assert.equal(await page.locator('#quota-a').innerText(),'1');assert.equal(await page.locator('#quota-b').innerText(),'3');
    for(let i=8;i<=11;i++)await next(i);
    assert.match(await page.locator('#reason-a').innerText(),/작업 중/);
    await next(12);assert.equal(await page.locator('#quota-a').innerText(),'3');
    await page.screenshot({path:path.join(__dirname,'work','quota-ui-desktop.png'),fullPage:true});
    await next(13);assert.match(await page.locator('#reason-a').innerText(),/오래된 예측/);
    await page.getByRole('button',{name:'처음부터'}).click();await page.waitForFunction(()=>document.querySelector('#progress').textContent.startsWith('0 /'));
    await page.locator('summary').click();
    await page.locator('#packet').fill('{"unexpected":true}');await page.getByRole('button',{name:'예측 전송'}).click();
    await page.waitForFunction(()=>document.querySelector('#message').textContent.includes('Invalid forecast fields'));
    assert.equal(await page.locator('#quota-a').innerText(),'1');
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.screenshot({path:path.join(__dirname,'work','quota-ui-mobile.png'),fullPage:true});
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:12,scenarioSteps:13,pageErrors:errors,viewports:[1440,390],checks:['initial','lead hold','growth','capacity shortage','duplicate','shrink and reassign','busy defer','idle growth','stale hold','reset','invalid input','mobile fit']},null,2));
  } finally {if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
