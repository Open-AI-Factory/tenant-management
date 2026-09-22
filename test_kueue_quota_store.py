import copy
import json
import unittest
from errors import Error
from test_kueue_support import seed_kueue, CM
try:
    from kueue_quota_store import QuotaStore
except ImportError:
    QuotaStore = None


class Tests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(QuotaStore, 'Durable budget store is not implemented')
        self.k, self.targets, self.policies, _ = seed_kueue()
        self.store = QuotaStore(self.k)

    def test_forecast_intent_survive_reload(self):
        s = self.store.load_state()
        s['tenants']['t-a'] = {'forecast': {'policyRevision': 1, 'event': {'eventId': 'f1'}},
                                'intent': {'policyRevision': 1, 'target': 2}, 'history': []}
        saved = self.store.save_state(s)
        restored = QuotaStore(self.k).load_state()
        self.assertEqual(1, restored['tenants']['t-a']['intent']['policyRevision'])
        self.assertEqual('f1', restored['tenants']['t-a']['forecast']['event']['eventId'])
        self.assertNotEqual(s['_resourceVersion'], saved['_resourceVersion'])
        self.assertNotIn('_resourceVersion', json.loads(self.k.objects[CM + '/tenant-admin-budget-state']['data']['document']))

    def test_state_requires_resource_version_and_uid(self):
        s = self.store.load_state(); del s['_resourceVersion']
        with self.assertRaises((Error, ValueError)): self.store.save_state(s)
        s = self.store.load_state(); s['_configMapUID'] = 'other'
        with self.assertRaises((Error, ValueError)): self.store.save_state(s)

    def test_conflict_does_not_overwrite(self):
        stale = self.store.load_state()
        current = self.store.load_state(); current['tenants']['t-b'] = {'history': [{'id': 'b'}]}
        self.store.save_state(current)
        stale['tenants']['t-a'] = {}
        with self.assertRaises(Error): self.store.save_state(stale)
        self.assertIn('t-b', self.store.load_state()['tenants'])

    def test_state_size_and_schema_rejected(self):
        for state in [{'schemaVersion': 2}, {'schemaVersion': 1, 'tenants': []}]:
            with self.assertRaises((Error, ValueError)): self.store.save_state(state)
        state = self.store.load_state(); state['large'] = 'x' * (512 * 1024)
        with self.assertRaises((Error, ValueError)): self.store.save_state(state)

    def test_ledger_full_rejects_without_evicting_valid_ids(self):
        s = self.store.load_state()
        s['tenants']['t-a'] = {'ledger': {str(i): {'fingerprint': 'x'} for i in range(1000)}}
        self.store.save_state(s)
        s = self.store.load_state(); s['tenants']['t-a']['ledger']['overflow'] = {}
        with self.assertRaises((Error, ValueError)): self.store.save_state(s)
        self.assertEqual(1000, len(self.store.load_state()['tenants']['t-a']['ledger']))

    def test_history_bound_and_nonfinite_or_unicode_rejected(self):
        for row in [{'history': [{}] * 101}, {'history': [{'n': float('inf')}]},
                    {'history': [{'s': '\ud800'}]}]:
            s = self.store.load_state(); s['tenants']['t-a'] = row
            with self.assertRaises((Error, ValueError)): self.store.save_state(s)

    def test_duplicate_keys_and_deep_stored_json_rejected(self):
        obj = self.k.objects[CM + '/tenant-admin-budget-state']
        for raw in ['{"schemaVersion":1,"schemaVersion":1,"tenants":{}}',
                    '{"schemaVersion":1,"tenants":{},"nested":' + '[' * 40 + '0' + ']' * 40 + '}']:
            obj['data']['document'] = raw
            with self.assertRaises((Error, ValueError)): self.store.load_state()

    def test_no_targets_or_policy_write(self):
        self.assertEqual(2, len(self.store.load_targets()['entries']))
        self.assertEqual(2, len(self.store.load_policy()['entries']))
        self.store.save_state(self.store.load_state())
        self.assertFalse(any(method != 'GET' and not path.endswith('/tenant-admin-budget-state')
                             for method, path in self.k.calls))
