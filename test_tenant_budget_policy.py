import copy
import unittest
from test_kueue_support import seed_kueue
try:
    from tenant_budget_policy import validate_policy
except ImportError:
    validate_policy = None


class Tests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(validate_policy, 'Capsule budget policy is not implemented')
        self.k, self.targets, self.policies, _ = seed_kueue()
        self.p = self.policies['entries']['t-a']

    def test_valid_policy_pins_capsule_identity_and_revision(self):
        result = validate_policy(self.p, self.targets)
        self.assertEqual(('t-a', 1, 0, 2), tuple(result[key] for key in
                         ('tenantUID', 'revision', 'minDevices', 'maxDevices')))
        revised = copy.deepcopy(self.p); revised['revision'] = 2
        self.assertNotEqual(result['digest'], validate_policy(revised, self.targets)['digest'])

    def test_invalid_quantity_time_confidence_and_identity_are_rejected(self):
        for key, value in [('minDevices', True), ('maxDevices', 3), ('revision', 0),
                           ('leadSeconds', -1), ('minConfidence', float('nan')),
                           ('poolKey', 'mig-test'), ('tenantUID', 'other'), ('unit', 'GiB')]:
            with self.subTest(key=key):
                p = copy.deepcopy(self.p); p[key] = value
                with self.assertRaises(ValueError): validate_policy(p, self.targets)

    def test_target_cannot_share_namespace_or_queue(self):
        self.targets['entries']['t-b']['clusterQueue'] = copy.deepcopy(self.p['clusterQueue'])
        with self.assertRaises(ValueError): validate_policy(self.p, self.targets)

    def test_missing_allowlist_cannot_authorize_policy(self):
        del self.targets['entries']['t-a']
        with self.assertRaises(ValueError): validate_policy(self.p, self.targets)
