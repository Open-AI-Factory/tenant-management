import json
import subprocess
import sys
import unittest
import tempfile
from pathlib import Path


class PackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run([sys.executable,'-B','build.py'],check=True,capture_output=True)
        cls.items=json.loads(Path('resources.json').read_text(encoding='utf-8'))['items']

    def test_all_runtime_modules_shipped(self):
        cm=next(x for x in self.items if x['kind']=='ConfigMap' and x['metadata']['name']=='tenant-admin')
        for name in ('runtime.py','tenant_api.py','http_app.py','errors.py','access.py','executions.py','context.py','kubeutil.py','namespaces.py','network_policy.py','admission.py','tenant_inventory.py','tenant_usage.py'):
            self.assertIn(name,set(cm['data']))
        self.assertLess(len(json.dumps(cm).encode()),1000000)

    def test_build_without_operator_work_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            for f in Path('.').iterdir():
                if f.is_file() and f.suffix in ('.py','.html','.js','.css','.json'):
                    (Path(folder)/f.name).write_bytes(f.read_bytes())
            result=subprocess.run([sys.executable,'-B','build.py'],cwd=folder,capture_output=True,text=True,timeout=15)
            self.assertEqual(0,result.returncode,result.stderr)

    def test_shipped_demo_runs_without_repository_files(self):
        cm=next(x for x in self.items if x['kind']=='ConfigMap' and x['metadata']['name']=='tenant-admin')
        with tempfile.TemporaryDirectory() as folder:
            for name,content in cm['data'].items():
                (Path(folder)/name).write_text(content,encoding='utf-8')
            script='''from pathlib import Path
from http_app import make_handler
from http.server import ThreadingHTTPServer
import threading,urllib.request,json
def auth(headers):return {'platformAdmin':True},'token'
server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(None,auth,'http://demo','https://grafana',Path('.')))
thread=threading.Thread(target=server.serve_forever);thread.start()
try:
    url='http://127.0.0.1:'+str(server.server_port)
    with urllib.request.urlopen(url+'/quota-demo') as r:assert r.status==200
    for _ in range(2):
        req=urllib.request.Request(url+'/api/quota-demo/scenario/next',data=b'{}',headers={'Content-Type':'application/json','Origin':'http://demo','X-CSRF-Token':'token'})
        with urllib.request.urlopen(req) as r:state=json.load(r)
    assert state['quotas']=={'tenant-a':3,'tenant-b':1}
finally:server.shutdown();server.server_close();thread.join()
'''
            result=subprocess.run([sys.executable,'-B','-c',script],cwd=folder,capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_configmaps_namespaced_only_and_no_secret_permissions(self):
        cluster=next(x for x in self.items if x['kind']=='ClusterRole')
        self.assertFalse(any('configmaps' in r['resources'] or 'secrets' in r['resources'] for r in cluster['rules']))
        role=next((x for x in self.items if x['kind']=='Role'),None)
        self.assertIsNotNone(role); self.assertEqual(role['metadata']['namespace'],'monitoring')
        self.assertTrue(any('configmaps' in r['resources'] and 'update' in r['verbs'] for r in role['rules']))

    def test_runtime_identity_and_staged_reconcile_gate(self):
        deploy=next(x for x in self.items if x['kind']=='Deployment')
        self.assertEqual(deploy['spec']['replicas'],1)
        container=deploy['spec']['template']['spec']['containers'][0]
        env={v['name']:v['value'] for v in container['env']}
        self.assertTrue(env.get('GRAFANA_INSTANCE_ID'))
        self.assertEqual(env.get('TENANT_RECONCILE_ENABLED'),'false')
        self.assertEqual(env.get('TENANT_MUTATIONS_ENABLED'),'false')

    def test_held_budget_modules_are_shipped_with_gate_closed(self):
        cm=next(x for x in self.items if x['kind']=='ConfigMap' and x['metadata']['name']=='tenant-admin')
        for name in ('tenant_budget_policy.py','kueue_binding.py','kueue_quota_store.py','kueue_quota_control.py',
                     'kueue_quota_http.py','kueue_quota.html','kueue_quota.js','kueue_quota.css','dummy_kueue_profiler.py'):
            self.assertIn(name,set(cm['data']))
        deploy=next(x for x in self.items if x['kind']=='Deployment')
        env={v['name']:v.get('value') for v in deploy['spec']['template']['spec']['containers'][0]['env']}
        self.assertEqual('false',env.get('KUEUE_QUOTA_ENABLED'))
        with tempfile.TemporaryDirectory() as folder:
            for name,content in cm['data'].items(): (Path(folder)/name).write_text(content,encoding='utf-8')
            script="from kueue_quota_http import KueueQuotaHTTP; from pathlib import Path; h=KueueQuotaHTTP(None,Path('.')); p=dict(platformAdmin=True,providerInstance='g',subjectKind='uid',subjectId='admin'); assert h.dispatch(p,'GET','/kueue-quota')[0]==200; assert h.dispatch(p,'GET','/api/kueue-quota/state')[1]['enabled'] is False"
            result=subprocess.run([sys.executable,'-B','-c',script],cwd=folder,capture_output=True,text=True,timeout=10)
            self.assertEqual(0,result.returncode,result.stderr)
