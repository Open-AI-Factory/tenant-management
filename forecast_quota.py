"""Forecast timing/amount policy with an explicitly simulated quota ledger.

No Kubernetes access. Device budgets are not physical allocations or guarantees.
All time is caller supplied so the same external event stream is reproducible.
"""
import copy
import json
import math
import re
from datetime import datetime
from decimal import Decimal, ROUND_CEILING


class ForecastError(ValueError):
    def __init__(self,message,status=400):super().__init__(message);self.status=status


def number(value,minimum=0,maximum=1000000,integer=False):
    if (type(value) not in (int,float) or not minimum<=value<=maximum or
        not math.isfinite(value) or (integer and type(value) is not int)):
        raise ForecastError('Invalid numeric value')
    return value


def timestamp(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)',value):
        raise ForecastError('RFC3339 timestamp required')
    try:
        dt=datetime.fromisoformat(value.replace('Z','+00:00'))
        if dt.tzinfo is None:raise ValueError()
        return dt.timestamp()
    except (ValueError,OverflowError):raise ForecastError('Invalid timezone timestamp')


def required_quota(demand,headroom,minimum):
    amount=Decimal(str(demand))*(Decimal(1)+Decimal(str(headroom)))
    return max(minimum,int(amount.to_integral_value(rounding=ROUND_CEILING)))


def identity(value):
    if not isinstance(value,str) or not 1<=len(value)<=100:raise ForecastError('Invalid identity')
    try:value.encode('utf-8')
    except UnicodeError:raise ForecastError('Invalid Unicode identity')


