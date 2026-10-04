'use strict';
const $=id=>document.getElementById(id);
let state=null,csrf=null,sending=false,inFlight=false,requestNumber=0,renderedRequest=0;
const phases={Deferred:'보류',Applying:'반영 중',ApplyUnknown:'반영 결과 미확인',Conflict:'외부 변경 충돌',QuotaVerified:'실제 큐 설정 확인',Unavailable:'미확인'};
const reasons={NoForecast:'예측 없음',Disabled:'제어 비활성',NominalQuotaReadback:'nominalQuota 재조회 완료',Capacity:'테스트 설정 합 한도 부족',LeadTime:'증설 시점 대기',DownHold:'새로운 낮은 수요 표본 확인 중',Cooldown:'변경 간격 대기',PolicyChanged:'정책 변경: 새 예측 필요',StaleForecast:'오래되거나 만료된 예측',PoolObservationUnknown:'풀 조회 미확인',ExternalEdit:'등록 후 외부에서 설정 변경',CapsuleQuotaUnresolved:'Capsule GPU 수량 쿼터 해석 미확인',PendingReadback:'변경 기록 복구·재조회 대기'};
function node(tag,text,cls){const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el;}
function value(v){return v===null||v===undefined?'미확인':String(v);}
function message(text,error=false){$('message').textContent=text;$('message').className=error?'error':'';}
async function api(path,raw){
  const options=raw===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:raw};
  const response=await fetch(path,{...options,credentials:'same-origin',cache:'no-store'});
  const data=await response.json();if(!response.ok)throw new Error(data.error||'요청 실패');return data;
}
function render(data){
  state=data;$('mode').textContent=data.enabled?'실제 제어 활성 · 접수 잠금':'실제 제어 비활성';
  $('updated').textContent='서버 UTC '+(data.serverTime||'미확인');
  const selected=$('tenant').value;$('tenant').replaceChildren();$('tenants').replaceChildren();
  for(const [uid,row] of Object.entries(data.tenants||{})){
    const p=row.policy,b=row.binding,name=p.tenantName;
    const option=node('option',name);option.value=uid;$('tenant').append(option);
    const card=node('article',undefined,'card');card.id='card-'+name;
    card.append(node('h2',name),node('p','Capsule UID: '+uid+' · 정책 revision '+p.revision,'identity'));
    const metrics=node('div',undefined,'metrics');
    for(const [label,amount,id] of [['더미 예상 수요',row.forecast?.event?.expectedDevices],['판단 목표',row.target],['실제 큐 예산',row.actual?.quota,'quota-'+name]]){
      const group=node('div'),number=node('strong',value(amount));if(id)number.id=id;
      group.append(node('small',label+' (device)'),number);metrics.append(group);
    }
    card.append(metrics,node('div',`정책 한도 ${p.minDevices}~${p.maxDevices}개 · 증설 ${p.leadSeconds}초 전 · 축소 ${p.downHoldSeconds}초 확인 · 변경 간격 ${p.cooldownSeconds}초`,'policy'));
    const phase=node('p',(phases[row.phase]||row.phase)+' · '+(reasons[row.reason]||row.reason),'phase');phase.id='phase-'+name;card.append(phase);
    const own=b.capsuleQuota?.state==='NotConfigured'?'미설정':b.capsuleQuota?.state==='Present'?'설정 있음':'미확인';
    const condition=b.controller?.conditions?.find(c=>c.type==='Active');
    card.append(node('div',`Namespace: ${p.namespaces.map(n=>n.name).join(', ')}\nLocalQueue: ${p.namespaces.map(n=>n.localQueue.name).join(', ')}\nClusterQueue: ${p.clusterQueue.name} · UID ${p.clusterQueue.uid}\nCapsule 자체 자원량 쿼터: ${own} · 큐와 자동 동기화 없음\n접수: ${b.clusterQueue?.spec?.stopPolicy||'미확인'} · 실행 잠금\nKueue controller: ${condition?condition.status+' / '+condition.reason:'미확인'} · effective 예산: 미확인\n사용/예약 관측: ${value(b.usage)} / ${value(b.reservation)} · RV ${value(row.actual?.resourceVersion)} · generation ${value(row.actual?.generation)}\nKAI: ${p.placement.kaiQueueName} / ${p.placement.schedulerName}\n새 테넌트 접수 범위: ${row.execution?.admissionCoverage?.state||'Unknown'} · GPU 실행 비활성`,'detail'));
    card.lastChild.style.whiteSpace='pre-line';
    if(row.forecast?.demandExceedsPolicy)card.append(node('p','예상 수요가 정책 최대 한도를 초과합니다. 목표량은 정책 안으로 제한됩니다.','phase'));
    const history=node('details',undefined,'history');history.append(node('summary','확인 이력 '+(row.history||[]).length+'건'));
    const ul=node('ul');for(const item of (row.history||[]).slice(-8).reverse())ul.append(node('li',`${item.at} · ${item.from} → ${item.to} · RV ${item.resourceVersion}`));history.append(ul);card.append(history);
    $('tenants').append(card);
  }
  if([...$('tenant').options].some(o=>o.value===selected))$('tenant').value=selected;
  if(!Object.keys(data.tenants||{}).length)$('tenants').append(node('p','정책·연결을 확인할 수 없습니다: '+(data.reason||'미확인')));
  $('prepare').disabled=!data.enabled||!$('tenant').value;
  $('send').disabled=sending||!data.enabled||!$('tenant').value;
}
async function refresh(){
  if(inFlight)return;inFlight=true;const number=++requestNumber;
  try{const data=await api('/api/kueue-quota/state');if(number>=renderedRequest){renderedRequest=number;render(data);}}
  catch(e){$('updated').textContent='현재 조회 실패 · 표시된 값은 마지막 조회 결과';message(e.message,true);}
  finally{inFlight=false;}
}
$('prepare').addEventListener('click',()=>{
  const uid=$('tenant').value,row=state?.tenants?.[uid];if(!row)return;
  const issued=new Date(state.serverTime),delay=Number($('delay').value),demand=Number($('demand').value);
  if(!Number.isFinite(demand)||demand<0||!Number.isInteger(delay)||delay<0){message('수요와 시작 시각을 확인하세요.',true);return;}
  $('packet').value=JSON.stringify({eventId:crypto.randomUUID(),source:row.policy.source,tenantUID:uid,poolKey:row.policy.poolKey,unit:row.policy.unit,sequence:(row.lastSequence||0)+1,issuedAt:issued.toISOString(),windowStart:new Date(+issued+delay*1000).toISOString(),windowEnd:new Date(+issued+(delay+120)*1000).toISOString(),expectedDevices:demand,confidence:.95},null,2);
  message('시험용 더미 예측을 만들었습니다. 전송하면 서버가 실제 정책으로 판단합니다.');
});
$('send').addEventListener('click',async()=>{
  if(sending)return;const raw=$('packet').value;sending=true;$('send').disabled=true;
  try{const accepted=await api('/api/kueue-quota/forecasts',raw);message(accepted.status==='Duplicate'?'중복 예측: 기존 수신 기록을 유지했습니다.':'예측 수신 완료 · 실제 반영 여부는 큐 상태에서 확인하세요.');await refresh();}
  catch(e){message(e.message,true);}finally{sending=false;$('send').disabled=!state?.enabled;}
});
$('refresh').addEventListener('click',refresh);
(async()=>{try{const session=await api('/api/session');csrf=session.csrf;await refresh();setInterval(refresh,2000);}catch(e){message(e.message,true);$('send').disabled=true;$('prepare').disabled=true;}})();
