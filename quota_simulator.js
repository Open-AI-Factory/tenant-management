'use strict';
const $=id=>document.getElementById(id);
const apiBase=document.body.dataset.apiBase||'/api';
const session=apiBase==='/api'?Promise.resolve(null):fetch('/api/session').then(async res=>{const data=await res.json();if(!res.ok)throw new Error(data.error||res.status);if(data.principal.platformAdmin!==true)throw new Error('플랫폼 관리자 로그인이 필요합니다.');return data;});
const reasons={NoForecast:'예측 대기',BeforeLeadWindow:'증설 시점 대기',BeforeDemandWindow:'사용 구간 시작 대기',CapacityLimited:'전체 예산 부족',WithinBounds:'현재 예산 유지',Cooldown:'변경 후 안정화 대기',DownscaleHold:'낮은 수요 지속 확인 중',ActiveWorkloads:'작업 중 · 적용 보류',UnknownObservation:'관측 미확인 · 적용 보류',ScaleUp:'증설',ScaleDown:'축소',TenantMaximum:'테넌트 최대 한도',StaleForecast:'오래된 예측 · 변경 보류',ExpiredForecast:'예측 기간 종료 · 변경 보류',LowConfidence:'낮은 신뢰도 · 변경 보류'};
const statuses={Hold:'유지',Deferred:'보류',SimulatedApplied:'모의 적용'};
const colors=['#82dbcb','#9cbcff','#e8be85'];
let state,busy=false,viewRevision=0,actionError='',connectionError='';
function showErrors(){$('message').textContent=[connectionError,actionError].filter(Boolean).join(' · ');}
function stateReceived(s){render(s);connectionError='';showErrors();}
function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function fmt(value){return value===null||value===undefined?'—':String(value);}
function clock(t){return new Date(t*1000).toISOString().slice(11,19)+' UTC';}
async function api(path,body){const auth=await session;const headers={'Content-Type':'application/json'};if(auth)headers['X-CSRF-Token']=auth.csrf;const res=await fetch(apiBase+path.slice(4),body===undefined?{}:{method:'POST',headers,body:JSON.stringify(body)});const data=await res.json();if(!res.ok)throw new Error(data.error||res.status);return data;}
function render(s){
  state=s;const c=s.config;const sum=Object.values(s.quotas).reduce((a,b)=>a+b,0);
  $('clock').textContent=clock(s.now);$('progress').textContent=`${s.scenarioIndex} / ${s.scenarioLength}`;
  $('step-label').textContent=s.label==='Ready'?'A·B 수요 변화 실험':s.label;
  $('policy').textContent=`증설 ${c.leadSeconds}초 전 · 축소 ${c.downHoldSeconds}초 확인 · 안정화 ${c.cooldownSeconds}초`;
  $('budget').textContent=`전체 ${c.capacity}개 중 ${sum}개 설정 · 여유 ${c.capacity-sum}개`;
  $('receipt').textContent=s.lastReceipt?`${s.lastReceipt.status} · ${s.lastReceipt.eventId} (${s.receivedEvents}개 고유 예측)`:'아직 수신하지 않음';
  $('capacity').replaceChildren();$('tenants').replaceChildren();
  c.tenants.forEach((t,i)=>{
    const uid=t.uid,suffix=['a','b'][i]||String(i),f=s.latest[uid],row=[...s.history].reverse().find(r=>r.tenantUID===uid);
    const bar=el('i');bar.style.width=(s.quotas[uid]/c.capacity*100)+'%';bar.style.background=colors[i%colors.length];$('capacity').append(bar);
    const card=el('article',undefined,'tenant');card.style.setProperty('--accent',colors[i%colors.length]);
    const heading=el('div',undefined,'tenant-heading');heading.append(el('h2','Tenant '+t.name),el('span',`최소 ${t.minimum} / 최대 ${t.maximum}개`));card.append(heading);
    const metrics=el('div',undefined,'metrics');
    [['현재 모의 예산',s.quotas[uid],'quota'],['예측 사용량',f?.expectedDevices,'demand'],['충족 못한 예산',row?.shortfall,'shortfall']].forEach(([label,value,id])=>{const m=el('div',undefined,'metric');const b=el('b',fmt(value));b.id=id+'-'+suffix;m.append(el('small',label),b);metrics.append(m);});card.append(metrics);
    const decision=el('div',`${statuses[row?.status]||'대기'} · ${reasons[row?.reason]||'예측 대기'}`,'decision');decision.id='reason-'+suffix;card.append(decision);
    card.append(el('p',f?`예측 사용 구간 ${clock(Date.parse(f.windowStart)/1000)}부터 · 신뢰도 ${Math.round(f.confidence*100)}% · 목표 ${fmt(row?.targetQuota)}개`:'아직 유효한 예측 데이터가 없습니다.','detail'));
    $('tenants').append(card);
  });
  $('history').replaceChildren();
  if(!s.history.length){const tr=el('tr');const td=el('td','첫 단계를 진행하면 판단 기록이 표시됩니다.');td.colSpan=6;tr.append(td);$('history').append(tr);}
  s.history.slice(-12).reverse().forEach(r=>{const tr=el('tr');[clock(r.at),r.name,fmt(r.requestedQuota),fmt(r.targetQuota),`${r.fromQuota} → ${r.appliedQuota}`,`${statuses[r.status]} · ${reasons[r.reason]||r.reason}`].forEach(v=>tr.append(el('td',v)));if(r.status==='SimulatedApplied')tr.className='applied';$('history').append(tr);});
  // Preserve manual edits during periodic polling; rebuild selectors only after explicit actions.
  if(!$('observations').children.length){c.tenants.forEach(t=>{const label=el('label','Tenant '+t.name);const select=el('select');select.dataset.uid=t.uid;select.setAttribute('aria-label','Tenant '+t.name+' 모의 작업 상태');[['unknown','미확인'],['idle','작업 없음'],['busy','작업 중']].forEach(([value,text])=>{const o=el('option',text);o.value=value;select.append(o);});label.append(select);$('observations').append(label);});}
  $('next').disabled=busy||s.scenarioIndex>=s.scenarioLength;
}
function updateObservations(){for(const select of $('observations').querySelectorAll('select')){const v=state.observations[select.dataset.uid]?.idle;select.value=v===true?'idle':v===false?'busy':'unknown';}}
function observations(now){return Object.fromEntries([...$('observations').querySelectorAll('select')].map(s=>[s.dataset.uid,{idle:s.value==='idle'?true:s.value==='busy'?false:null,observedAt:now}]));}
async function action(fn,syncObservations=false){if(busy)return;busy=true;viewRevision++;document.querySelectorAll('button').forEach(b=>b.disabled=true);actionError='';showErrors();try{await fn();stateReceived(await api('/api/state'));if(syncObservations)updateObservations();}catch(e){actionError=e.message;showErrors();}finally{busy=false;document.querySelectorAll('button').forEach(b=>b.disabled=false);if(state)$('next').disabled=state.scenarioIndex>=state.scenarioLength;}}
$('next').onclick=()=>action(()=>api('/api/scenario/next',{}),true);
$('reset').onclick=()=>action(()=>api('/api/reset',{}),true);
$('send').onclick=()=>action(()=>api('/api/forecasts',JSON.parse($('packet').value)));
$('evaluate').onclick=()=>action(async()=>{await api('/api/advance',{now:state.now,observations:observations(state.now)});await api('/api/evaluate',{});});
$('advance').onclick=()=>action(async()=>{const now=state.now+30;await api('/api/advance',{now,observations:observations(now)});await api('/api/evaluate',{});});
(async()=>{try{stateReceived(await api('/api/state'));updateObservations();const c=state.config,t=c.tenants[0],now=state.now;const iso=seconds=>new Date(seconds*1000).toISOString();$('packet').value=JSON.stringify({eventId:'manual-1',source:c.source,tenantUID:t.uid,poolKey:c.poolKey,unit:c.unit,sequence:1,issuedAt:iso(now),windowStart:iso(now+60),windowEnd:iso(now+300),expectedDevices:3,confidence:.95},null,2);}catch(e){connectionError='서버 연결 미확인: '+e.message;showErrors();}})();
setInterval(async()=>{if(busy)return;const revision=++viewRevision;try{const s=await api('/api/state');if(!busy&&revision===viewRevision)stateReceived(s);}catch(e){if(!busy&&revision===viewRevision){connectionError='서버 연결 미확인: '+e.message;showErrors();}}},2500);
