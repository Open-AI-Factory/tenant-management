"""Adversarial QA: public protocol limits and independent quota invariants."""
import copy
import random
import unittest
from concurrent.futures import ThreadPoolExecutor
from forecast_quota import QuotaModel, ForecastError, timestamp
from quota_simulator import make_server
from test_forecast_quota import config, forecast, T
import test_quota_simulator as http_fixture


class PolicyQaTests(unittest.TestCase):
    def test_explicit_empty_config_is_not_silently_replaced_with_demo(self):
        candidate=None
        try:
            with self.assertRaises(ForecastError):candidate=make_server(0,{})
        finally:
            if candidate:candidate.server_close()

    def test_random_demand_walk_preserves_capacity_bounds_and_cooldown(self):
        rng=random.Random(1006);model=QuotaModel(config());last_applied={}
        for i in range(450):
            at=i*3;prior=copy.deepcopy(model.quotas)
            observations={}
            for uid in ('tenant-a','tenant-b'):
                e=forecast(uid=uid,seq=i+1,demand=rng.choice([0,1,1.2,2,3,7]),issued=at,start=at+rng.choice([0,30,60]),end=at+180,
                           confidence=rng.choice([.7,.95,.95,.95]))
                model.receive(e,T+at)
                if i%37==0:
                    snapshot=model.snapshot();self.assertEqual(model.receive(e,T+at)['status'],'Duplicate');self.assertEqual(model.snapshot(),snapshot)
                observations[uid]={'idle':rng.choice([True,True,False,None]),'observedAt':T+at-rng.choice([0,0,31])}
            rows=model.step(T+at,observations)
            for row in rows:
                uid=row['tenantUID'];n=row['appliedQuota']
                self.assertGreaterEqual(n,1);self.assertLessEqual(n,3)
                if row['status']=='SimulatedApplied':
                    self.assertIs(observations[uid]['idle'],True)
                    self.assertLessEqual(T+at-observations[uid]['observedAt'],30)
                    if uid in last_applied:self.assertGreaterEqual(T+at-last_applied[uid],30)
                    last_applied[uid]=T+at
                    if n>row['fromQuota']:self.assertGreaterEqual(T+at,row['eligibleAt'])
                prior[uid]=n;self.assertLessEqual(sum(prior.values()),4)
            self.assertLessEqual(len(model.history),400)
        self.assertEqual(model.snapshot()['receivedEvents'],900)

    def test_full_inbox_rejects_new_event_but_allows_exact_retry(self):
        model=QuotaModel(config());first=forecast()
        for i in range(1,1001):model.receive(forecast(seq=i),T)
        before=model.snapshot()
        with self.assertRaises(ForecastError) as raised:model.receive(forecast(seq=1001),T)
        self.assertEqual(raised.exception.status,429);self.assertEqual(model.snapshot(),before)
        self.assertEqual(model.receive(first,T)['status'],'Duplicate');self.assertEqual(model.snapshot(),before)

    def test_supported_timezone_and_fraction_inputs_have_exact_epoch(self):
        self.assertEqual(timestamp('2026-10-06T09:00:00+09:00'),T)
        self.assertEqual(timestamp('2026-10-06T00:00:00.500Z'),T+.5)
        for bad in ('2026-10-06x00:00:00Z','2026-10-06T00:00:00+00:00:01','2026-10-06T00:00:00+0000',
                    '2026-10-06T00:01:00+00:99','2026-10-06T00:01:00-01:75'):
            with self.assertRaises(ForecastError):timestamp(bad)


class ConcurrentHttpQaTests(unittest.TestCase):
    setUp=http_fixture.SimulatorTests.setUp
    tearDown=http_fixture.SimulatorTests.tearDown
    request=http_fixture.SimulatorTests.request
    def test_concurrent_retry_stores_one_forecast(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            replies=list(executor.map(lambda _:self.request('/api/forecasts',forecast()),range(24)))
        self.assertTrue(all(code==200 for code,_ in replies))
        statuses=[body['status'] for _,body in replies]
        self.assertEqual(statuses.count('Accepted'),1);self.assertEqual(statuses.count('Duplicate'),23)
        state=self.request('/api/state')[1];self.assertEqual(state['receivedEvents'],1)
        self.assertEqual(state['quotas'],{'tenant-a':1,'tenant-b':1})

if __name__=='__main__':unittest.main()
