import copy,unittest
from server import validate,Error,ensure,reconcile,OWNER
POOL={'key':'whole-test','model':'NVIDIA RTX A6000','mode':'whole','capacity':2,'nodes':['node1','node2']}
BODY={'name':'test-team','displayName':'Test team','contact':'','pool':'whole-test','gpu':1,'cpu':4,'memoryGB':16}
class KubeFake:
    def __init__(self):self.objects={};self.calls=[];self.occupied=False
    def call(self,path,method='GET',body=None,optional=False):
        self.calls.append((method,path))
        if path.endswith('/pods') or path.endswith('/resourceclaims'):return {'items':[{}] if self.occupied else []}
        if method=='GET':return copy.deepcopy(self.objects.get(path))
        obj=copy.deepcopy(body)
        if method=='POST':
            obj['metadata']['uid']='uid-'+obj['metadata']['name'];obj['metadata']['resourceVersion']='1'
            path+='/'+obj['metadata']['name']
        self.objects[path]=obj
        return copy.deepcopy(obj)
class Tests(unittest.TestCase):
    def test_valid_units(self):
        d=validate(BODY,[POOL]);self.assertEqual(d['resources']['cpu']['maximum'],4000);self.assertEqual(d['namespaceName'],'tenant-test-team')
    def test_input_rejections(self):
        for patch in [{'name':'../../kube-system'},{'gpu':True},{'gpu':3},{'cpu':1.5},{'pool':'foreign'},{'memoryGB':0},{'unexpected':'x'}]:
            with self.subTest(patch=patch),self.assertRaises(Error):validate(dict(BODY,**patch),[POOL])
    def test_no_adoption(self):
        k=KubeFake();k.objects['/x/a']={'metadata':{'name':'a','labels':{OWNER:'other'}}}
        with self.assertRaises(Error):ensure(k,'/x',{'metadata':{'name':'a'}},'ours')
    def test_reconcile_is_idempotent_and_locked(self):
        k=KubeFake();p={'metadata':{'name':'test-team','uid':'policy1','generation':1},'spec':validate(BODY,[POOL])}
        first=reconcile(k,p);self.assertEqual(first['phase'],'Ready');self.assertFalse(first['workloadsEnabled'])
        count=sum(m!='GET' for m,p in k.calls);second=reconcile(k,p)
        self.assertEqual(count,sum(m!='GET' for m,p in k.calls));self.assertEqual(first,second)
        quota=k.objects['/api/v1/namespaces/tenant-test-team/resourcequotas/configuration-test-lock'];self.assertEqual(quota['spec']['hard']['pods'],'0')
    def test_occupied_namespace_stops_mutation(self):
        k=KubeFake();p={'metadata':{'name':'test-team','uid':'policy1','generation':1},'spec':validate(BODY,[POOL])}
        reconcile(k,p);before=copy.deepcopy(k.objects);k.occupied=True;p['spec']['resources']['gpu']={'guaranteed':2,'maximum':2}
        with self.assertRaises(Error):reconcile(k,p)
        self.assertEqual(k.objects,before)

if __name__=='__main__':unittest.main()
