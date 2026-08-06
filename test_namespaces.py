import copy
import importlib.util
import unittest
from test_support import seeded, TENANTS, OWNER, QUEUES
from errors import Error


class NamespaceTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('namespaces'), 'multi-namespace management required')
        import namespaces
        self.n = namespaces; self.k, self.p = seeded()

    def test_legacy_policy_has_one_namespace(self):
        self.assertEqual(self.n.policy_namespaces(self.p), ('tenant-team-a',))

    def test_three_namespaces_one_queue(self):
        self.p['spec']['additionalNamespaceNames'] = ['tenant-team-a-train', 'tenant-team-a-infer']
        tenant = self.k.objects[TENANTS + '/team-a']
        result = self.n.reconcile_namespaces(self.k, self.p, tenant)
        self.assertEqual(set(result), {'tenant-team-a', 'tenant-team-a-train', 'tenant-team-a-infer'})
        self.assertTrue(all(b['phase'] == 'Ready' for b in result.values()))
        self.assertEqual(len([p for p in self.k.objects if p.startswith(QUEUES)]), 1)

    def test_fourth_or_duplicate_namespace_rejected(self):
        for extra in [['tenant-team-a-x'] * 2, ['tenant-team-a'],
                      ['tenant-team-a-x', 'tenant-team-a-y', 'tenant-team-a-z'], ['kube-system']]:
            self.p['spec']['additionalNamespaceNames'] = extra
            with self.subTest(extra=extra), self.assertRaises(Error): self.n.policy_namespaces(self.p)

    def test_foreign_namespace_not_adopted(self):
        self.k.objects['/api/v1/namespaces/tenant-team-a']['metadata']['labels'][OWNER] = 'foreign'
        with self.assertRaises(Error): self.n.reconcile_namespaces(self.k, self.p, self.k.objects[TENANTS + '/team-a'])

    def test_pending_intent_blocks_policy_edit(self):
        with self.assertRaises(Error): self.n.assert_policy_editable(self.k, self.p, [{'phase': 'Prepared'}])

    def test_claim_in_second_namespace_blocks_edit(self):
        self.p['spec']['additionalNamespaceNames'] = ['tenant-team-a-train']
        self.k.objects['/apis/resource.k8s.io/v1/namespaces/tenant-team-a-train/resourceclaims/c1'] = {'metadata': {'name': 'c1'}}
        with self.assertRaises(Error): self.n.assert_policy_editable(self.k, self.p, [])

if __name__ == '__main__': unittest.main()
