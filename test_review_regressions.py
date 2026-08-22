import copy
import hashlib
import json
import unittest
from test_support import seeded, BASE, QUEUES
from errors import Error
from context import resolve_context
from executions import submit_execution, records, decode, ACCESS, get_execution, cancel_execution
from runtime import observe_records
from server import sync_one


class ReviewRegressions(unittest.TestCase):
    def setUp(self):
        self.k,self.p=seeded(); self.ctx=resolve_context(self.k,self.p,'tenant-team-a')
        self.actor=dict(providerInstance='g1',subjectKind='uid',subjectId='member',platformAdmin=False)
        self.admin=dict(providerInstance='g1',subjectKind='uid',subjectId='admin',platformAdmin=True)
        self.k.objects[ACCESS]={'metadata':{'name':'tenant-access-v1','resourceVersion':'1'},'data':{'bindings.json':json.dumps([dict(providerInstance='g1',subjectKind='uid',subjectId='member',tenantUID='t-a',role='member')])}}
        self.body=dict(requestId='review-1',namespaceName='tenant-team-a',durationSeconds=30)

    def test_controller_does_not_adopt_recreated_queue(self):
        name='tenant-team-a-'+hashlib.sha256(b'p-a').hexdigest()[:10]
        q=self.k.objects.pop(QUEUES+'/tenant-a-q'); q['metadata']['name']=name;q['metadata']['uid']='replacement-q'
        self.k.objects[QUEUES+'/'+name]=q
        self.p['status']['queue']=name; self.k.objects[BASE+'/team-a']=copy.deepcopy(self.p)
        sync_one(self.k,self.p)
        status=self.k.objects[BASE+'/team-a']['status']
        self.assertEqual(status['queueUID'],'q-a')
        self.assertEqual(status['phase'],'Error')

    def lost_response(self):
        self.k.lose_create_response=True
        with self.assertRaises(Error): submit_execution(self.k,self.ctx,self.actor,self.body)
        self.k.objects[ACCESS]['data']['bindings.json']='[]'
        return next(v for path,v in self.k.objects.items() if '/pods/' in path)

    def test_worker_recovers_existing_pod_after_actor_revocation(self):
        pod=self.lost_response(); before=len([x for x in self.k.calls if x[0]=='POST' and x[1].endswith('/pods')])
        self.k.objects[BASE+'/team-a']['spec']['mode']='configuration-test'
        observe_records(self.k)
        record=decode(records(self.k)[0]); self.assertEqual(record['pod']['uid'],pod['metadata']['uid'])
        self.assertEqual(len([x for x in self.k.calls if x[0]=='POST' and x[1].endswith('/pods')]),before)
        cancelled=cancel_execution(self.k,self.ctx,self.admin,self.body['requestId'],record['pod']['uid'])
        self.assertTrue(cancelled['cancelRequested'])

    def test_worker_does_not_adopt_digest_label_with_wrong_pod_spec(self):
        pod=self.lost_response(); pod['spec']['containers'][0]['command']=['python','-c','bad()']
        observe_records(self.k)
        self.assertIsNone(decode(records(self.k)[0])['pod']['uid'])

    def test_http_replay_returns_accepted_record_with_unready_policy(self):
        from tenant_api import TenantAPI
        api=TenantAPI(self.k,b'key'); _,a=api.dispatch(self.actor,'POST','/api/tenants/team-a/jobs',self.body)
        self.k.objects[BASE+'/team-a']['spec']['mode']='configuration-test'
        status,b=api.dispatch(self.actor,'POST','/api/tenants/team-a/jobs',self.body)
        self.assertEqual(status,200);self.assertEqual(a['pod']['uid'],b['pod']['uid'])

    def test_direct_replay_returns_existing_result_after_resource_path_disappears(self):
        a,_=submit_execution(self.k,self.ctx,self.actor,self.body)
        self.k.objects.pop('/apis/resource.k8s.io/v1/resourceslices/s1')
        b,created=submit_execution(self.k,self.ctx,self.actor,self.body)
        self.assertFalse(created);self.assertEqual(a['pod']['uid'],b['pod']['uid'])
