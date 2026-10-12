import importlib.util
import json
import unittest


class BaselineTests(unittest.TestCase):
    def collector(self):
        self.assertIsNotNone(importlib.util.find_spec('collect_tenant_baseline'),
                             'read-only baseline collector is required')
        from collect_tenant_baseline import collect_baseline
        return collect_baseline

    def test_read_only_and_redacted(self):
        calls = []
        class API:
            def call(self, path, method='GET', **kwargs):
                calls.append((method, path))
                if path == '/version':
                    return {'gitVersion': 'v1.34.0'}
                return {'items': [], 'metadata': {}}
        result = self.collector()(API(), lambda: {'id': 7, 'uid': 'u7',
            'login': 'private-login', 'password': 'do-not-output',
            'token': 'do-not-output', 'isGrafanaAdmin': True})
        self.assertEqual(result['versions']['kubernetes'], 'v1.34.0')
        self.assertTrue(calls)
        self.assertEqual({m for m, _ in calls}, {'GET'})
        self.assertNotIn('do-not-output', json.dumps(result))
        self.assertNotIn('private-login', json.dumps(result))
        self.assertEqual(result['authIdentityShape']['uid'], 'str')
        self.assertEqual(result['errors'], [])

    def test_incomplete_query_is_not_empty_success(self):
        class API:
            def call(self, path, method='GET', **kwargs):
                raise OSError('unavailable')
        result = self.collector()(API(), lambda: {})
        self.assertTrue(result['errors'])
        self.assertIsNone(result['tenants'])

    def test_paginated_resources_are_not_truncated(self):
        class API:
            def call(self, path, method='GET', **kwargs):
                if path == '/version': return {'gitVersion': 'test'}
                if '/tenants' in path:
                    return {'items': [{'metadata': {'name': 'second' if 'continue=' in path else 'first'}}],
                            'metadata': {} if 'continue=' in path else {'continue': 'opaque/token'}}
                return {'items': [], 'metadata': {}}
        result = self.collector()(API(), lambda: {})
        self.assertEqual([x['metadata']['name'] for x in result['tenants']], ['first', 'second'])


if __name__ == '__main__': unittest.main()