class QuotaModel:
    """Single caller/serialized use only; the HTTP simulator supplies the lock."""
    def __init__(self,config):
        c=copy.deepcopy(config)
        required={'poolKey','unit','capacity','source','headroomRatio','leadSeconds','cooldownSeconds',
                  'downHoldSeconds','maxAgeSeconds','maxSampleGapSeconds','minConfidence','tenants'}
        if not isinstance(c,dict) or set(c)!=required:raise ForecastError('Invalid config fields')
        for key in ('poolKey','source'):
            identity(c[key])
        if c['unit']!='device':raise ForecastError('Only homogeneous device units supported')
        number(c['capacity'],1,integer=True);number(c['headroomRatio'],0,1);number(c['minConfidence'],0,1)
        for key in ('leadSeconds','cooldownSeconds','downHoldSeconds'):number(c[key],0,3600,True)
        for key in ('maxAgeSeconds','maxSampleGapSeconds'):number(c[key],1,3600,True)
        if not isinstance(c['tenants'],list) or not 1<=len(c['tenants'])<=100:raise ForecastError('Invalid tenants')
        self.tenants={}
        for t in c['tenants']:
            if not isinstance(t,dict) or set(t)!={'name','uid','minimum','maximum','current'}:raise ForecastError('Invalid tenant config')
            for key in ('name','uid'):
                identity(t[key])
            if t['uid'] in self.tenants:raise ForecastError('Duplicate tenant UID')
            for key in ('minimum','maximum','current'):number(t[key],1,c['capacity'],True)
            if not t['minimum']<=t['current']<=t['maximum']:raise ForecastError('Invalid quota bounds')
            self.tenants[t['uid']]=t
        if sum(t['current'] for t in self.tenants.values())>c['capacity']:raise ForecastError('Initial quota exceeds capacity')
        self.config=c;self.quotas={u:t['current'] for u,t in self.tenants.items()}
        self.latest={};self.receipts={};self.last_change={};self.low_since={};self.low_at={};self.low_sequence={};self.low_target={};self.low_count={}
        self.history=[];self.now=None

    def clock(self,now):
        number(now,0,1e12)
        if self.now is not None and now<self.now:raise ForecastError('Clock cannot move backwards',409)

    def receive(self,event,now):
        self.clock(now)
        required={'eventId','source','tenantUID','poolKey','unit','sequence','issuedAt','windowStart','windowEnd','expectedDevices','confidence'}
        if not isinstance(event,dict) or set(event)!=required:raise ForecastError('Invalid forecast fields')
        e=copy.deepcopy(event)
        for key in ('eventId','tenantUID','source','poolKey','unit'):
            identity(e[key])
        if e['tenantUID'] not in self.tenants:raise ForecastError('Unknown tenant UID',409)
        if any(e[k]!=self.config[k] for k in ('source','poolKey','unit')):raise ForecastError('Source/pool/unit mismatch',409)
        number(e['sequence'],1,1e12,True);number(e['expectedDevices']);number(e['confidence'],0,1)
        issued,start,end=[timestamp(e[k]) for k in ('issuedAt','windowStart','windowEnd')]
        signature=json.dumps(e,sort_keys=True,separators=(',',':'),allow_nan=False)
        if e['eventId'] in self.receipts:
            if self.receipts[e['eventId']]!=signature:raise ForecastError('Conflicting eventId',409)
            return {'status':'Duplicate','eventId':e['eventId']}
        if issued>now+5 or now-issued>self.config['maxAgeSeconds'] or end<=now or end<=start or end<=issued:
            raise ForecastError('Future, stale or expired forecast')
        old=self.latest.get(e['tenantUID'])
        if old and (e['sequence']<=old['sequence'] or issued<old['_issued']):raise ForecastError('Out-of-order forecast',409)
        if len(self.receipts)>=1000:raise ForecastError('Simulation inbox full; start a new simulation',429)
        uid=e['tenantUID'];t=self.tenants[uid]
        desired=min(t['maximum'],required_quota(e['expectedDevices'],self.config['headroomRatio'],t['minimum']))
        # Every accepted sample participates in continuity, even if replaced
        # before the next evaluation. A lapse in validity also breaks the hold.
        if ((old and (old['_end']<now or now-old['_issued']>self.config['maxAgeSeconds'])) or
            start>now or e['confidence']<self.config['minConfidence'] or desired>=self.quotas[uid] or
            desired!=self.low_target.get(uid,desired)):
            self.clear_low(uid)
        e.update(_issued=issued,_start=start,_end=end)
        self.receipts[e['eventId']]=signature;self.latest[e['tenantUID']]=e;self.now=now
        return {'status':'Accepted','eventId':e['eventId']}

    def clear_low(self,uid):
        for data in (self.low_since,self.low_at,self.low_sequence,self.low_target,self.low_count):data.pop(uid,None)

    def step(self,now,observations):
        if not isinstance(observations,dict) or set(observations)-set(self.tenants):raise ForecastError('Invalid observations')
        for observation in observations.values():
            if (not isinstance(observation,dict) or set(observation)!={'idle','observedAt'} or
                (observation['idle'] is not None and type(observation['idle']) is not bool)):
                raise ForecastError('Invalid observation fields')
            number(observation['observedAt'],0,1e12)
        self.clock(now);self.now=now;c=self.config;rows=[]
        for uid,t in self.tenants.items():
            before=self.quotas[uid];e=self.latest.get(uid)
            row=dict(tenantUID=uid,name=t['name'],fromQuota=before,targetQuota=before,appliedQuota=before,
                     requestedQuota=None,desiredQuota=None,shortfall=None,status='Hold',reason='NoForecast',at=now,eventId=e['eventId'] if e else None)
            invalid=('NoForecast' if not e else 'ExpiredForecast' if now>=e['_end'] else
                     'StaleForecast' if now-e['_issued']>c['maxAgeSeconds'] else
                     'LowConfidence' if e['confidence']<c['minConfidence'] else None)
            if invalid:
                row['reason']=invalid;self.clear_low(uid);rows.append(row);continue
            requested=required_quota(e['expectedDevices'],c['headroomRatio'],t['minimum'])
            desired=min(t['maximum'],requested);available=c['capacity']-sum(self.quotas.values())
            target=min(desired,before+available) if desired>before else desired
            row.update(requestedQuota=requested,desiredQuota=desired,targetQuota=target,eligibleAt=e['_start']-c['leadSeconds'])
            if desired<before and now>=e['_start'] and 0<=now-e['_issued']<=c['maxSampleGapSeconds']:
                if (uid in self.low_at and e['_issued']-self.low_at[uid]>c['maxSampleGapSeconds']) or desired!=self.low_target.get(uid,desired):self.clear_low(uid)
                if self.low_sequence.get(uid)!=e['sequence']:
                    if e['_issued']>self.low_at.get(uid,float('-inf')):self.low_count[uid]=self.low_count.get(uid,0)+1
                    self.low_since.setdefault(uid,now);self.low_at[uid]=e['_issued'];self.low_sequence[uid]=e['sequence'];self.low_target[uid]=desired
            else:self.clear_low(uid)
            if desired>before and now<e['_start']-c['leadSeconds']:row['reason']='BeforeLeadWindow'
            elif desired<before and now<e['_start']:row['reason']='BeforeDemandWindow'
            elif target==before:row['reason']='CapacityLimited' if requested>before else 'WithinBounds'
            elif now-self.last_change.get(uid,float('-inf'))<c['cooldownSeconds']:row['reason']='Cooldown'
            elif target<before and (uid not in self.low_since or now-self.low_since[uid]<c['downHoldSeconds'] or
                                    (c['downHoldSeconds']>0 and self.low_count.get(uid,0)<2)):row['reason']='DownscaleHold'
            else:
                observation=observations.get(uid,{})
                observed=observation.get('observedAt');idle=observation.get('idle')
                if (type(observed) not in (int,float) or not math.isfinite(observed) or
                    not 0<=now-observed<=30 or idle is not True):
                    row.update(status='Deferred',reason='ActiveWorkloads' if idle is False else 'UnknownObservation')
                else:
                    self.quotas[uid]=target;self.last_change[uid]=now;self.clear_low(uid)
                    row.update(status='SimulatedApplied',reason='CapacityLimited' if target<desired else 'TenantMaximum' if desired<requested else 'ScaleUp' if target>before else 'ScaleDown')
            row.update(appliedQuota=self.quotas[uid],shortfall=max(0,requested-self.quotas[uid]));rows.append(row)
        self.history.extend(copy.deepcopy(rows));self.history=self.history[-400:]
        return rows

    def snapshot(self):
        return copy.deepcopy(dict(mode='simulation',config=self.config,now=self.now,quotas=self.quotas,
                                  history=self.history,receivedEvents=len(self.receipts)))
