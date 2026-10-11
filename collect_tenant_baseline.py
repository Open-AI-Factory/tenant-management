"""Read-only, credential-free evidence collection for the tenant console."""
import datetime
import hashlib
import json
import urllib.parse

PATHS = {
    'tenants': '/apis/capsule.clastix.io/v1beta2/tenants',
    'policies': '/apis/tenancy.gpu.platform.io/v1alpha1/tenantresourcepolicies',
    'namespaces': '/api/v1/namespaces',
    'queues': '/apis/scheduling.run.ai/v2/queues',
    'templates': '/apis/resource.k8s.io/v1/resourceclaimtemplates',
    'claims': '/apis/resource.k8s.io/v1/resourceclaims',
    'admission': '/apis/admissionregistration.k8s.io/v1/validatingadmissionpolicies',
    'bindings': '/apis/admissionregistration.k8s.io/v1/validatingadmissionpolicybindings',
    'resourcePaths': '/apis/resource.k8s.io/v1/resourceslices',
    'deviceClasses': '/apis/resource.k8s.io/v1/deviceclasses',
    'nodes': '/api/v1/nodes',
    'protectedWorkloads': '/api/v1/pods',
    'deployments': '/apis/apps/v1/deployments',
    'services': '/api/v1/services',
    'crds': '/apis/apiextensions.k8s.io/v1/customresourcedefinitions',
}


def metadata(obj):
    m = obj.get('metadata', {})
    return {k: m[k] for k in ('name', 'namespace', 'uid', 'resourceVersion',
            'generation', 'labels', 'ownerReferences', 'deletionTimestamp') if k in m}


def project(kind, obj):
    value = {'metadata': metadata(obj)}
    if kind == 'protectedWorkloads':
        value['status'] = {k: obj.get('status', {}).get(k) for k in
                           ('phase', 'conditions', 'containerStatuses')}
        value['spec'] = {k: obj.get('spec', {}).get(k) for k in
                         ('nodeName', 'schedulerName', 'resourceClaims')}
    elif kind == 'deployments':
        containers = obj.get('spec', {}).get('template', {}).get('spec', {}).get('containers', [])
        value['images'] = [{'name': c['name'], 'image': c.get('image')} for c in containers]
        value['status'] = obj.get('status', {})
    else:
        value.update({k: obj[k] for k in ('spec', 'status') if k in obj})
    return value


def collect_baseline(kube, grafana_reader):
    out = {'collectedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(),
           'versions': {}, 'errors': [], 'authIdentityShape': {}}
    try:
        out['versions']['kubernetes'] = kube.call('/version')['gitVersion']
    except Exception as exc:
        out['errors'].append({'section': 'version', 'error': type(exc).__name__})
    for kind, path in PATHS.items():
        try:
            items, page, seen = [], path, set()
            while True:
                response = kube.call(page)
                if not isinstance(response.get('items'), list):
                    raise ValueError('incomplete list')
                items.extend(project(kind, item) for item in response['items'])
                cursor = response.get('metadata', {}).get('continue')
                if not cursor:
                    break
                if cursor in seen:
                    raise ValueError('repeated list cursor')
                seen.add(cursor)
                page = path + '?' + urllib.parse.urlencode({'continue': cursor})
            out[kind] = items
        except Exception as exc:
            out[kind] = None
            out['errors'].append({'section': kind, 'error': type(exc).__name__})
    try:
        user = grafana_reader()
        out['authIdentityShape'] = {key: type(user[key]).__name__ for key in
                                   ('id', 'uid', 'login', 'isGrafanaAdmin') if key in user}
    except Exception as exc:
        out['errors'].append({'section': 'grafana', 'error': type(exc).__name__})
    return out


def code_hashes(config_map):
    return {name: hashlib.sha256(text.encode('utf-8')).hexdigest()
            for name, text in config_map.get('data', {}).items()}
