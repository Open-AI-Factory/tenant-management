import copy,unittest
from workloads import pod_manifest,template_spec,submit,WorkloadError
P={'metadata':{'name':'test-team','uid':'policy-uid'},'spec':{'namespaceName':'tenant-test-team','pool':{'mode':'mig','model':'NVIDIA RTX PRO 6000 Blackwell Server Edition','profile':'2g.48gb','nodes':['gpu-npu-worker01']}},'status':{'queue':'tenant-test-queue'}}
class Tests(unittest.TestCase):
 def test_server_derives_routing(self):
  p=pod_manifest(P,60,'tjob-000000000000')
  self.assertEqual(p['metadata']['namespace'],'tenant-test-team')
  self.assertEqual(p['metadata']['labels']['kai.scheduler/queue'],'tenant-test-queue')
  self.assertEqual(p['spec']['schedulerName'],'kai-scheduler')
  self.assertNotIn('nodeName',p['spec'])
  self.assertEqual(p['spec']['resourceClaims'],[{'name':'gpu','resourceClaimTemplateName':'tenant-gpu'}])
 def test_homogeneous_mig_request(self):
  t=template_spec(P['spec']['pool']);r=t['spec']['devices']['requests'][0]['exactly']
  self.assertEqual(r['count'],1);self.assertEqual(r['deviceClassName'],'mig.nvidia.com')
  self.assertIn('2g.48gb',r['selectors'][0]['cel']['expression'])
  self.assertIn('NVIDIA RTX PRO 6000',r['selectors'][0]['cel']['expression'])
 def test_whole_uses_gpu_class(self):
  pool=dict(P['spec']['pool'],mode='whole',model='NVIDIA RTX A6000')
  r=template_spec(pool)['spec']['devices']['requests'][0]['exactly']
  self.assertEqual(r['deviceClassName'],'gpu.nvidia.com')
  self.assertNotIn('profile',r['selectors'][0]['cel']['expression'])
 def test_request_cannot_supply_routing_or_unbounded_duration(self):
  for b in [{'durationSeconds':60,'queue':'foreign'},{'durationSeconds':True},{'durationSeconds':0},{'durationSeconds':181},{'durationSeconds':60,'image':'foreign'}]:
   with self.subTest(body=b),self.assertRaises(WorkloadError):submit(None,P,b)
if __name__=='__main__':unittest.main()
