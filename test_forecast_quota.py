"""Expectations are hand calculated, using virtual seconds, not the policy formula."""
import copy
import importlib.util
import unittest
from datetime import datetime, timezone

T=1791244800
def stamp(seconds):return datetime.fromtimestamp(T+seconds,timezone.utc).isoformat().replace('+00:00','Z')
def config():return {'poolKey':'mig-demo','unit':'device','capacity':4,'source':'dummy-profiler',
    'headroomRatio':0,'leadSeconds':30,'cooldownSeconds':30,'downHoldSeconds':60,
    'maxAgeSeconds':120,'maxSampleGapSeconds':30,'minConfidence':0.8,
    'tenants':[{'name':'A','uid':'tenant-a','minimum':1,'maximum':3,'current':1},
               {'name':'B','uid':'tenant-b','minimum':1,'maximum':3,'current':1}]}
def forecast(seq=1,demand=3,issued=0,start=60,end=300,uid='tenant-a',**extra):
    return dict({'eventId':uid+'-'+str(seq),'source':'dummy-profiler','tenantUID':uid,'poolKey':'mig-demo',
        'unit':'device','sequence':seq,'issuedAt':stamp(issued),'windowStart':stamp(start),
        'windowEnd':stamp(end),'expectedDevices':demand,'confidence':0.95},**extra)

class ForecastTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('forecast_quota'),'forecast quota controller required')
        from forecast_quota import QuotaModel, ForecastError
        self.Model=QuotaModel;self.Error=ForecastError;self.m=QuotaModel(config())
    def step(self,t,idle=True):return self.m.step(T+t,{u:{'idle':idle,'observedAt':T+t} for u in ('tenant-a','tenant-b')})
    def row(self,result,uid='tenant-a'):return next(r for r in result if r['tenantUID']==uid)
    def test_growth_occurs_at_lead_boundary_not_receipt(self):
        self.m.receive(forecast(),T)
        self.assertEqual(self.row(self.step(0))['reason'],'BeforeLeadWindow')
        self.step(29);self.assertEqual(self.m.quotas['tenant-a'],1)
        r=self.row(self.step(30));self.assertEqual((r['fromQuota'],r['targetQuota'],r['status']),(1,3,'SimulatedApplied'))
        self.assertEqual(self.m.quotas,{'tenant-a':3,'tenant-b':1})
    def test_capacity_shortage_never_takes_other_tenants_budget(self):
        self.m.receive(forecast(start=0),T);self.step(0)
        self.m.receive(forecast(uid='tenant-b',start=0),T)
        r=self.row(self.step(0),'tenant-b')
        self.assertEqual((r['reason'],r['shortfall'],r['targetQuota']),('CapacityLimited',2,1))
        self.assertEqual(self.m.quotas,{'tenant-a':3,'tenant-b':1})
    def test_fractional_demand_headroom_rounding_and_ceiling(self):
        c=config();c['headroomRatio']=0.2;self.m=self.Model(c)
        self.m.receive(forecast(demand=1.8,start=0),T);self.step(0)
        self.assertEqual(self.m.quotas['tenant-a'],3)
        self.m.receive(forecast(seq=2,demand=10,issued=1,start=1),T+1)
        r=self.row(self.step(1));self.assertEqual((r['requestedQuota'],r['desiredQuota'],r['shortfall']),(12,3,9))
    def test_shrink_requires_distinct_sustained_fresh_samples(self):
        self.m.receive(forecast(start=0),T);self.step(0)
        for seq,t in enumerate((30,60),2):
            self.m.receive(forecast(seq=seq,demand=1,issued=t,start=t),T+t);self.step(t)
            self.assertEqual(self.m.quotas['tenant-a'],3)
        self.m.receive(forecast(seq=4,demand=1,issued=90,start=90),T+90);self.step(90)
        self.assertEqual(self.m.quotas['tenant-a'],1)
    def test_tick_and_duplicate_do_not_count_as_new_low_samples(self):
        self.m.receive(forecast(start=0),T);self.step(0)
        low=forecast(seq=2,demand=1,issued=30,start=30);self.m.receive(low,T+30);self.step(30)
        self.assertEqual(self.m.receive(low,T+60)['status'],'Duplicate')
        for t in (60,90):self.step(t)
        self.assertEqual(self.m.quotas['tenant-a'],3)
    def test_gap_and_low_confidence_reset_shrink_hold(self):
        self.m.receive(forecast(start=0),T);self.step(0)
        for seq,t,confidence in ((2,30,.95),(3,60,.2),(4,90,.95),(5,121,.95)):
            self.m.receive(forecast(seq=seq,demand=0,issued=t,start=t,confidence=confidence),T+t);self.step(t)
        self.assertEqual(self.m.quotas['tenant-a'],3)
        self.assertEqual(self.m.low_since['tenant-a'],T+121)
    def test_busy_and_unknown_observations_defer_actual_change(self):
        self.m.receive(forecast(start=0),T)
        for idle in (False,None):
            r=self.row(self.step(0,idle));self.assertEqual(r['status'],'Deferred')
            self.assertEqual(self.m.quotas['tenant-a'],1)
        self.step(0,True);self.assertEqual(self.m.quotas['tenant-a'],3)
    def test_expiry_and_stale_forecast_never_apply(self):
        self.m.receive(forecast(start=100,end=200),T)
        self.assertEqual(self.row(self.step(121))['reason'],'StaleForecast')
        self.assertEqual(self.row(self.step(200))['reason'],'ExpiredForecast')
        self.assertEqual(self.m.quotas['tenant-a'],1)
    def test_cooldown_prevents_immediate_reexpansion(self):
        c=config();c['downHoldSeconds']=0;self.m=self.Model(c)
        self.m.receive(forecast(start=0),T);self.step(0)
        self.m.receive(forecast(seq=2,demand=1,issued=30,start=30),T+30);self.step(30)
        self.m.receive(forecast(seq=3,demand=3,issued=31,start=31),T+31)
        self.assertEqual(self.row(self.step(31))['reason'],'Cooldown')
        self.step(60);self.assertEqual(self.m.quotas['tenant-a'],3)
    def test_replay_conflict_order_identity_and_source_are_rejected(self):
        f=forecast();self.m.receive(f,T)
        for bad in (dict(f,expectedDevices=2),dict(f,eventId='other'),forecast(seq=2,uid='recreated-a'),
                    forecast(seq=2,source='unknown'),forecast(seq=2,unit='percent'),forecast(seq=2,poolKey='other')):
            with self.assertRaises(self.Error):self.m.receive(bad,T)
        self.assertEqual(self.m.latest['tenant-a']['sequence'],1)
    def test_invalid_numeric_and_time_values_have_no_side_effects(self):
        for field,value in [('expectedDevices',True),('expectedDevices',float('nan')),('confidence',float('inf')),
                            ('sequence',True),('issuedAt','bad'),('windowEnd',stamp(-1)),('issuedAt',stamp(10)),
                            ('expectedDevices',-1),('confidence',1.1),('expectedDevices',10**1000)]:
            with self.subTest(field=field,value=value):
                with self.assertRaises(self.Error):self.m.receive(forecast(**{field:value}),T)
        self.assertEqual(self.m.latest,{})
    def test_invalid_config_and_backward_clock_rejected(self):
        for edit in ({'capacity':1},{'headroomRatio':float('nan')},{'maxSampleGapSeconds':0}):
            with self.assertRaises(self.Error):self.Model(dict(config(),**edit))
        self.step(1)
        with self.assertRaises(self.Error):self.step(0)
    def test_observation_age_and_minimum_are_enforced(self):
        self.m.receive(forecast(start=0),T)
        r=self.row(self.m.step(T+40,{'tenant-a':{'idle':True,'observedAt':T}}))
        self.assertEqual(r['status'],'Deferred');self.assertEqual(self.m.quotas['tenant-a'],1)
        self.m.receive(forecast(seq=2,demand=0,issued=40,start=40),T+40);self.step(40)
        self.assertEqual(self.m.quotas['tenant-a'],1)

    def test_lower_target_requires_its_own_hold_period(self):
        self.m.receive(forecast(start=0),T);self.step(0)
        for seq,t,demand in ((2,30,2),(3,60,2),(4,90,1)):
            self.m.receive(forecast(seq=seq,demand=demand,issued=t,start=t),T+t);self.step(t)
        self.assertEqual(self.m.quotas['tenant-a'],3)

    def test_invalid_observations_do_not_partially_change_state(self):
        self.m.receive(forecast(start=0),T)
        before=self.m.snapshot()
        for obs in (None,[],{'tenant-a':None},{'tenant-a':{'idle':'yes','observedAt':T}}):
            with self.assertRaises(self.Error):self.m.step(T,obs)
            self.assertEqual(self.m.snapshot(),before)

    def test_old_issued_times_cannot_fake_fresh_low_samples(self):
        self.m.receive(forecast(start=0),T);self.step(0)
        for seq,t in ((2,30),(3,60),(4,90)):
            self.m.receive(forecast(seq=seq,demand=1,issued=30,start=30),T+t);self.step(t)
        self.assertEqual(self.m.quotas['tenant-a'],3)

    def test_replaced_bad_sample_breaks_low_demand_even_without_tick(self):
        for bad in (dict(confidence=.2,demand=1),dict(demand=3)):
            self.m=self.Model(config());self.m.receive(forecast(start=0),T);self.step(0)
            for seq,t in ((2,30),(3,60)):
                self.m.receive(forecast(seq=seq,demand=1,issued=t,start=t),T+t);self.step(t)
            self.m.receive(forecast(seq=4,issued=70,start=70,**bad),T+70)
            self.m.receive(forecast(seq=5,demand=1,issued=90,start=90),T+90);self.step(90)
            self.assertEqual(self.m.quotas['tenant-a'],3)

    def test_replacement_after_expiry_breaks_low_demand_continuity(self):
        self.m.receive(forecast(start=0),T);self.step(0)
        for seq,t in ((2,30),(3,60)):
            self.m.receive(forecast(seq=seq,demand=1,issued=t,start=t,end=65),T+t);self.step(t)
        self.m.receive(forecast(seq=4,demand=1,issued=75,start=75),T+75);self.step(75)
        self.m.receive(forecast(seq=5,demand=1,issued=90,start=90),T+90);self.step(90)
        self.assertEqual(self.m.quotas['tenant-a'],3)

    def test_consistent_delivery_latency_does_not_break_sample_cadence(self):
        self.m.receive(forecast(start=0),T);self.step(0)
        for seq,t in ((2,30),(3,60),(4,90)):
            self.m.receive(forecast(seq=seq,demand=1,issued=t,start=t),T+t+1);self.step(t+1)
        self.assertEqual(self.m.quotas['tenant-a'],1)

    def test_future_window_replacement_interrupts_current_low_evidence(self):
        self.m.receive(forecast(start=0),T);self.step(0)
        for seq,t in ((2,30),(3,60)):
            self.m.receive(forecast(seq=seq,demand=1,issued=t,start=t),T+t);self.step(t)
        self.m.receive(forecast(seq=4,demand=1,issued=70,start=200),T+70)
        self.m.receive(forecast(seq=5,demand=1,issued=90,start=90),T+90);self.step(90)
        self.assertEqual(self.m.quotas['tenant-a'],3)

    def test_decimal_headroom_does_not_add_an_extra_device(self):
        c=config();c['capacity']=20;c['headroomRatio']=.12;c['tenants'][0]['maximum']=19;self.m=self.Model(c)
        self.m.receive(forecast(demand=12.5,start=0),T)
        r=self.row(self.step(0));self.assertEqual((r['requestedQuota'],r['appliedQuota']),(14,14))

    def test_intermediate_rise_below_current_quota_breaks_lower_target_hold(self):
        self.m.receive(forecast(start=0),T);self.step(0)
        for seq,t in ((2,30),(3,60)):
            self.m.receive(forecast(seq=seq,demand=1,issued=t,start=t),T+t);self.step(t)
        self.m.receive(forecast(seq=4,demand=2,issued=70,start=70),T+70)
        self.m.receive(forecast(seq=5,demand=1,issued=90,start=90),T+90);self.step(90)
        self.assertEqual(self.m.quotas['tenant-a'],3)

    def test_positive_hold_requires_multiple_distinct_issued_samples(self):
        c=config();c['downHoldSeconds']=30;self.m=self.Model(c)
        self.m.receive(forecast(start=0),T);self.step(0)
        self.m.receive(forecast(seq=2,demand=1,issued=30,start=30),T+30);self.step(30)
        self.step(60);self.assertEqual(self.m.quotas['tenant-a'],3)
        self.m.receive(forecast(seq=3,demand=1,issued=30,start=30),T+60);self.step(60)
        self.assertEqual(self.m.quotas['tenant-a'],3)
        self.m.receive(forecast(seq=4,demand=1,issued=60,start=60),T+60);self.step(60)
        self.assertEqual(self.m.quotas['tenant-a'],1)

    def test_nonstandard_timestamp_separator_is_rejected_without_mutation(self):
        before=self.m.snapshot()
        with self.assertRaises(self.Error):self.m.receive(forecast(windowStart=stamp(60).replace('T','X')),T)
        self.assertEqual(self.m.snapshot(),before)

if __name__=='__main__':unittest.main()
