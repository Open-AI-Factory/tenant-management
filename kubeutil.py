import urllib.parse
from errors import Error

BASE = '/apis/tenancy.gpu.platform.io/v1alpha1/tenantresourcepolicies'
TENANTS = '/apis/capsule.clastix.io/v1beta2/tenants'
QUEUES = '/apis/scheduling.run.ai/v2/queues'
OWNER = 'tenancy.gpu.platform.io/policy-uid'
MANAGED = 'tenancy.gpu.platform.io/console'


def read(kube, path, optional=False):
    try:
        return kube.call(path, optional=optional)
    except Error as exc:
        if exc.status in (404, 409): raise
        raise Error('권위 있는 API를 조회할 수 없습니다.', 503, 'Unavailable') from exc
    except Exception as exc:
        raise Error('권위 있는 API를 조회할 수 없습니다.', 503, 'Unavailable') from exc


def listing(kube, path):
    items, page, seen = [], path, set()
    while True:
        response = read(kube, page)
        if not isinstance(response, dict) or not isinstance(response.get('items'), list):
            raise Error('API 목록이 완전하지 않습니다.', 503, 'Unavailable')
        items.extend(response['items'])
        cursor = response.get('metadata', {}).get('continue')
        if not cursor: return items
        if cursor in seen: raise Error('반복된 API 페이지입니다.', 503, 'Unavailable')
        seen.add(cursor)
        page = path + ('&' if '?' in path else '?') + urllib.parse.urlencode({'continue': cursor})


def metadata(name, uid, namespace=None):
    result = {'name': name, 'labels': {MANAGED: 'true', OWNER: uid}}
    if namespace: result['namespace'] = namespace
    return result


def ensure(kube, path, desired, uid, update=False):
    old = read(kube, path + '/' + desired['metadata']['name'], optional=True)
    if old is None: return kube.call(path, 'POST', desired)
    if old['metadata'].get('labels', {}).get(OWNER) != uid or old['metadata'].get('deletionTimestamp'):
        raise Error('기존 객체 소유권이 일치하지 않습니다.', 409, 'IdentityMismatch')
    if update and old.get('spec') != desired.get('spec'):
        old['spec'] = desired['spec']
        return kube.call(path + '/' + old['metadata']['name'], 'PUT', old)
    return old
