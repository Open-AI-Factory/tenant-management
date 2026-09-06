# -*- coding: utf-8 -*-
"""Separate external-module stand-in; sends JSON over real loopback HTTP."""
import argparse
import json
import urllib.request
from datetime import datetime, timezone

START=1791244800


def stamp(seconds):return datetime.fromtimestamp(START+seconds,timezone.utc).isoformat().replace('+00:00','Z')


def prediction(uid,seq,demand,issued,start):
    return dict(eventId=uid+'-'+str(seq),source='dummy-profiler',tenantUID=uid,poolKey='mig-demo',unit='device',
                sequence=seq,issuedAt=stamp(issued),windowStart=stamp(start),windowEnd=stamp(600),
                expectedDevices=demand,confidence=.95)


def scenario():
    a=lambda seq,demand,at,start:prediction('tenant-a',seq,demand,at,start)
    b=lambda seq,demand,at,start:prediction('tenant-b',seq,demand,at,start)
    rows=[
        (0,'A가 60초부터 3개 필요하다는 예측 수신',[a(1,3,0,60)],True),
        (30,'수요 시작 30초 전: A 증설',[],True),
        (30,'B도 3개 요청: 전체 예산 부족',[b(1,3,30,60)],True),
        (30,'B 예측 재전송: 중복 수신만 확인',[b(1,3,30,60)],True),
        (60,'A 수요 감소: 축소 대기 시작',[a(2,1,60,60)],True),
        (90,'A 낮은 수요 재확인: 아직 유지',[a(3,1,90,90)],True),
        (120,'60초 확인 후 A 축소 → B 증설',[a(4,1,120,120)],True),
        (150,'B 수요 감소 / A 증가 예측, A 작업 중',[b(2,1,150,150),a(5,3,150,180)],False),
        (180,'B 낮은 수요 재확인',[b(3,1,180,180)],False),
        (210,'B 축소: 여유 예산 확보',[b(4,1,210,210)],False),
        (211,'A는 작업 중이므로 증설 보류',[],False),
        (212,'A 작업 종료 관측 후 증설',[],True),
        (360,'예측 갱신 중단: 오래된 데이터로 변경하지 않음',[],True)]
    return [dict(at=START+t,label=label,forecasts=events,observations={
        'tenant-a':dict(idle=idle,observedAt=START+t),'tenant-b':dict(idle=True,observedAt=START+t)})
        for t,label,events,idle in rows]


def send(url,path,body):
    req=urllib.request.Request(url+path,data=json.dumps(body,allow_nan=False).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=10) as response:return json.load(response)


def replay(url):
    send(url,'/api/reset',{});states=[]
    for step in scenario():
        send(url,'/api/advance',dict(now=step['at'],observations=step['observations']))
        for event in step['forecasts']:send(url,'/api/forecasts',event)
        state=send(url,'/api/evaluate',{});state['label']=step['label'];states.append(state)
    return states


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--url',default='http://127.0.0.1:8894')
    parser.add_argument('--event',help='JSON forecast file; omit to reset and replay the built-in scenario')
    args=parser.parse_args()
    if args.event:
        with open(args.event,encoding='utf-8') as source:result=send(args.url,'/api/forecasts',json.load(source))
    else:result=[dict(label=s['label'],now=s['now'],quotas=s['quotas'],decisions=s['history'][-2:]) for s in replay(args.url)]
    print(json.dumps(result,ensure_ascii=False,indent=2))
