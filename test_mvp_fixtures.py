import importlib.util
import unittest
from test_support import seeded
from errors import Error


class MvpTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('verify_tenant_mvp'))
        from verify_tenant_mvp import verify_mvp
        self.verify=verify_mvp; self.k,self.p=seeded()
        self.config={'namespaceName':'tenant-team-a','namespaceUID':'ns-a','tenantUID':'t-a',
            'allowedNamespaces':['tenant-team-a'],'baselineComplete':True}

    def test_wrong_scope_and_uid_and_incomplete_baseline_never_mutate(self):
        for changes in ({'namespaceName':'kube-system'},{'namespaceUID':'foreign'},{'baselineComplete':False},{'mutate':True}):
            with self.assertRaises(Error): self.verify(self.k,'quota',dict(self.config,**changes))
        self.assertTrue(all(method=='GET' for method,path in self.k.calls))

    def test_observations_never_claim_gpu_test_passed(self):
        result=self.verify(self.k,'quota',self.config)
        self.assertEqual(result['verdict'],'NotRun')
        self.assertEqual(result['mutations'],0)
        self.assertIn('claims',result['observations'])

    def test_api_failure_not_empty_or_verified(self):
        self.k.fail_paths.add('/apis/resource.k8s.io/v1/namespaces/tenant-team-a/resourceclaims')
        with self.assertRaises(Error) as cm: self.verify(self.k,'cancel',self.config)
        self.assertEqual(cm.exception.status,503)
