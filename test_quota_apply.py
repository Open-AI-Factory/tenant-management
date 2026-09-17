import copy
import hashlib
import importlib.util
import unittest
from tenant_api import TenantAPI
from test_support import seeded, BASE, TENANTS, QUEUES
from server import pool_inventory, sync_one


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('quota_apply'), 'guarded API adapter required')
        from quota_apply import apply_quota
        self.apply=apply_quota
        self.k,p=seeded()
        devices=self.k.objects['/apis/resource.k8s.io/v1/resourceslices/s1']['spec']['devices']
        devices.append(dict(copy.deepcopy(devices[0]),name='mig2'))
        self.p=self.k.objects[BASE+'/team-a'];self.p['spec']['pool']=pool_inventory(self.k)[0]
        name='tenant-team-a-'+hashlib.sha256(b'p-a').hexdigest()[:10]
        q=self.k.objects.pop(QUEUES+'/tenant-a-q');q['metadata']['name']=name
        self.k.objects[QUEUES+'/'+name]=q;self.p['status']['queue']=name
        self.api=TenantAPI(self.k,b'test-key')
        self.admin=dict(providerInstance='g1',subjectKind='uid',subjectId='admin',login='Admin',platformAdmin=True)
        self.expected=dict(tenantUID='t-a',policyUID='p-a',queueUID='q-a',resourceVersion='1',gpu=1)
    def run_apply(self,**kwargs):
        return self.apply(self.api,kwargs.get('principal',self.admin),'team-a',2,kwargs.get('expected',self.expected))
    def no_writes(self):self.assertFalse([x for x in self.k.calls if x[0]!='GET'])
    def test_policy_update_then_separate_reconcile_preserves_queue_identity(self):
        old=copy.deepcopy(self.p['spec']);r=self.run_apply()
        self.assertEqual(r['status'],'PolicyUpdated')
        p=self.k.objects[BASE+'/team-a'];self.assertEqual(p['spec']['resources']['gpu'],{'guaranteed':2,'maximum':2})
        for key in ('cpu','memory'):self.assertEqual(p['spec']['resources'][key],old['resources'][key])
        qpath=QUEUES+'/'+p['status']['queue']
        self.assertEqual(self.k.objects[qpath]['spec']['resources']['gpu']['quota'],1)
        sync_one(self.k,copy.deepcopy(p))
        self.assertEqual(self.k.objects[qpath]['spec']['resources']['gpu']['quota'],2)
        self.assertEqual(self.k.objects[qpath]['metadata']['uid'],'q-a')
    def test_gate_and_role_are_not_bypassed(self):
        self.api.mutations_enabled=False
        self.assertEqual(self.run_apply()['httpStatus'],503);self.no_writes()
        self.api.mutations_enabled=True
        self.assertEqual(self.run_apply(principal=dict(self.admin,platformAdmin=False))['httpStatus'],403);self.no_writes()
    def test_changed_identity_version_or_quota_never_writes(self):
        for key,value in [('tenantUID','new'),('policyUID','new'),('queueUID','new'),('resourceVersion','old'),('gpu',2)]:
            with self.subTest(key=key):
                self.assertEqual(self.run_apply(expected=dict(self.expected,**{key:value}))['status'],'Deferred');self.no_writes()
    def test_busy_and_unavailable_inventory_are_deferred(self):
        path='/api/v1/namespaces/tenant-team-a/pods/p1'
        self.k.objects[path]={'metadata':{'name':'p1'}}
        self.assertEqual(self.run_apply()['status'],'Deferred');self.no_writes()
        del self.k.objects[path]
        self.k.fail_paths.add('/apis/resource.k8s.io/v1/resourceslices')
        self.assertEqual(self.run_apply()['status'],'Deferred');self.no_writes()
    def test_recreated_queue_and_repeat_are_not_adopted(self):
        q=self.k.objects[QUEUES+'/'+self.p['status']['queue']]
        q['metadata']['uid']='new'
        self.assertEqual(self.run_apply()['status'],'Deferred');self.no_writes()
        q['metadata']['uid']='q-a'
        self.run_apply();n=len([x for x in self.k.calls if x[0]=='PUT'])
        self.assertEqual(self.run_apply()['status'],'Deferred')
        self.assertEqual(len([x for x in self.k.calls if x[0]=='PUT']),n)
    def test_malformed_preconditions_and_quota_rejected(self):
        for expected in ({},dict(self.expected,gpu=True),dict(self.expected,extra=1)):
            self.assertEqual(self.run_apply(expected=expected)['status'],'Rejected');self.no_writes()
        for value in (True,0,1.2):
            self.assertEqual(self.apply(self.api,self.admin,'team-a',value,self.expected)['status'],'Rejected');self.no_writes()

if __name__=='__main__':unittest.main()
