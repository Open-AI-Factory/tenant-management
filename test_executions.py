import copy
import importlib.util
import json
import unittest
from errors import Error
from test_support import seeded
from context import resolve_context


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('executions'), 'durable execution journal required')
        import executions
        self.e = executions; self.k, self.policy = seeded()
        self.ctx = resolve_context(self.k, self.policy, 'tenant-team-a')
        self.actor = dict(providerInstance='g1', subjectKind='uid', subjectId='a', platformAdmin=False)
        self.k.objects['/api/v1/namespaces/monitoring/configmaps/tenant-access-v1'] = {
            'metadata': {'name': 'tenant-access-v1', 'uid': 'access', 'resourceVersion': '1'},
            'data': {'bindings.json': json.dumps([dict(providerInstance='g1', subjectKind='uid', subjectId='a', tenantUID='t-a', role='member')])}}
        self.body = {'durationSeconds': 30, 'namespaceName': 'tenant-team-a', 'requestId': 'run-1'}

    def submit(self, body=None): return self.e.submit_execution(self.k, self.ctx, self.actor, body or self.body)
    def pods(self): return [v for p,v in self.k.objects.items() if '/pods/' in p]

    def test_retry_returns_same_pod(self):
        a, created = self.submit(); b, again = self.submit()
        self.assertTrue(created); self.assertFalse(again)
        self.assertEqual(a['pod']['uid'], b['pod']['uid']); self.assertEqual(len(self.pods()), 1)

    def test_same_id_different_input_409(self):
        self.submit()
        with self.assertRaises(Error) as cm: self.submit(dict(self.body, durationSeconds=31))
        self.assertEqual(cm.exception.status, 409); self.assertEqual(len(self.pods()), 1)

    def test_create_response_lost_recovers_by_uid(self):
        self.k.lose_create_response = True
        try: self.submit()
        except Error as exc: self.assertEqual(exc.status, 503)
        result, _ = self.submit()
        self.assertEqual(result['pod']['uid'], self.pods()[0]['metadata']['uid'])
        self.assertEqual(len(self.pods()), 1)

    def test_restart_after_prepared_recovers(self):
        original = self.k.call
        def crash(path, method='GET', **kwargs):
            if method == 'PUT' and '/configmaps/tenant-run-' in path: raise Error('crash before create intent', 503)
            return original(path, method, **kwargs)
        self.k.call = crash
        with self.assertRaises(Error): self.submit()
        self.assertEqual(len(self.pods()), 0)
        self.k.call = original
        self.submit(); self.assertEqual(len(self.pods()), 1)

    def test_missing_previously_created_pod_not_recreated(self):
        result, _ = self.submit()
        self.k.objects.pop('/api/v1/namespaces/tenant-team-a/pods/' + result['pod']['name'])
        self.submit()
        self.assertEqual(len(self.pods()), 0)

    def test_namespace_uid_changed_not_adopted(self):
        self.submit()
        self.k.objects['/api/v1/namespaces/tenant-team-a']['metadata']['uid'] = 'new-uid'
        with self.assertRaises(Error): self.submit()
        self.assertEqual(len(self.pods()), 1)

    def test_short_hash_collision_rejected(self):
        result, _ = self.submit()
        for path, obj in self.k.objects.items():
            if '/configmaps/tenant-run-' in path:
                record = json.loads(obj['data']['record.json']); record['requestDigest'] = 'foreign'
                obj['data']['record.json'] = json.dumps(record)
        with self.assertRaises(Error) as cm: self.submit()
        self.assertEqual(cm.exception.status, 409)

    def test_cancel_peer_403(self):
        result, _ = self.submit()
        other = dict(self.actor, subjectId='b')
        access = self.k.objects['/api/v1/namespaces/monitoring/configmaps/tenant-access-v1']
        rows = json.loads(access['data']['bindings.json']); rows.append(dict(rows[0], subjectId='b'))
        access['data']['bindings.json'] = json.dumps(rows)
        with self.assertRaises(Error) as cm:
            self.e.cancel_execution(self.k, self.ctx, other, 'run-1', result['pod']['uid'])
        self.assertEqual(cm.exception.status, 403); self.assertEqual(len(self.pods()), 1)

    def test_unknown_claim_release_not_verified(self):
        result, _ = self.submit()
        self.pods()[0]['status'] = {'phase': 'Succeeded'}
        observed = self.e.observe_execution(self.k, result)
        self.assertEqual(observed['release'], 'Unknown')

    def test_record_limit_1000_allows_existing_retry_and_cancel(self):
        result, _ = self.submit()
        for n in range(999):
            self.k.objects['/api/v1/namespaces/monitoring/configmaps/tenant-run-extra'+str(n)] = {
                'metadata': {'name': 'tenant-run-extra'+str(n), 'labels': {'tenancy.gpu.platform.io/execution': 'v1'}}}
        with self.assertRaises(Error) as cm: self.submit(dict(self.body, requestId='new-run'))
        self.assertEqual(cm.exception.status, 409)
        self.submit()
        self.e.cancel_execution(self.k, self.ctx, self.actor, 'run-1', result['pod']['uid'])
        self.assertEqual(len(self.pods()), 0)

    def test_unknown_routing_and_bad_duration_rejected(self):
        for patch in [{'queue': 'other'}, {'durationSeconds': True}, {'durationSeconds': 181}, {'namespaceName': 'other'}]:
            with self.subTest(patch=patch), self.assertRaises(Error): self.submit(dict(self.body, **patch))
        self.assertEqual(len(self.pods()), 0)

if __name__ == '__main__': unittest.main()
