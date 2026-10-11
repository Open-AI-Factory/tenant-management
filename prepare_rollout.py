"""Produce reviewable UID/RV-preconditioned JSON patches. Never contacts a cluster."""
import argparse
import json
from pathlib import Path

STAGES={'CustomResourceDefinition':0,'ServiceAccount':1,'ClusterRole':1,'ClusterRoleBinding':1,
        'Role':1,'RoleBinding':1,'ValidatingAdmissionPolicy':2,'ValidatingAdmissionPolicyBinding':2,
        'ConfigMap':3,'Deployment':4,'Service':4}


def prepare(items, identities):
    known={}
    for line in identities.splitlines():
        api,kind,ns,name,uid,rv=line.split('|')
        if not uid or not rv or (kind,ns,name) in known: raise ValueError('Invalid or duplicate baseline identity')
        known[(kind,ns,name)]=(uid,rv)
    operations=[]
    for obj in items:
        kind=obj['kind']; m=obj['metadata']; key=(kind,m.get('namespace',''),m['name'])
        if key not in known:
            operations.append({'stage':STAGES[kind],'action':'create','object':obj})
            continue
        # These unchanged bootstrap objects do not need a rewrite.
        if kind in ('Service','ServiceAccount','ClusterRoleBinding'): continue
        uid,rv=known[key]
        fields={'ClusterRole':['rules'],'Role':['rules'],'RoleBinding':['roleRef','subjects'],
                'ConfigMap':['data']}.get(kind,['spec'])
        patch=[{'op':'test','path':'/metadata/uid','value':uid},
               {'op':'test','path':'/metadata/resourceVersion','value':rv}]
        patch += [{'op':'add','path':'/'+field,'value':obj[field]} for field in fields]
        operations.append({'stage':STAGES[kind],'action':'json-patch','apiVersion':obj['apiVersion'],
            'kind':kind,'namespace':m.get('namespace'),'name':m['name'],'patch':patch})
    return {'status':'REVIEW_ONLY_NOT_APPLIED','operations':sorted(operations,key=lambda x:x['stage'])}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',default='resources.json'); p.add_argument('--identities',required=True); p.add_argument('--output',required=True)
    a=p.parse_args()
    result=prepare(json.loads(Path(a.manifest).read_text(encoding='utf-8'))['items'],Path(a.identities).read_text(encoding='utf-8-sig'))
    Path(a.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
