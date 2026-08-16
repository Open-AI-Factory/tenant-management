"""Durable per-request journal. Queue scheduling remains KAI's responsibility."""
import copy
import datetime
import hashlib
import json
import re
import secrets
from errors import Error
from access import authorize, validate_bindings, same_identity, IDENTITY
from kubeutil import read, listing, BASE, OWNER
from context import resolve_context
from workloads import pod_manifest

CMS = '/api/v1/namespaces/monitoring/configmaps'
ACCESS = CMS + '/tenant-access-v1'
RECORD_LABEL = 'tenancy.gpu.platform.io/execution'
DIGEST = 'tenancy.gpu.platform.io/request-digest'
TERMINAL = {'Succeeded', 'Failed', 'Cancelled'}


def compact(value): return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
def digest(value): return hashlib.sha256(compact(value).encode()).hexdigest()
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()


def access_bindings(kube):
    cm = read(kube, ACCESS, optional=True)
    if cm is None: return []
    try: return validate_bindings(json.loads(cm['data']['bindings.json']))
    except (KeyError, ValueError, Error) as exc:
        raise Error('구성원 설정을 검증할 수 없습니다.', 503) from exc


def normalize_submission(body, context, principal):
    if not isinstance(body, dict) or set(body) - {'durationSeconds', 'namespaceName', 'requestId'}:
        raise Error('지원하지 않는 제출 필드입니다.')
    duration = body.get('durationSeconds')
    if type(duration) is not int or not 30 <= duration <= 180:
        raise Error('실행 시간은 30~180초 정수여야 합니다.')
    ns, request_id = body.get('namespaceName'), body.get('requestId')
    if principal.get('platformAdmin') is True and set(body) == {'durationSeconds'}:
        ns, request_id = context['namespaceName'], 'legacy-' + secrets.token_hex(12)
    if ns != context['namespaceName'] or not isinstance(request_id, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', request_id):
        raise Error('허용된 Namespace와 requestId가 필요합니다.')
    return {'namespaceName': ns, 'requestId': request_id, 'durationSeconds': duration}


def record_identity(context, request_id):
    return digest([context['tenantUID'], context['namespaceUID'], request_id])


def records(kube):
    return [cm for cm in listing(kube, CMS + '?labelSelector=' + RECORD_LABEL + '=v1')
            if cm['metadata'].get('labels', {}).get(RECORD_LABEL) == 'v1']


def decode(cm):
    try:
        record = json.loads(cm['data']['record.json'])
        if record['recordName'] != cm['metadata']['name']: raise ValueError('name mismatch')
        return record
    except (KeyError, ValueError, TypeError) as exc:
        raise Error('실행 기록이 손상되었습니다.', 409) from exc


def save(kube, cm, record):
    cm = copy.deepcopy(cm); cm['data'] = {'record.json': compact(record)}
    return kube.call(CMS + '/' + cm['metadata']['name'], 'PUT', body=cm)


def identity_live(kube, context):
    ns = read(kube, '/api/v1/namespaces/' + context['namespaceName'])
    if ns['metadata']['uid'] != context['namespaceUID'] or not any(
        o.get('kind') == 'Tenant' and o.get('uid') == context['tenantUID']
        for o in ns['metadata'].get('ownerReferences', [])):
        raise Error('Namespace 소속 또는 UID가 변경되었습니다.', 409)


def contains(actual, expected):
    """Expected fixed profile, allowing only API defaulting around requested fields."""
    if isinstance(expected,dict):
        return isinstance(actual,dict) and all(k in actual and contains(actual[k],v) for k,v in expected.items())
    if isinstance(expected,list):
        return isinstance(actual,list) and len(actual)==len(expected) and all(contains(a,b) for a,b in zip(actual,expected))
    return actual==expected


def verify_record_pod(record, pod):
    expected=record.get('expectedPod')
    if (not expected or not contains(pod,expected) or pod['metadata'].get('annotations',{}).get(DIGEST)!=record['requestDigest'] or
        pod['metadata'].get('labels',{}).get(OWNER)!=record['context']['policyUID'] or
        any(pod['spec'].get(k) for k in ('volumes','initContainers','ephemeralContainers','hostNetwork','hostPID','hostIPC','runtimeClassName'))):
        raise Error('기록한 고정 명세와 다른 Pod를 인수할 수 없습니다.',409)


def recover_existing(kube, record):
    """Bind an existing Pod to a durable intent; never create a Pod or infer authority."""
    identity_live(kube,record['context'])
    if record['pod'].get('uid') or not record.get('createAttempted'): return record
    path='/api/v1/namespaces/'+record['context']['namespaceName']+'/pods/'+record['pod']['name']
    pod=read(kube,path,optional=True)
    if pod is None: return record
    verify_record_pod(record,pod)
    cm=read(kube,CMS+'/'+record['recordName']); latest=decode(cm)
    if latest['requestDigest']!=record['requestDigest'] or latest['requestKey']!=record['requestKey']: raise Error('실행 기록이 변경되었습니다.',409)
    if latest['pod'].get('uid') not in (None,pod['metadata']['uid']): raise Error('Pod UID가 변경되었습니다.',409)
    latest['pod']['uid']=pod['metadata']['uid']; latest['phase']='Submitted'
    return decode(save(kube,cm,latest))


def replay_execution(kube, context, principal, body):
    authorize(principal,context['tenantUID'],'submit',access_bindings(kube))
    request=normalize_submission(body,context,principal); identity_live(kube,context)
    key=record_identity(context,request['requestId'])
    cm=read(kube,CMS+'/tenant-run-'+key[:24],optional=True)
    if cm is None: return None
    record=decode(cm)
    expected=digest({'key':key,'input':request,'actor':{k:principal[k] for k in IDENTITY}})
    if record['requestKey']!=key or record['requestDigest']!=expected: raise Error('requestId가 다른 입력 또는 사용자에게 속합니다.',409)
    record=recover_existing(kube,record)
    if record['pod'].get('uid'): return observe_execution(kube,record)
    if record.get('createAttempted'):
        record['release']='Unknown'; record['observationError']='이전 생성 결과를 확정할 수 없습니다. 자동 재생성하지 않습니다.'
        return record
    return None


def submit_execution(kube, context, principal, request):
    authorize(principal, context['tenantUID'], 'submit', access_bindings(kube))
    request = normalize_submission(request, context, principal)
    replay=replay_execution(kube,context,principal,request)
    if replay is not None: return replay,False
    policy = read(kube, BASE + '/' + context['policyName'])
    fresh = resolve_context(kube, policy, context['namespaceName'])
    if any(fresh[k] != context[k] for k in ('tenantUID', 'namespaceUID', 'policyUID', 'policyGeneration', 'queueUID', 'templateUID')):
        raise Error('제출 문맥이 변경되었습니다.', 409)
    if policy['spec']['resources']['memory']['maximum'] < 2000:
        raise Error('GPU 테스트에는 호스트 메모리 쿼터 2GB 이상이 필요합니다.')
    key = record_identity(context, request['requestId']); name = 'tenant-run-' + key[:24]
    cm = read(kube, CMS + '/' + name, optional=True)
    expected_digest = digest({'key': key, 'input': request, 'actor': {k: principal[k] for k in IDENTITY}})
    if cm is not None:
        record = decode(cm)
        if record['requestDigest'] != expected_digest or record['requestKey'] != key:
            raise Error('requestId가 다른 입력 또는 사용자에게 속합니다.', 409)
        if record.get('pod', {}).get('uid'):
            return observe_execution(kube, record), False
        created = False
    else:
        if len(records(kube)) >= 1000:
            raise Error('보존 기록 1000개에 도달했습니다. 운영자 보관·정리가 필요합니다.', 409)
        record = dict(recordName=name, requestKey=key, requestDigest=expected_digest,
            input=request, actor={k: principal[k] for k in IDENTITY}, context=copy.deepcopy(context),
            createdAt=now(), phase='Prepared', release='Pending', createAttempted=False,
            pod={'name': 'tjob-' + key[:12], 'uid': None}, claims={}, cancelRequested=False)
        p=copy.deepcopy(policy); p['spec']['namespaceName']=context['namespaceName']
        record['expectedPod']=pod_manifest(p,request['durationSeconds'],record['pod']['name'])
        record['expectedPod']['metadata']['annotations']={DIGEST:expected_digest}
        body = {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': name,
                'namespace': 'monitoring', 'labels': {RECORD_LABEL: 'v1', 'tenancy.gpu.platform.io/tenant-uid': context['tenantUID']}},
                'data': {'record.json': compact(record)}}
        try: cm = kube.call(CMS, 'POST', body=body)
        except Error as exc:
            if exc.status != 409: raise
            return submit_execution(kube, context, principal, request)
        created = True
    # Recheck current authority before any new Pod mutation, including recovery.
    authorize(principal, context['tenantUID'], 'submit', access_bindings(kube))
    fresh = resolve_context(kube, policy, context['namespaceName'])
    if fresh != context: raise Error('제출 직전 정책 또는 장치 경로가 변경되었습니다.', 409)
    path = '/api/v1/namespaces/' + context['namespaceName'] + '/pods'
    pod = read(kube, path + '/' + record['pod']['name'], optional=True)
    if pod is None:
        if record['createAttempted']:
            raise Error('이전 생성 결과를 확정할 수 없습니다. 자동 재생성을 중지합니다.', 409)
        record['createAttempted'] = True
        cm = save(kube, cm, record)
        manifest=record['expectedPod']
        try: pod = kube.call(path, 'POST', body=manifest)
        except Error as exc:
            if exc.status != 409: raise
            pod = read(kube, path + '/' + record['pod']['name'])
    verify_record_pod(record,pod)
    record['pod']['uid'] = pod['metadata']['uid']; record['phase'] = 'Submitted'
    cm = save(kube, cm, record)
    return decode(cm), created


