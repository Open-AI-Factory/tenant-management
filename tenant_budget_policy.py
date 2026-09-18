"""Capsule-linked policy validation; this is not a Capsule CRD."""
import copy
import hashlib
import json
import math


def check_json(value, depth=0):
    if depth > 32:
        raise ValueError('JSON depth exceeds 32')
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str): raise ValueError('JSON key must be text')
            key.encode('utf-8')
            check_json(item, depth + 1)
    elif isinstance(value, list):
        for item in value: check_json(item, depth + 1)
    elif isinstance(value, str):
        value.encode('utf-8')
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError('Non-finite JSON number')
    elif value is not None and type(value) not in (str, int, float, bool):
        raise ValueError('Unsupported JSON value')


def strict_json(raw, maximum=32768):
    if isinstance(raw, str): raw = raw.encode('utf-8')
    if len(raw) > maximum:
        raise ValueError('JSON too large')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    def invalid(value): raise ValueError('Non-finite JSON number')
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique, parse_constant=invalid)
    check_json(value)
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode('utf-8')).hexdigest()


def integer(value, low, high):
    return type(value) is int and low <= value <= high


def validate_policy(policy: dict, targets: dict) -> dict:
    if not isinstance(policy, dict) or not isinstance(targets, dict):
        raise ValueError('Policy/targets required')
    if (targets.get('schemaVersion') != 1 or type(targets.get('schemaVersion')) is not int or
            targets.get('envelopeDevices') != 2 or type(targets.get('envelopeDevices')) is not int):
        raise ValueError('Unsupported target envelope')
    for key, expected in [('poolKey', 'example-gpu-pool'), ('resource', 'nvidia.com/gpu'), ('unit', 'device')]:
        if policy.get(key) != expected or targets.get(key) != expected:
            raise ValueError('Pool/resource/unit mismatch')
    entries = targets.get('entries')
    if not isinstance(entries, dict) or len(entries) != 2:
        raise ValueError('Exactly two dedicated test targets required')
    namespaces, namespace_uids, queues, queue_uids, local_uids = set(), set(), set(), set(), set()
    for uid, row in entries.items():
        if not isinstance(row, dict) or row.get('tenantUID') != uid or not uid:
            raise ValueError('Target tenant UID mismatch')
        if row.get('tenantName') not in ('example-tenant-a', 'example-tenant-b'):
            raise ValueError('Unapproved tenant')
        suffix = row['tenantName'][-1]
        cq = row.get('clusterQueue', {})
        if cq.get('name') != 'example-tenant-queue-' + suffix or not cq.get('uid'):
            raise ValueError('Unapproved queue')
        if cq['name'] in queues or cq['uid'] in queue_uids:
            raise ValueError('Tenants must not share ClusterQueue')
        queues.add(cq['name']); queue_uids.add(cq['uid'])
        spaces = row.get('namespaces')
        if not isinstance(spaces, list) or not 1 <= len(spaces) <= 3:
            raise ValueError('One to three namespaces required')
        for ns in spaces:
            lq, rq = ns.get('localQueue', {}), ns.get('resourceQuota', {})
            if (not ns.get('name') or not ns.get('uid') or not lq.get('uid') or
                    lq.get('name') != row['tenantName'] or not rq.get('name') or not rq.get('uid')):
                raise ValueError('Pinned namespace/queue/lock identity required')
            if ns['name'] in namespaces or ns['uid'] in namespace_uids or lq['uid'] in local_uids:
                raise ValueError('Namespace/LocalQueue identity is shared')
            namespaces.add(ns['name']); namespace_uids.add(ns['uid']); local_uids.add(lq['uid'])
    target = entries.get(policy.get('tenantUID'))
    if target is None:
        raise ValueError('Tenant not allowlisted')
    for key in ('tenantName', 'tenantUID', 'namespaces', 'clusterQueue', 'flavor', 'placement'):
        if policy.get(key) != target.get(key):
            raise ValueError('Pinned ' + key + ' mismatch')
    if policy.get('backend') != 'kueue' or policy.get('source') != 'dummy-profiler':
        raise ValueError('Unsupported backend/source')
    if (policy['flavor'].get('name') != 'example-gpu-flavor' or not policy['flavor'].get('uid') or
            policy['placement'].get('schedulerName') != 'kai-scheduler' or
            policy['placement'].get('kaiQueueName') != 'example-kai-queue' or
            not policy['placement'].get('kaiQueueUID')):
        raise ValueError('Unapproved execution reference')
    if not integer(policy.get('revision'), 1, 2147483647):
        raise ValueError('Positive policy revision required')
    for key in ('minDevices', 'maxDevices', 'headroomDevices'):
        if not integer(policy.get(key), 0, 2):
            raise ValueError('Device bounds must be integers 0..2')
    if policy['minDevices'] > policy['maxDevices']:
        raise ValueError('Minimum exceeds maximum')
    for key in ('leadSeconds', 'downHoldSeconds', 'cooldownSeconds', 'maxAgeSeconds', 'maxSampleGapSeconds'):
        if not integer(policy.get(key), 0 if key in ('leadSeconds', 'cooldownSeconds') else 1, 3600):
            raise ValueError('Invalid time rule: ' + key)
    confidence = policy.get('minConfidence')
    if type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError('Invalid confidence')
    result = copy.deepcopy(policy)
    result.pop('digest', None)
    result['digest'] = digest(result)
    return result
