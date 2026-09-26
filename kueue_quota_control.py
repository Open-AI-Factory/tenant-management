"""Single-writer, held-queue forecast control with durable apply intents."""
import copy
import math
import time
from datetime import datetime, timezone
from errors import Error
from tenant_budget_policy import validate_policy, digest, check_json
from kueue_binding import observe_binding, execution_binding, gpu_resource, devices, CQ
from kueue_quota_store import QuotaStore

FIELDS = {'eventId', 'source', 'tenantUID', 'poolKey', 'unit', 'sequence', 'issuedAt',
          'windowStart', 'windowEnd', 'expectedDevices', 'confidence'}


def stamp(now):
    return datetime.fromtimestamp(now, timezone.utc).isoformat().replace('+00:00', 'Z')


def instant(value):
    if not isinstance(value, str): raise ValueError('Timezone-aware timestamp required')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None: raise ValueError('Timezone required')
    return parsed.timestamp()


def projection(cq):
    """Ignore server metadata/status; normalize GPU quantity string vs number."""
    spec = copy.deepcopy(cq['spec'])
    for group in spec.get('resourceGroups', []):
        for flavor in group.get('flavors', []):
            for resource in flavor.get('resources', []):
                if resource.get('name') == 'nvidia.com/gpu':
                    resource['nominalQuota'] = str(devices(resource['nominalQuota']))
    meta = cq['metadata']
    return {'spec': spec, 'metadata': {key: copy.deepcopy(meta.get(key, default)) for key, default in
            [('name', None), ('uid', None), ('labels', {}), ('annotations', {}),
             ('ownerReferences', []), ('finalizers', [])]}}


