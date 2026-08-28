// Local browser regression tests. Requires Playwright; never contacts a cluster.
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const http=require('node:http');
const path=require('node:path');
const {spawnSync}=require('node:child_process');
const {chromium}=require('playwright');

const fixture=spawnSync(process.env.PYTHON||'python',['-B','-X','utf8','-c',`
import json
from test_usage import UsageTests
from executions import ACCESS
from kubeutil import TENANTS
t=UsageTests(); t.setUp(); second=t.namespace()
t.claim('first',['mig1']); t.claim('second',['mig2'],namespace=second)
t.pod('waiting-a'); t.pod('waiting-b',namespace=second)
t.k.objects[TENANTS+'/platform-services']={'metadata':{'name':'platform-services','uid':'t-service'}}
out={}
for role in ('admin','member','viewer'):
    bindings=json.loads(t.k.objects[ACCESS]['data']['bindings.json'])
    bindings[0]['role']='viewer' if role=='admin' else role
    t.k.objects[ACCESS]['data']['bindings.json']=json.dumps(bindings)
    principal=dict(t.user,platformAdmin=role=='admin')
    out[role]={'session':{'principal':principal,'csrf':'local-test'},
               'state':t.api.dispatch(principal,'GET','/api/state')[1]}
print(json.dumps(out))
`],{cwd:__dirname,encoding:'utf8'});
assert.equal(fixture.status,0,fixture.stderr);
const fixtures=JSON.parse(fixture.stdout);
let role='admin',failState=false,override=null;
const server=http.createServer((req,res)=>{
  res.setHeader('Cache-Control','no-store');
  const url=new URL(req.url,'http://127.0.0.1');
  if(url.pathname==='/api/session') return json(fixtures[role].session);
  if(url.pathname==='/api/state'){
    if(failState){res.statusCode=503;return json({error:'synthetic outage'});}
    const state=structuredClone(override||fixtures[role].state);
    for(const tenant of state.tenants) if(tenant.usage?.status==='Observed') tenant.usage.observedAt=new Date().toISOString();
    return json(state);
  }
  if(url.pathname.endsWith('/jobs'))return json({jobs:[{input:{requestId:'visible-job'},
    pod:{name:'test-visible-job',uid:'visible-uid'},context:{namespaceName:url.searchParams.get('namespaceName'),queueName:'tenant-a-q'},
    actor:{subjectKind:'uid',subjectId:'alice'},phase:'Running',release:'Pending',canCancel:false}],nextCursor:null});
  if(url.pathname.endsWith('/logs'))return json({logs:'local GPU log fixture'});
  if(url.pathname.endsWith('/members'))return json({bindings:[],resourceVersion:'1'});
  const assets={'/':['index.html','text/html'],'/app.js':['app.js','text/javascript'],'/style.css':['style.css','text/css']};
  if(assets[url.pathname]){
    const [file,mime]=assets[url.pathname];res.setHeader('Content-Type',mime+'; charset=utf-8');
    return res.end(fs.readFileSync(path.join(__dirname,file)));
  }
  res.statusCode=404;res.end();
  function json(data){res.setHeader('Content-Type','application/json; charset=utf-8');res.end(JSON.stringify(data));}
});

