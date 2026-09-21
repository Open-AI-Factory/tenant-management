"""Durable RV-conditional state; operator policy/targets are read-only."""
import copy
import json
from errors import Error
from kubeutil import read
from tenant_budget_policy import strict_json, check_json


class QuotaStore:
    def __init__(self, kube, namespace: str = 'monitoring'):
        self.kube = kube
        self.base = '/api/v1/namespaces/' + namespace + '/configmaps/tenant-admin-budget-'

    def _document(self, suffix):
        cm = read(self.kube, self.base + suffix)
        try:
            meta = cm['metadata']
            if not meta.get('uid') or not meta.get('resourceVersion') or meta.get('deletionTimestamp'):
                raise ValueError('ConfigMap identity unavailable')
            doc = strict_json(cm['data']['document'], 512 * 1024)
            self._validate(doc, suffix)
            doc['_resourceVersion'] = meta['resourceVersion']
            doc['_configMapUID'] = meta['uid']
            return doc
        except (KeyError, ValueError, TypeError, UnicodeError, RecursionError) as exc:
            raise Error('Budget configuration/state is invalid', 503, 'Unavailable') from exc

    @staticmethod
    def _validate(doc, suffix):
        check_json(doc)
        if not isinstance(doc, dict) or type(doc.get('schemaVersion')) is not int or doc['schemaVersion'] != 1:
            raise ValueError('Unsupported document schema')
        rows = doc.get('tenants' if suffix == 'state' else 'entries')
        if not isinstance(rows, dict) or len(rows) > 2:
            raise ValueError('Invalid tenant map')
        if suffix == 'state':
            for row in rows.values():
                if not isinstance(row, dict): raise ValueError('Invalid tenant state')
                for key in ('history', 'events'):
                    if not isinstance(row.get(key, []), list) or len(row.get(key, [])) > 100:
                        raise ValueError('History bound exceeded')
                if not isinstance(row.get('ledger', {}), dict) or len(row.get('ledger', {})) > 1000:
                    raise ValueError('Ledger bound exceeded')

    def load_targets(self) -> dict:
        return self._document('targets')

    def load_policy(self) -> dict:
        return self._document('policy')

    def load_state(self) -> dict:
        return self._document('state')

    def save_state(self, state: dict) -> dict:
        self._validate(state, 'state')
        if not state.get('_resourceVersion') or not state.get('_configMapUID'):
            raise Error('State preconditions required', 409, 'Conflict')
        doc = {key: copy.deepcopy(value) for key, value in state.items() if not key.startswith('_')}
        try:
            raw = json.dumps(doc, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
            if len(raw.encode('utf-8')) > 512 * 1024: raise ValueError('State too large')
        except (ValueError, UnicodeError) as exc:
            raise Error('State is not persistable', 413, 'StateCapacity') from exc
        cm = read(self.kube, self.base + 'state')
        if (cm['metadata'].get('uid') != state['_configMapUID'] or
                cm['metadata'].get('resourceVersion') != state['_resourceVersion'] or
                cm['metadata'].get('deletionTimestamp')):
            raise Error('Budget state changed concurrently', 409, 'Conflict')
        cm.setdefault('data', {})['document'] = raw
        self.kube.call(self.base + 'state', 'PUT', cm)
        restored = self.load_state()
        if {key: value for key, value in restored.items() if not key.startswith('_')} != doc:
            raise Error('Budget state readback differs', 409, 'Conflict')
        return restored
