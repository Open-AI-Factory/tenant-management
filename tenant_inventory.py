"""Capsule-first registry; a missing GPU policy never hides a tenant."""
from errors import Error
from access import authorize


def list_tenants(snapshot):
    if any(not isinstance(snapshot.get(k), list) for k in ('tenants', 'policies', 'namespaces')):
        raise Error('테넌트 목록 조회가 완전하지 않습니다.', 503)
    rows = []
    for tenant in snapshot['tenants']:
        meta = tenant['metadata']; name, uid = meta['name'], meta['uid']
        policies = [p for p in snapshot['policies'] if p.get('spec', {}).get('tenantName') == name]
        state, policy = 'PolicyMissing', None
        if len(policies) > 1:
            state = 'IdentityMismatch'
        elif policies:
            policy = policies[0]; status = policy.get('status', {})
            if status.get('tenantUID') not in (None, uid):
                state = 'IdentityMismatch'
            else:
                state = snapshot.get('policyStates', {}).get(policy['metadata']['uid'], 'Reconciling')
        spaces = []
        for ns in snapshot['namespaces']:
            m = ns['metadata']
            if m.get('labels', {}).get('capsule.clastix.io/tenant') == name and any(
                o.get('kind') == 'Tenant' and o.get('name') == name and o.get('uid') == uid
                for o in m.get('ownerReferences', [])):
                spaces.append({'name': m['name'], 'uid': m['uid']})
        if meta.get('deletionTimestamp'):
            state = 'IdentityMismatch'
        rows.append(dict(name=name, uid=uid, namespaces=sorted(spaces, key=lambda n: n['name']),
                         policyState=state, policy=policy,
                         capabilities={'submit': state == 'Ready'}))
    return sorted(rows, key=lambda row: row['name'])


def filter_tenants(principal, tenants, bindings):
    visible = []
    for row in tenants:
        try:
            authorize(principal, row['uid'], 'read', bindings)
        except Error:
            continue
        item = dict(row, capabilities=dict(row.get('capabilities', {})))
        for action in ('submit', 'add_namespace', 'configure', 'members'):
            try:
                authorize(principal, row['uid'], action, bindings)
                item['capabilities'][action] = action != 'submit' or row['policyState'] == 'Ready'
            except Error:
                item['capabilities'][action] = False
        visible.append(item)
    return visible
