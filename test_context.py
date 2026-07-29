import importlib.util
import unittest
from test_support import seeded, BASE, QUEUES
from errors import Error


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('context'), 'verified context required')
        import context
        self.c = context; self.k, self.p = seeded()

    def resolve(self): return self.c.resolve_context(self.k, self.p, 'tenant-team-a')

    def test_valid_context_is_read_only(self):
        r = self.resolve()
        self.assertEqual((r['tenantUID'], r['namespaceUID'], r['queueUID']), ('t-a', 'ns-a', 'q-a'))
        self.assertEqual({m for m, _ in self.k.calls}, {'GET'})

    def test_namespace_recreated_uid_rejected(self):
        self.k.objects['/api/v1/namespaces/tenant-team-a']['metadata']['uid'] = 'replacement'
        with self.assertRaises(Error): self.resolve()

    def test_queue_uid_changed_rejected(self):
        self.k.objects[QUEUES + '/tenant-a-q']['metadata']['uid'] = 'replacement'
        with self.assertRaises(Error): self.resolve()

    def test_stale_generation_rejected(self):
        self.k.objects[BASE + '/team-a']['metadata']['generation'] = 2
        with self.assertRaises(Error): self.resolve()

    def test_wrong_template_rejected(self):
        path = '/apis/resource.k8s.io/v1/namespaces/tenant-team-a/resourceclaimtemplates/tenant-gpu'
        self.k.objects[path]['spec']['spec']['devices']['requests'][0]['exactly']['count'] = 2
        with self.assertRaises(Error): self.resolve()

    def test_native_only_pool_is_unsupported_path(self):
        self.k.objects.pop('/apis/resource.k8s.io/v1/resourceslices/s1')
        with self.assertRaises(Error) as cm: self.resolve()
        self.assertEqual(cm.exception.code, 'UnsupportedPath')

    def test_incomplete_pool_generation_not_supported(self):
        self.k.objects['/apis/resource.k8s.io/v1/resourceslices/s1']['spec']['pool']['resourceSliceCount'] = 2
        with self.assertRaises(Error) as cm: self.resolve()
        self.assertEqual(cm.exception.code, 'Unavailable')

    def test_api_failure_does_not_become_empty_pool(self):
        self.k.fail_paths.add('/apis/resource.k8s.io/v1/resourceslices')
        with self.assertRaises(Error) as cm: self.resolve()
        self.assertEqual(cm.exception.status, 503)

if __name__ == '__main__': unittest.main()