class KueueQuotaControl:
    def __init__(self, kube, enabled: bool = False, clock=None, store=None):
        self.kube = kube
        self.enabled = enabled
        self.clock = clock or time.time
        self.store = store or QuotaStore(kube)
        self._view = None

    @staticmethod
    def authorize(actor):
        if (not isinstance(actor, dict) or actor.get('platformAdmin') is not True or
                any(not isinstance(actor.get(k), str) or not actor[k] for k in
                    ('providerInstance', 'subjectKind', 'subjectId'))):
            raise Error('플랫폼 관리자 인증이 필요합니다.', 403)

    def _load(self):
        targets, document, state = self.store.load_targets(), self.store.load_policy(), self.store.load_state()
        policies = {uid: validate_policy(p, targets) for uid, p in document['entries'].items()}
        if set(policies) != set(targets['entries']) or any(p['tenantUID'] != uid for uid, p in policies.items()):
            raise Error('Policy and allowlist identities differ', 503, 'Unavailable')
        if set(state['tenants']) - set(policies):
            raise Error('State contains unregistered tenant identity', 409, 'IdentityMismatch')
        observations = {uid: observe_binding(self.kube, p, targets) for uid, p in policies.items()}
        for uid, p in policies.items():
            row = state['tenants'].setdefault(uid, {})
            row.setdefault('ledger', {}); row.setdefault('history', []); row.setdefault('events', [])
            row.setdefault('phase', 'Deferred'); row.setdefault('reason', 'NoForecast')
            if 'baseline' not in row and observations[uid]['guards']['ok']:
                row['baseline'] = projection(observations[uid]['clusterQueue'])
        return targets, policies, state, observations

    def _render(self, targets, policies, state, observations):
        result = {'enabled': self.enabled, 'serverTime': stamp(self.clock()), 'envelopeDevices': 2,
                  'unit': 'device', 'tenants': {}}
        for uid, p in policies.items():
            row = copy.deepcopy(state['tenants'][uid]); obs = observations[uid]
            row.update(policy=copy.deepcopy(p), binding=copy.deepcopy(obs),
                       actual={'quota': obs['quota'], 'resourceVersion': obs['resourceVersion'],
                               'generation': obs['generation']},
                       execution=execution_binding(p, targets.get('admissionCoverage', {'state': 'Unknown'})))
            if not self.enabled:
                row.update(phase='Deferred', reason='Disabled')
            elif not obs['guards']['ok']:
                row.update(phase='Unavailable' if obs['guards']['reason'] == 'Unavailable' else 'Deferred',
                           reason=obs['guards']['reason'])
            elif row.get('intent') and row['phase'] != 'Conflict':
                row.update(phase='ApplyUnknown', reason='PendingReadback')
            elif row.get('baseline') != projection(obs['clusterQueue']):
                row.update(phase='Conflict', reason='ExternalEdit')
            result['tenants'][uid] = row
        self._view = copy.deepcopy(result)
        return result

    def _unavailable(self, reason):
        result = copy.deepcopy(self._view) if self._view else {'enabled': self.enabled, 'tenants': {}}
        result.update(serverTime=stamp(self.clock()), phase='Unavailable', reason=reason)
        for row in result['tenants'].values():
            row.update(phase='Unavailable', reason=reason)
            row['actual'] = {'quota': None, 'resourceVersion': None, 'generation': None}
        return result

    def state(self) -> dict:
        try:
            return self._render(*self._load())
        except (Error, ValueError, KeyError, TypeError) as exc:
            return self._unavailable(getattr(exc, 'code', None) or 'Unavailable')

    def _validate_event(self, event, p, now):
        try:
            check_json(event)
            if not isinstance(event, dict) or set(event) != FIELDS: raise ValueError('Forecast fields mismatch')
            for key in ('eventId', 'source', 'tenantUID', 'poolKey', 'unit'):
                if not isinstance(event[key], str) or not 1 <= len(event[key]) <= 200:
                    raise ValueError('Invalid forecast identity')
            for key in ('source', 'tenantUID', 'poolKey', 'unit'):
                if event[key] != p[key]: raise ValueError('Forecast policy binding mismatch')
            if type(event['sequence']) is not int or not 1 <= event['sequence'] <= 2147483647:
                raise ValueError('Positive integer sequence required')
            for key in ('expectedDevices', 'confidence'):
                if type(event[key]) not in (int, float) or not math.isfinite(event[key]):
                    raise ValueError('Finite quantity/confidence required')
            if not 0 <= event['expectedDevices'] <= 1000000 or not p['minConfidence'] <= event['confidence'] <= 1:
                raise ValueError('Invalid demand/confidence')
            issued, start, end = (instant(event[key]) for key in ('issuedAt', 'windowStart', 'windowEnd'))
            if not issued <= start < end or issued > now or now - issued > p['maxAgeSeconds'] or end <= now:
                raise ValueError('Invalid/stale prediction time')
            return issued, start, end
        except (ValueError, UnicodeError, TypeError, KeyError, OverflowError, RecursionError) as exc:
            raise Error('예측 입력을 확인하세요: ' + str(exc), 400, 'InvalidForecast') from exc

    def receive(self, event: dict, actor: dict) -> dict:
        self.authorize(actor)
        if not self.enabled: raise Error('실제 쿼터 제어가 비활성 상태입니다.', 503, 'Disabled')
        try:
            check_json(event)
            targets, policies, state, observations = self._load()
            uid = event.get('tenantUID') if isinstance(event, dict) else None
            if uid not in policies: raise Error('등록된 Capsule Tenant UID가 아닙니다.', 400)
            p, row = policies[uid], state['tenants'][uid]
            if not observations[uid]['guards']['ok']:
                raise Error('실제 테넌트/큐 연결을 확인할 수 없습니다.', 409, observations[uid]['guards']['reason'])
            now = self.clock(); fingerprint = digest(event)
            ledger = {key: value for key, value in row['ledger'].items() if now - value['receivedAt'] < 86400}
            if event.get('eventId') in ledger:
                old = ledger[event['eventId']]
                if old['fingerprint'] != fingerprint: raise Error('같은 eventId에 다른 입력입니다.', 409, 'Conflict')
                return {'status': 'Duplicate', 'tenantUID': uid, 'policyRevision': old['policyRevision']}
            issued, start, end = self._validate_event(event, p, now)
            if event['sequence'] <= row.get('lastSequence', 0) or issued < row.get('lastIssuedAt', issued):
                raise Error('역순 예측입니다.', 409, 'OutOfOrder')
            if len(ledger) >= 1000: raise Error('수신 기록이 가득 찼습니다.', 429, 'LedgerCapacity')
            raw_target = math.ceil(event['expectedDevices'] + p['headroomDevices'])
            target = max(p['minDevices'], min(p['maxDevices'], raw_target))
            old_low = row.get('low')
            if target < observations[uid]['quota']:
                if (not old_low or old_low['target'] != target or old_low['policyDigest'] != p['digest'] or
                        issued - old_low['lastSample'] > p['maxSampleGapSeconds']):
                    old_low = {'start': issued, 'lastSample': issued, 'count': 0,
                               'target': target, 'policyDigest': p['digest']}
                old_low['lastSample'] = issued; old_low['count'] += 1
                row['low'] = old_low
            else: row.pop('low', None)
            ledger[event['eventId']] = {'fingerprint': fingerprint, 'receivedAt': now, 'policyRevision': p['revision']}
            row.update(ledger=ledger, lastSequence=event['sequence'], lastIssuedAt=issued, target=target,
                       forecast={'event': copy.deepcopy(event), 'fingerprint': fingerprint,
                                 'receivedAt': now, 'policyRevision': p['revision'], 'policyDigest': p['digest'],
                                 'targetsDigest': digest({k: v for k, v in targets.items() if not k.startswith('_')}),
                                 'issued': issued, 'start': start, 'end': end,
                                 'demandExceedsPolicy': raw_target > p['maxDevices']})
            row['events'] = (row['events'] + [{'eventId': event['eventId'], 'sequence': event['sequence'],
                                             'at': stamp(now), 'target': target}])[-100:]
            state = self.store.save_state(state)
            self._render(targets, policies, state, observations)
            return {'status': 'Received', 'tenantUID': uid, 'policyRevision': p['revision'], 'target': target}
        except Error:
            raise
        except (ValueError, KeyError, TypeError, UnicodeError, RecursionError) as exc:
            raise Error('예측 또는 예산 설정을 확인하세요.', 400, 'InvalidForecast') from exc

    def _finish(self, row, obs, now):
        intent = row['intent']
        row['baseline'] = projection(obs['clusterQueue'])
        row['lastChange'] = now
        row['history'] = (row['history'] + [{'at': stamp(now), 'eventId': intent['eventId'],
            'policyRevision': intent['policyRevision'], 'from': intent['previous'], 'to': intent['target'],
            'clusterQueueUID': intent['clusterQueueUID'], 'resourceVersion': obs['resourceVersion'],
            'generation': obs['generation'], 'phase': 'QuotaVerified'}])[-100:]
        row.update(phase='QuotaVerified', reason='NominalQuotaReadback')
        row.pop('intent', None); row.pop('low', None)

    def _stored_forecast_valid(self, row, p):
        try:
            f = row['forecast']; e = f['event']
            issued, start, end = self._validate_event(e, p, f['receivedAt'])
            expected = max(p['minDevices'], min(p['maxDevices'], math.ceil(e['expectedDevices'] + p['headroomDevices'])))
            return (type(row['target']) is int and row['target'] == expected and f['fingerprint'] == digest(e) and
                    (f['issued'], f['start'], f['end']) == (issued, start, end) and
                    e['sequence'] == row['lastSequence'] and issued == row['lastIssuedAt'])
        except (Error, ValueError, KeyError, TypeError, OverflowError):
            return False

    def tick(self) -> dict:
        if not self.enabled: return self.state()
        try:
            targets, policies, state, observations = self._load()
            self._render(targets, policies, state, observations)
            now = self.clock()
            pending = [uid for uid, row in state['tenants'].items() if row.get('intent')]
            if pending:
                for uid in pending:
                    row, obs = state['tenants'][uid], observations[uid]
                    intent = row['intent']
                    if not obs['guards']['ok']:
                        row.update(phase='ApplyUnknown', reason='ReadbackUnavailable')
                    elif (obs['clusterQueue']['metadata']['uid'] == intent['clusterQueueUID'] and
                            projection(obs['clusterQueue']) == intent['after']):
                        self._finish(row, obs, now)
                    elif projection(obs['clusterQueue']) != intent['before']:
                        row.update(phase='Conflict', reason='ExternalEdit')
                    else:
                        row.update(phase='ApplyUnknown', reason='WriteOutcomeUnconfirmed')
                state = self.store.save_state(state)
                return self._render(targets, policies, state, observations)
            conflicts = False
            for uid, row in state['tenants'].items():
                obs = observations[uid]
                if obs['guards']['ok'] and row.get('baseline') != projection(obs['clusterQueue']):
                    row.update(phase='Conflict', reason='ExternalEdit'); conflicts = True
                if row['phase'] == 'Conflict': conflicts = True
            if conflicts:
                state = self.store.save_state(state)
                return self._render(targets, policies, state, observations)
            for uid, p in policies.items():
                row, obs = state['tenants'][uid], observations[uid]
                forecast = row.get('forecast')
                if not forecast: continue
                reason = None
                target = row['target']
                if (forecast['policyDigest'] != p['digest'] or forecast['targetsDigest'] !=
                        digest({k: v for k, v in targets.items() if not k.startswith('_')})):
                    reason = 'PolicyChanged'
                elif not self._stored_forecast_valid(row, p): reason = 'InvalidStoredForecast'
                elif now - forecast['issued'] > p['maxAgeSeconds'] or now >= forecast['end']:
                    reason = 'StaleForecast'
                elif any(not item['guards']['ok'] for item in observations.values()):
                    reason = 'PoolObservationUnknown'
                elif any(r.get('intent') for r in state['tenants'].values()): reason = 'ApplyUnknown'
                elif target == obs['quota']:
                    # An unchanged target is observation, not a newly verified apply.
                    continue
                elif now < forecast['start'] - p['leadSeconds']: reason = 'LeadTime'
                elif now - row.get('lastChange', -float('inf')) < p['cooldownSeconds']: reason = 'Cooldown'
                elif target < obs['quota']:
                    low = row.get('low', {})
                    if (low.get('count', 0) < 2 or low.get('lastSample', now) - low.get('start', now) < p['downHoldSeconds'] or
                            now - low.get('lastSample', 0) > p['maxSampleGapSeconds']): reason = 'DownHold'
                if not reason and sum(o['quota'] for o in observations.values()) - obs['quota'] + target > 2:
                    reason = 'Capacity'
                if reason:
                    row.update(phase='Deferred', reason=reason)
                    continue
                # Reload every authority immediately before preparing an intent.
                fresh_t, fresh_p, fresh_s, fresh_o = self._load()
                if (fresh_s['_resourceVersion'] != state['_resourceVersion'] or
                        fresh_p[uid]['digest'] != p['digest'] or fresh_t != targets or
                        any(not o['guards']['ok'] or projection(o['clusterQueue']) !=
                            projection(observations[u]['clusterQueue']) for u, o in fresh_o.items())):
                    row.update(phase='Deferred', reason='ConcurrentChange'); continue
                obs = fresh_o[uid]
                desired = copy.deepcopy(obs['clusterQueue'])
                gpu_resource(desired, p)['nominalQuota'] = str(target)
                row['intent'] = {'tenantUID': uid, 'clusterQueueUID': p['clusterQueue']['uid'],
                    'previous': obs['quota'], 'target': target, 'previousRV': obs['resourceVersion'],
                    'before': projection(obs['clusterQueue']), 'after': projection(desired),
                    'policyRevision': p['revision'], 'eventId': forecast['event']['eventId'], 'at': stamp(now)}
                row.update(phase='Applying', reason='IntentPersisted')
                state = self.store.save_state(state)
                row = state['tenants'][uid]
                try:
                    # Persisting an intent can take time; re-read authorities before the CQ write.
                    last_t, last_p, last_s, last_o = self._load()
                    if (not self.enabled or last_t != targets or last_p[uid]['digest'] != p['digest'] or
                            last_s['_resourceVersion'] != state['_resourceVersion'] or
                            any(not o['guards']['ok'] or projection(o['clusterQueue']) != projection(observations[u]['clusterQueue'])
                                for u, o in last_o.items())):
                        row.pop('intent', None)
                        row.update(phase='Deferred', reason='ConcurrentChange')
                        state = self.store.save_state(state)
                        return self._render(last_t, last_p, state, last_o)
                    self.kube.call(CQ + '/' + p['clusterQueue']['name'], 'PUT', desired)
                    observations = {u: observe_binding(self.kube, v, targets) for u, v in policies.items()}
                    current = observations[uid]
                    if (not current['guards']['ok'] or projection(current['clusterQueue']) != row['intent']['after'] or
                            any(not o['guards']['ok'] or (u != uid and projection(o['clusterQueue']) !=
                                state['tenants'][u]['baseline']) for u, o in observations.items())):
                        raise Error('Apply readback is uncertain', 503, 'ApplyUnknown')
                    self._finish(row, current, now)
                    state = self.store.save_state(state)
                except Exception as exc:
                    # Reload durable intent: _finish may have removed the in-memory copy
                    # before its confirmation save failed.
                    try:
                        state = self.store.load_state(); row = state['tenants'][uid]
                        row.update(phase='Conflict' if getattr(exc, 'status', None) == 409 else 'ApplyUnknown',
                                   reason='ConcurrentChange' if getattr(exc, 'status', None) == 409 else 'ApplyUnknown')
                        state = self.store.save_state(state)
                    except Exception:
                        return self._unavailable('ApplyOutcomeOrStateUnknown')
                return self._render(targets, policies, state, observations)
            state = self.store.save_state(state)
            return self._render(targets, policies, state, observations)
        except (Error, ValueError, KeyError, TypeError) as exc:
            return self._unavailable(getattr(exc, 'code', None) or 'Unavailable')
