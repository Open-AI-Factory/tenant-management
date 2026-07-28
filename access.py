"""Portal authorization. Principals must originate in verified Grafana responses."""
from errors import Error

IDENTITY = ('providerInstance', 'subjectKind', 'subjectId')
ACTIONS = {'read', 'submit', 'cancel', 'add_namespace', 'configure', 'members'}
ROLES = {'viewer': {'read'}, 'member': {'read', 'submit'},
         'tenant-admin': {'read', 'submit', 'cancel', 'add_namespace'}}


def principal_from_user(user, instance_id):
    if not isinstance(instance_id, str) or not instance_id or len(instance_id) > 200:
        raise Error('인증 제공자 설정이 올바르지 않습니다.', 503)
    uid, number = user.get('uid'), user.get('id')
    if isinstance(uid, str) and uid.strip() and len(uid) <= 200:
        kind, subject = 'uid', uid
    elif type(number) is int and number > 0:
        kind, subject = 'id', str(number)
    else:
        raise Error('검증 가능한 사용자 식별자가 없습니다.', 401)
    return dict(providerInstance=instance_id, subjectKind=kind, subjectId=subject,
                login=str(user.get('login', ''))[:128], platformAdmin=user.get('isGrafanaAdmin') is True)


def same_identity(left, right):
    return bool(left and right) and all(left.get(k) is not None and left.get(k) == right.get(k) for k in IDENTITY)


def role_for(principal, tenant_uid, bindings):
    matches = [b['role'] for b in bindings if b.get('tenantUID') == tenant_uid and same_identity(principal, b)]
    return matches[0] if len(matches) == 1 and matches[0] in ROLES else None


def authorize(principal, tenant_uid, action, bindings, owner=None):
    if action not in ACTIONS or not tenant_uid:
        raise Error('허용되지 않은 동작입니다.', 403)
    if principal.get('platformAdmin') is True:
        return
    role = role_for(principal, tenant_uid, bindings)
    if action in ROLES.get(role, set()):
        return
    if role == 'member' and action == 'cancel' and same_identity(principal, owner):
        return
    raise Error('이 테넌트에서 해당 작업을 수행할 권한이 없습니다.', 403)


def validate_bindings(bindings):
    if not isinstance(bindings, list) or len(bindings) > 1000:
        raise Error('구성원 목록은 1000개 이하의 배열이어야 합니다.')
    seen = set()
    required = set(IDENTITY) | {'tenantUID', 'role'}
    for b in bindings:
        if not isinstance(b, dict) or set(b) != required:
            raise Error('구성원 필드가 올바르지 않습니다.')
        if any(not isinstance(b[k], str) or not b[k] or len(b[k]) > 200 for k in required):
            raise Error('구성원 식별자가 올바르지 않습니다.')
        if b['subjectKind'] not in ('uid', 'id') or b['role'] not in ROLES:
            raise Error('지원하지 않는 식별자 또는 역할입니다.')
        key = tuple(b[k] for k in IDENTITY) + (b['tenantUID'],)
        if key in seen:
            raise Error('같은 사용자의 테넌트 역할이 중복됐습니다.')
        seen.add(key)
    return bindings
