import copy
import importlib.util
import json
import unittest
from errors import Error
from test_support import seeded, TENANTS, BASE
from executions import ACCESS, CMS, RECORD_LABEL, compact


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('tenant_api'), 'tenant API required')
        from tenant_api import TenantAPI
        self.k, self.p = seeded(); self.api = TenantAPI(self.k, b'test-cursor-key')
        self.user = dict(providerInstance='g1', subjectKind='uid', subjectId='alice', login='A', platformAdmin=False)
        self.admin = dict(self.user, platformAdmin=True)
        self.bind('member')
        self.k.objects[TENANTS+'/platform-services'] = {'metadata': {'name':'platform-services','uid':'t-service'}}
        self.ns = 'tenant-team-a'
        self.body = dict(namespaceName=self.ns, requestId='run-1', durationSeconds=30)

    def bind(self, role):
        self.k.objects[ACCESS] = {'metadata':{'name':'tenant-access-v1','resourceVersion':'1'},'data':{'bindings.json':json.dumps([dict(providerInstance='g1',subjectKind='uid',subjectId='alice',tenantUID='t-a',role=role)])}}

    def call(self, method, path, body=None, actor=None):
        return self.api.dispatch(actor or self.user, method, path, body)

    def test_registry_capsule_first_and_scoped(self):
        _, state = self.call('GET','/api/state')
        self.assertEqual([t['name'] for t in state['tenants']], ['team-a'])
        self.assertEqual(state['pools'], [])
        _, state = self.call('GET','/api/state', actor=self.admin)
        missing = next(t for t in state['tenants'] if t['name']=='platform-services')
        self.assertEqual(missing['policyState'],'PolicyMissing')
        self.assertFalse(missing['capabilities']['submit'])

    def test_viewer_cannot_submit_and_no_pod_created(self):
        self.bind('viewer')
        with self.assertRaises(Error) as cm: self.call('POST','/api/tenants/team-a/jobs',self.body)
        self.assertEqual(cm.exception.status,403)
        self.assertFalse(any('/pods/' in p for p in self.k.objects))

    def test_foreign_tenant_direct_read_is_404(self):
        for suffix in ('jobs?namespaceName=tenant-team-a','jobs/run-1?namespaceName=tenant-team-a','jobs/run-1/logs?namespaceName=tenant-team-a'):
            with self.assertRaises(Error) as cm: self.call('GET','/api/tenants/platform-services/'+suffix)
            self.assertEqual(cm.exception.status,404)

    def test_submission_retry_and_role_revocation(self):
        a,r = self.call('POST','/api/tenants/team-a/jobs',self.body)
        b,s = self.call('POST','/api/tenants/team-a/jobs',self.body)
        self.assertEqual((a,b),(201,200)); self.assertEqual(r['pod'],s['pod'])
        self.bind('viewer')
        with self.assertRaises(Error) as cm: self.call('POST','/api/tenants/team-a/jobs/run-1/cancel',dict(namespaceName=self.ns,uid=r['pod']['uid']))
        self.assertEqual(cm.exception.status,403)

    def test_bindings_compare_and_swap_admin_only(self):
        with self.assertRaises(Error): self.call('GET','/api/tenants/team-a/members')
        _, old = self.call('GET','/api/tenants/team-a/members',actor=self.admin)
        with self.assertRaises(Error) as cm: self.call('PUT','/api/tenants/team-a/members',dict(resourceVersion='wrong',bindings=[]),actor=self.admin)
        self.assertEqual(cm.exception.status,409)
        self.call('PUT','/api/tenants/team-a/members',dict(resourceVersion=old['resourceVersion'],bindings=[]),actor=self.admin)
        _, s = self.call('GET','/api/state'); self.assertEqual(s['tenants'],[])

    def test_add_namespace_uses_same_policy_queue_and_rejects_collision(self):
        self.bind('tenant-admin')
        _, p = self.call('POST','/api/tenants/team-a/namespaces',dict(purpose='dev',resourceVersion='1'))
        self.assertEqual(p['spec']['additionalNamespaceNames'],['tenant-team-a-dev'])
        self.assertEqual(p['status']['queue'],'tenant-a-q')
        self.assertEqual(p['spec']['resources'],self.p['spec']['resources'])
        with self.assertRaises(Error): self.call('POST','/api/tenants/team-a/namespaces',dict(purpose='../../x',resourceVersion=p['resourceVersion']))

    def test_jobs_paginate_with_scope_bound_cursor(self):
        _, record = self.call('POST','/api/tenants/team-a/jobs',self.body)
        for i in range(55):
            r=copy.deepcopy(record); r['recordName']='tenant-run-extra-'+str(i).zfill(3); r['requestKey']='extra-'+str(i)
            self.k.objects[CMS+'/'+r['recordName']]={'metadata':{'name':r['recordName'],'labels':{RECORD_LABEL:'v1'}},'data':{'record.json':compact(r)}}
        _, page = self.call('GET','/api/tenants/team-a/jobs?namespaceName='+self.ns)
        self.assertEqual(len(page['jobs']),50); self.assertTrue(page['nextCursor'])
        _, rest = self.call('GET','/api/tenants/team-a/jobs?namespaceName='+self.ns+'&cursor='+page['nextCursor'])
        self.assertEqual(len(rest['jobs']),6)
        with self.assertRaises(Error): self.call('GET','/api/tenants/team-a/jobs?namespaceName='+self.ns+'&cursor='+page['nextCursor']+'x')

    def test_outage_not_empty_state(self):
        self.k.fail_paths.add(TENANTS)
        with self.assertRaises(Error) as cm: self.call('GET','/api/state')
        self.assertEqual(cm.exception.status,503)

    def test_staged_deployment_exposes_read_only_capabilities(self):
        self.call('POST', '/api/tenants/team-a/jobs', self.body)
        self.api.mutations_enabled = False
        _, state = self.call('GET', '/api/state', actor=self.admin)
        self.assertFalse(state.get('mutationsEnabled', True))
        tenant = next(row for row in state['tenants'] if row['name'] == 'team-a')
        self.assertFalse(any(tenant['capabilities'].values()))
        self.assertEqual(tenant['usage']['status'], 'Observed')
        _, page = self.call('GET', '/api/tenants/team-a/jobs?namespaceName='+self.ns)
        self.assertFalse(page['jobs'][0]['canCancel'])

    def test_read_and_cancel_available_when_policy_disabled(self):
        _, r=self.call('POST','/api/tenants/team-a/jobs',self.body)
        self.k.objects[BASE+'/team-a']['spec']['mode']='configuration-test'
        _, page=self.call('GET','/api/tenants/team-a/jobs?namespaceName='+self.ns)
        self.assertEqual(len(page['jobs']),1)
        _, cancelled=self.call('POST','/api/tenants/team-a/jobs/run-1/cancel',dict(namespaceName=self.ns,uid=r['pod']['uid']))
        self.assertTrue(cancelled['cancelRequested'])

    def test_reconcile_error_preserves_uid_history(self):
        from server import sync_one
        self.k.fail_paths.add(TENANTS+'/team-a')
        sync_one(self.k,self.p)
        status=self.k.objects[BASE+'/team-a']['status']
        self.assertEqual(status['phase'],'Error')
        self.assertEqual(status.get('tenantUID'),'t-a')
        self.assertEqual(status.get('namespaceBindings'),self.p['status']['namespaceBindings'])