def get_execution(kube, context, principal, request_id):
    try: authorize(principal, context['tenantUID'], 'read', access_bindings(kube))
    except Error as exc:
        if exc.status == 403: raise Error('실행을 찾을 수 없습니다.', 404) from exc
        raise
    identity_live(kube, context)
    cm = read(kube, CMS + '/tenant-run-' + record_identity(context, request_id)[:24], optional=True)
    if cm is None: raise Error('실행을 찾을 수 없습니다.', 404)
    record = decode(cm)
    if record['requestKey'] != record_identity(context, request_id): raise Error('실행 식별자 충돌입니다.', 409)
    return record


def observe_execution(kube, record):
    r = copy.deepcopy(record); ctx = r['context']
    try:
        identity_live(kube, ctx)
        pod = read(kube, '/api/v1/namespaces/' + ctx['namespaceName'] + '/pods/' + r['pod']['name'], optional=True)
        if pod is not None and pod['metadata']['uid'] != r['pod']['uid']:
            raise Error('Pod UID changed', 409)
        if pod is not None:
            r['phase'] = pod.get('status', {}).get('phase', 'Pending')
            r['node'] = pod.get('spec', {}).get('nodeName', '')
            r['message'] = next((c.get('message', '') for c in pod.get('status', {}).get('conditions', [])
                                 if c.get('type') == 'PodScheduled' and c.get('status') == 'False'), '')
        elif r.get('cancelRequested'): r['phase'] = 'Cancelled'
        claims = listing(kube, '/apis/resource.k8s.io/v1/namespaces/' + ctx['namespaceName'] + '/resourceclaims')
        for claim in claims:
            if any(o.get('uid') == r['pod']['uid'] for o in claim['metadata'].get('ownerReferences', [])):
                r['claims'].setdefault(claim['metadata']['name'], claim['metadata']['uid'])
        terminal = r['phase'] in TERMINAL or pod is None
        if terminal:
            released = bool(r['claims'])
            for name, uid in r['claims'].items():
                claim = next((c for c in claims if c['metadata']['name'] == name), None)
                if claim is not None and (claim['metadata']['uid'] != uid or
                    claim.get('status', {}).get('allocation') or claim.get('status', {}).get('reservedFor')):
                    released = False
            r['release'] = 'Verified' if released else 'Unknown'
        else: r['release'] = 'Pending'
    except Error:
        r['release'] = 'Unknown'; r['observationError'] = '실제 UID·할당·반납을 확인할 수 없습니다.'
    cm = read(kube, CMS + '/' + r['recordName'])
    old = decode(cm)
    if old['requestKey'] != r['requestKey']: raise Error('실행 기록이 변경되었습니다.', 409)
    # Do not erase a cancellation requested between observation and persistence.
    r['cancelRequested'] = old.get('cancelRequested', False) or r.get('cancelRequested', False)
    save(kube, cm, r)
    return r


def cancel_execution(kube, context, principal, request_id, pod_uid):
    record = get_execution(kube, context, principal, request_id)
    authorize(principal, context['tenantUID'], 'cancel', access_bindings(kube), record['actor'])
    if record['pod'].get('uid') != pod_uid or not pod_uid:
        raise Error('작업 UID가 변경되었습니다.', 409)
    record = observe_execution(kube, record)
    record['cancelRequested'] = True
    cm = read(kube, CMS + '/' + record['recordName']); save(kube, cm, record)
    try:
        kube.call('/api/v1/namespaces/' + context['namespaceName'] + '/pods/' + record['pod']['name'],
                  'DELETE', body={'apiVersion': 'v1', 'kind': 'DeleteOptions',
                                  'preconditions': {'uid': pod_uid}, 'gracePeriodSeconds': 5})
    except Error as exc:
        if exc.status != 404: raise
    return observe_execution(kube, record)
