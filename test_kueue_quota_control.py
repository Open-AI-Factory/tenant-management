import copy
import json
import unittest
from datetime import datetime, timezone
from errors import Error
from test_kueue_support import seed_kueue, CQ, CM
try:
    from kueue_quota_control import KueueQuotaControl
except ImportError:
    KueueQuotaControl = None

ADMIN = {'platformAdmin': True, 'providerInstance': 'grafana-1', 'subjectKind': 'uid', 'subjectId': 'admin'}


def stamp(now):
    return datetime.fromtimestamp(now, timezone.utc).isoformat().replace('+00:00', 'Z')


def event(now, seq=1, demand=2, uid='t-a', delay=0):
    return {'eventId': uid + '-' + str(seq), 'source': 'dummy-profiler', 'tenantUID': uid,
            'poolKey': 'example-gpu-pool', 'unit': 'device', 'sequence': seq,
            'issuedAt': stamp(now), 'windowStart': stamp(now + delay), 'windowEnd': stamp(now + 600),
            'expectedDevices': demand, 'confidence': 0.95}


class Tests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(KueueQuotaControl, 'Actual quota controller not implemented')
        self.k, self.targets, self.policies, _ = seed_kueue()
        self.now = 1791406800.0
        self.c = self.control()

    def control(self, enabled=True):
        return KueueQuotaControl(self.k, enabled=enabled, clock=lambda: self.now)

    def row(self, uid='t-a'):
        return self.c.state()['tenants'][uid]

    def quota(self, letter='a'):
        return int(self.k.objects[CQ + '/example-tenant-queue-' + letter]['spec']['resourceGroups'][0]['flavors'][0]['resources'][0]['nominalQuota'])

    def writes(self):
        return sum(method == 'PUT' and path.startswith(CQ + '/') for method, path in self.k.calls)

    def send(self, seq=1, demand=2, uid='t-a', delay=0):
        return self.c.receive(event(self.now, seq, demand, uid, delay), ADMIN)

    def test_real_quota_one_two_one_and_b_zero_one(self):
        protected = copy.deepcopy(self.k.objects[CQ + '/example-tenant-queue-b'])
        self.send(demand=1.2); self.c.tick()
        self.assertEqual(2, self.quota()); self.assertEqual(protected, self.k.objects[CQ + '/example-tenant-queue-b'])
        self.assertEqual('QuotaVerified', self.row()['phase'])
        for seq in range(2, 5):
            self.now += 30; self.send(seq, 1); self.c.tick()
        self.assertEqual(1, self.quota())
        self.send(uid='t-b', demand=1); self.c.tick()
        self.assertEqual((1, 1), (self.quota(), self.quota('b')))
        self.assertEqual('48Gi', self.k.objects[CQ + '/example-tenant-queue-a']['spec']['resourceGroups'][0]['flavors'][0]['resources'][2]['nominalQuota'])
        self.assertFalse(self.row()['execution']['executionEnabled'])

    def test_persisted_target_must_match_forecast(self):
        self.send(demand=1)
        doc = json.loads(self.k.objects[CM+'/tenant-admin-budget-state']['data']['document'])
        doc['tenants']['t-a']['target'] = 2
        self.k.objects[CM+'/tenant-admin-budget-state']['data']['document'] = json.dumps(doc)
        self.c.tick()
        self.assertEqual(0, self.writes())
        self.assertEqual('InvalidStoredForecast', self.row()['reason'])

    def test_policy_change_after_intent_save_stops_write(self):
        self.send()
        def change(path, method, result):
            if path == CM+'/tenant-admin-budget-state' and 'intent' in result['data']['document']:
                self.k.after_write = None
                doc = json.loads(self.k.objects[CM+'/tenant-admin-budget-policy']['data']['document'])
                doc['entries']['t-a']['revision'] = 2
                self.k.objects[CM+'/tenant-admin-budget-policy']['data']['document'] = json.dumps(doc)
        self.k.after_write = change
        self.c.tick()
        self.assertEqual(0, self.writes())
        self.assertEqual('ConcurrentChange', self.row()['reason'])

    def test_lead_boundary_and_capacity_shortage(self):
        self.send(delay=60); self.c.tick(); self.assertEqual(1, self.quota())
        self.now += 29; self.c.tick(); self.assertEqual(1, self.quota())
        self.now += 1; self.c.tick(); self.assertEqual(2, self.quota())
        self.send(uid='t-b', demand=1); self.c.tick()
        self.assertEqual(0, self.quota('b')); self.assertEqual('Capacity', self.row('t-b')['reason'])

    def test_repeated_low_sample_cannot_fill_down_hold(self):
        self.send(demand=0); self.c.tick()
        self.now += 60; self.c.tick()
        self.assertEqual(1, self.quota()); self.assertNotEqual('QuotaVerified', self.row()['phase'])

    def test_down_hold_resets_after_gap_and_cooldown_applies(self):
        self.send(); self.c.tick()
        self.now += 1; self.send(2, 1); self.c.tick()
        self.assertEqual('Cooldown', self.row()['reason'])
        self.now += 35; self.send(3, 1); self.c.tick()
        self.now += 30; self.send(4, 1); self.c.tick()
        self.assertEqual(2, self.quota())
        self.now += 30; self.send(5, 1); self.c.tick(); self.assertEqual(1, self.quota())

    def test_duplicate_survives_restart_and_conflicting_id_rejected(self):
        e = event(self.now)
        self.c.receive(e, ADMIN); self.c.tick(); count = self.writes()
        self.c = self.control()
        self.assertEqual('Duplicate', self.c.receive(e, ADMIN)['status'])
        self.c.tick(); self.assertEqual(count, self.writes())
        e['expectedDevices'] = 1
        with self.assertRaises(Error): self.c.receive(e, ADMIN)

    def test_stale_expired_and_out_of_order_inputs_cannot_write(self):
        self.send(2, 2, delay=60)
        with self.assertRaises(Error): self.send(1)
        self.now += 121; self.c.tick(); self.assertEqual(1, self.quota())
        with self.assertRaises(Error): self.c.receive(event(self.now - 121, 3), ADMIN)
        e = event(self.now, 3); e['windowEnd'] = stamp(self.now - 1)
        with self.assertRaises(Error): self.c.receive(e, ADMIN)

    def test_invalid_inputs_and_admin_are_rejected(self):
        for key, value in [('tenantUID', 'other'), ('source', 'other'), ('unit', 'GiB'),
                            ('expectedDevices', float('nan')), ('expectedDevices', True),
                            ('sequence', True), ('confidence', 0.1), ('issuedAt', 'bad'), ('eventId', '\ud800')]:
            e = event(self.now); e[key] = value
            with self.subTest(key=key), self.assertRaises(Error): self.c.receive(e, ADMIN)
        with self.assertRaises(Error): self.c.receive(event(self.now), {'platformAdmin': False})
        self.assertEqual(0, self.writes())

    def test_store_failure_never_acknowledges(self):
        self.k.fail_paths.add(CM + '/tenant-admin-budget-state')
        with self.assertRaises(Error): self.send()
        self.assertEqual(0, self.writes())

    def test_unknown_b_blocks_growth(self):
        self.send()
        self.k.fail_paths.add(CQ + '/example-tenant-queue-b')
        self.c.tick(); self.assertEqual(1, self.quota())
        self.assertNotEqual('QuotaVerified', self.row()['phase'])

    def test_lost_response_restart_reads_before_writing(self):
        self.send()
        def lost(path, method, obj):
            if path.startswith(CQ + '/'):
                self.k.after_write = None
                raise Error('Response lost', 503)
        self.k.after_write = lost
        self.c.tick(); self.assertEqual('ApplyUnknown', self.row()['phase'])
        count = self.writes(); self.c = self.control(); self.c.tick()
        self.assertEqual(count, self.writes()); self.assertEqual('QuotaVerified', self.row()['phase'])

    def test_apply_history_save_failure_is_not_success(self):
        self.send()
        def fail_store(path, method, obj):
            if path.startswith(CQ + '/'):
                self.k.fail_paths.add(CM + '/tenant-admin-budget-state'); self.k.after_write = None
        self.k.after_write = fail_store
        result = self.c.tick()
        self.assertNotEqual('QuotaVerified', result['tenants']['t-a']['phase'])
        self.k.fail_paths.clear(); self.c = self.control(); count = self.writes(); self.c.tick()
        self.assertEqual(count, self.writes()); self.assertEqual('QuotaVerified', self.row()['phase'])

    def test_unknown_apply_blocks_other_tenant_growth(self):
        self.send(); self.send(uid='t-b', demand=1)
        def lost(path, method, obj):
            if path.startswith(CQ + '/'):
                self.k.fail_paths.add(path); self.k.after_write = None
                raise Error('Unknown write', 503)
        self.k.after_write = lost
        self.c.tick(); count = self.writes(); self.c.tick()
        self.assertEqual(0, self.quota('b')); self.assertEqual(count, self.writes())

    def test_external_edit_and_rv_conflict_are_not_overwritten(self):
        self.send()
        self.k.objects[CQ + '/example-tenant-queue-a']['spec']['resourceGroups'][0]['flavors'][0]['resources'][1]['nominalQuota'] = '17'
        self.c.tick(); self.assertEqual(0, self.writes()); self.assertEqual('Conflict', self.row()['phase'])

    def test_policy_revision_change_defers_old_forecast(self):
        self.send(delay=60)
        cm = self.k.objects[CM + '/tenant-admin-budget-policy']
        doc = json.loads(cm['data']['document']); doc['entries']['t-a']['revision'] = 2
        cm['data']['document'] = json.dumps(doc); cm['metadata']['resourceVersion'] = '99'
        self.now += 30; self.c.tick()
        self.assertEqual(1, self.quota()); self.assertEqual('PolicyChanged', self.row()['reason'])
        self.send(2); self.c.tick(); self.assertEqual(2, self.quota())

    def test_disabled_missing_policy_and_state_queries_never_write(self):
        self.c = self.control(False)
        with self.assertRaises(Error): self.send()
        self.c.tick(); self.c.state(); self.assertEqual(0, self.writes())
        del self.k.objects[CM + '/tenant-admin-budget-policy']
        self.c = self.control(); self.c.tick(); self.assertEqual(0, self.writes())

    def test_history_ledger_limit_and_timestamp_default(self):
        for seq in range(1, 106): self.send(seq, 1)
        row = self.c.state()['tenants']['t-a']
        self.assertEqual(100, len(row['events'])); self.assertEqual(105, len(row['ledger']))
        import time
        real = KueueQuotaControl(self.k, enabled=False).state()
        parsed = datetime.fromisoformat(real['serverTime'].replace('Z', '+00:00')).timestamp()
        self.assertLess(abs(parsed - time.time()), 3)

    def test_dummy_profiler_generates_current_utc_contract(self):
        try:
            from dummy_kueue_profiler import make_event
        except ImportError:
            self.fail('Actual dummy profiler CLI is not implemented')
        import time
        e = make_event('t-a', 7, 1.2)
        self.assertEqual(('t-a', 7, 'example-gpu-pool', 'device'),
                         tuple(e[k] for k in ('tenantUID', 'sequence', 'poolKey', 'unit')))
        at = datetime.fromisoformat(e['issuedAt'].replace('Z', '+00:00')).timestamp()
        self.assertLess(abs(time.time() - at), 3)
        self.assertEqual(30, datetime.fromisoformat(e['windowStart'].replace('Z', '+00:00')).timestamp() - at)
