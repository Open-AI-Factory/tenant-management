import importlib.util
import unittest
from test_server import BODY, POOL
from server import validate


class PolicyTests(unittest.TestCase):
    def test_additional_namespace_input_is_preserved(self):
        p = validate(dict(BODY, additionalNamespaceNames=['tenant-test-team-train']), [POOL])
        self.assertEqual(p['additionalNamespaceNames'], ['tenant-test-team-train'])

    def test_schema_retains_legacy_required_fields(self):
        self.assertIsNotNone(importlib.util.find_spec('policy_schema'), 'compatible schema module required')
        from policy_schema import policy_schema
        s = policy_schema()
        self.assertIn('namespaceName', s['properties']['spec']['required'])
        self.assertNotIn('additionalNamespaceNames', s['properties']['spec']['required'])
        self.assertEqual(s['properties']['spec']['properties']['additionalNamespaceNames']['maxItems'], 2)
        self.assertIn('namespaceBindings', s['properties']['status']['properties'])

if __name__ == '__main__': unittest.main()
