"""Read-only live-test preflight. Observations are not a claim of scenario success."""
import json
import re
import argparse
from errors import Error
from kubeutil import read, listing
from executions import identity_live, now

CASES={'access','namespaces','quota','retry','cancel','network','protected'}


def verify_mvp(client, case, config):
    namespace=config.get('namespaceName')
    if case not in CASES or config.get('mutate') or config.get('baselineComplete') is not True:
        raise Error('완전한 기준선과 읽기 전용 시험 항목이 필요합니다.',409)
    if not isinstance(namespace,str) or not re.fullmatch('tenant-[a-z0-9-]{1,56}',namespace) or namespace not in config.get('allowedNamespaces',[]):
        raise Error('명시된 시험 Namespace만 조회할 수 있습니다.',403)
    if not config.get('namespaceUID') or not config.get('tenantUID'): raise Error('시험 객체 UID가 필요합니다.',409)
    identity_live(client,config)
    observations={'namespace':read(client,'/api/v1/namespaces/'+namespace)}
    if case in ('quota','retry','cancel'):
        observations['pods']=listing(client,'/api/v1/namespaces/'+namespace+'/pods')
        observations['claims']=listing(client,'/apis/resource.k8s.io/v1/namespaces/'+namespace+'/resourceclaims')
    if case=='network':
        observations['networkPolicy']=read(client,'/apis/networking.k8s.io/v1/namespaces/'+namespace+'/networkpolicies/tenant-boundary')
    if case=='protected':
        protected=config.get('protectedPods',[])
        if not protected: raise Error('비교할 보호 Pod 기준선이 없습니다.',409)
        observations['protectedPods']=[]
        for expected in protected:
            ns,name=expected['namespace'],expected['name']
            if not all(isinstance(x,str) and re.fullmatch('[a-z0-9-]{1,253}',x) for x in (ns,name)): raise Error('잘못된 보호 Pod 이름입니다.')
            pod=read(client,'/api/v1/namespaces/'+ns+'/pods/'+name)
            statuses=pod.get('status',{}).get('containerStatuses',[])
            current={'namespace':ns,'name':name,'uid':pod['metadata']['uid'],
                'ready':bool(statuses) and all(s['ready'] for s in statuses),'restarts':sum(s['restartCount'] for s in statuses)}
            observations['protectedPods'].append(current)
            if current!=expected: raise Error('보호 Pod 기준선이 변경되었습니다.',409)
    return {'case':case,'collectedAt':now(),'mutations':0,'verdict':'NotRun',
        'note':'사전 관측만 완료. 계정·GPU·admission·CNI 시나리오는 승인된 범위에서 별도 실행해야 합니다.',
        'observations':observations}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case',required=True,choices=sorted(CASES)); parser.add_argument('--config',required=True)
    args=parser.parse_args()
    from server import Kube
    with open(args.config,encoding='utf-8') as stream: config=json.load(stream)
    print(json.dumps(verify_mvp(Kube(),args.case,config),ensure_ascii=False,indent=2))
