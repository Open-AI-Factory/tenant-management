import unittest
from admission import guards
from test_support import seeded
from workloads import binding


class AdmissionTests(unittest.TestCase):
    def test_global_queue_scope_guard_is_parametrized_and_fail_closed(self):
        policies = {p['metadata']['name']: p for p in guards()}
        self.assertIn('tenant-queue-scope', policies)
        p = policies['tenant-queue-scope']['spec']
        self.assertEqual(p['failurePolicy'], 'Fail')
        self.assertEqual(p['paramKind']['kind'], 'TenantResourcePolicy')
        self.assertIn('UPDATE', p['matchConstraints']['resourceRules'][0]['operations'])

    def test_scope_binding_covers_foreign_namespaces_without_matching_unrelated_queues(self):
        import admission
        self.assertTrue(hasattr(admission, 'queue_scope_binding'), 'global protected-queue binding required')
        _, policy = seeded()
        b = admission.queue_scope_binding(policy, 'tenant-a-q')
        match = b['spec']['matchResources']
        self.assertNotIn('namespaceSelector', match)
        self.assertEqual(match['objectSelector']['matchLabels'], {'kai.scheduler/queue': 'tenant-a-q'})
        self.assertEqual(b['spec']['paramRef']['parameterNotFoundAction'], 'Deny')

    def test_bound_policy_keeps_missing_parameters_closed(self):
        _, p = seeded()
        self.assertEqual(binding(p)['spec']['paramRef']['parameterNotFoundAction'], 'Deny')

if __name__ == '__main__': unittest.main()
