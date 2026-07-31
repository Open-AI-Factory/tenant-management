import importlib.util
import unittest


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('tenant_inventory'), 'Capsule registry required')
        import tenant_inventory
        self.i = tenant_inventory
        self.snapshot = {'tenants': [{'metadata': {'name': 'a', 'uid': 'ta'}},
                                     {'metadata': {'name': 'platform', 'uid': 'tp'}}],
                         'policies': [], 'namespaces': []}

    def test_capsule_only_tenant_visible_to_admin(self):
        rows = self.i.list_tenants(self.snapshot)
        self.assertEqual([r['name'] for r in rows], ['a', 'platform'])
        self.assertEqual(rows[1]['policyState'], 'PolicyMissing')
        self.assertFalse(rows[1]['capabilities']['submit'])

    def test_member_sees_only_bound_tenant(self):
        principal = dict(providerInstance='g1', subjectKind='uid', subjectId='u', platformAdmin=False)
        binds = [dict(principal, tenantUID='ta', role='viewer')]
        rows = self.i.filter_tenants(principal, self.i.list_tenants(self.snapshot), binds)
        self.assertEqual([r['uid'] for r in rows], ['ta'])

    def test_incomplete_snapshot_not_empty_success(self):
        self.snapshot['tenants'] = None
        with self.assertRaises(self.i.Error) as cm: self.i.list_tenants(self.snapshot)
        self.assertEqual(cm.exception.status, 503)

    def test_duplicate_policy_or_wrong_uid_not_ready(self):
        p = {'metadata': {'name': 'a', 'uid': 'p', 'generation': 1},
             'spec': {'tenantName': 'a'}, 'status': {'tenantUID': 'old', 'phase': 'Ready', 'observedGeneration': 1}}
        self.snapshot['policies'] = [p]
        self.assertEqual(self.i.list_tenants(self.snapshot)[0]['policyState'], 'IdentityMismatch')
        p['status']['tenantUID'] = 'ta'; self.snapshot['policies'].append(p)
        self.assertEqual(self.i.list_tenants(self.snapshot)[0]['policyState'], 'IdentityMismatch')


if __name__ == '__main__': unittest.main()