(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({headless:true,channel:process.env.BROWSER_CHANNEL||'chrome'});
  const page=await browser.newPage({viewport:{width:1280,height:1000}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const checks=[];
  const expectText=async(id,expected)=>{assert.equal(await page.locator('#'+id).count(),1,'missing UI: '+id);assert.equal((await page.locator('#'+id).textContent()).trim(),expected);};
  const poll=async()=>page.evaluate(()=>refresh());
  const open=async()=>{await page.goto('http://127.0.0.1:'+server.address().port);await page.locator('#workspace').waitFor({state:'visible'});await page.getByRole('button',{name:/team-a/}).click();};
  try{
    await open();
    await expectText('usage-guaranteed','1개');await expectText('usage-maximum','1개');
    await expectText('usage-allocated','2개');await expectText('usage-waiting','2건');
    assert.match(await page.locator('#usage-observed').textContent(),/관측/);
    assert.match(await page.locator('#tenants').textContent(),/할당 2개.*대기 2건/);
    checks.push('desktop: separate budget, allocation and waiting counts');

    await page.locator('#duration').selectOption('120');
    await page.locator('#space').selectOption('tenant-team-a-dev');
    await page.getByRole('button',{name:'로그',exact:true}).click();
    await page.locator('#job-logs').waitFor({state:'visible'});
    await poll();
    assert.equal(await page.locator('#duration').inputValue(),'120');
    assert.equal(await page.locator('#space').inputValue(),'tenant-team-a-dev');
    assert.equal(await page.locator('#job-logs').isVisible(),true,'usage-only poll must preserve open job logs');
    checks.push('poll preserves duration, namespace and open job logs');

    await page.locator('#displayName').fill('unsaved display name');
    override=structuredClone(fixtures.admin.state);
    override.tenants.find(t=>t.name==='team-a').usage.allocatedDevices=0;
    override.tenants.find(t=>t.name==='team-a').usage.waitingJobs=0;
    await poll();
    await expectText('usage-allocated','0개');await expectText('usage-waiting','0건');
    assert.equal(await page.locator('#displayName').inputValue(),'unsaved display name');
    checks.push('live zero counts update without resetting unsaved form');

    failState=true;
    await page.evaluate(()=>refresh().catch(()=>{}));
    await expectText('usage-allocated','미확인');await expectText('usage-waiting','미확인');
    assert.doesNotMatch(await page.locator('#tenants').textContent(),/할당 0개/);
    assert.match(await page.locator('#usage-reason').textContent(),/조회/);
    checks.push('failed refresh clears stale counts in detail and list');
    failState=false;override=null;await poll();
    await expectText('usage-allocated','2개');
    checks.push('successful refresh restores observed counts');

    await open();
    await page.getByRole('button',{name:/platform-services/}).click();
    await expectText('usage-allocated','미확인');await expectText('usage-waiting','미확인');
    await expectText('usage-guaranteed','미설정');
    checks.push('missing policy is unknown, not zero budget or usage');

    override=structuredClone(fixtures.admin.state);override.mutationsEnabled=false;
    for(const tenant of override.tenants)for(const action of Object.keys(tenant.capabilities))tenant.capabilities[action]=false;
    await open();
    assert.equal(await page.locator('#new').isDisabled(),true,'staged deployment must disable creation');
    assert.equal(await page.locator('#run').isVisible(),false);
    assert.match(await page.locator('#deployment-mode').textContent(),/조회 모드/);
    checks.push('staged deployment exposes read-only controls and explanation');
    override=null;

    for(const selectedRole of ['member','viewer']){
      role=selectedRole;await open();
      assert.equal(await page.locator('#tenants .tenant').count(),1);
      await expectText('usage-allocated','2개');
      assert.equal(await page.locator('#run').isVisible(),selectedRole==='member');
      checks.push(selectedRole+': own-tenant usage with existing action permissions');
    }

    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>window.innerWidth),390);
    const size=await page.evaluate(()=>({width:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth}));
    assert.ok(size.scroll<=size.width,JSON.stringify(size));
    await expectText('usage-allocated','2개');await expectText('usage-waiting','2건');
    checks.push('390px: all usage cards visible without horizontal overflow');
    if(process.env.UI_SCREENSHOT_DIR){
      fs.mkdirSync(process.env.UI_SCREENSHOT_DIR,{recursive:true});
      await page.screenshot({path:path.join(process.env.UI_SCREENSHOT_DIR,'capsule-usage-mobile.png'),fullPage:true});
      await page.setViewportSize({width:1280,height:1000});role='admin';await open();
      await page.screenshot({path:path.join(process.env.UI_SCREENSHOT_DIR,'capsule-usage-desktop.png'),fullPage:true});
    }
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({checks,passed:checks.length,browserErrors:errors},null,2));
  }finally{
    await browser.close();await new Promise(resolve=>server.close(resolve));
  }
})().catch(error=>{console.error(error);server.close();process.exitCode=1;});
