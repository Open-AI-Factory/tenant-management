import hashlib,json,os,re
from ipaddress import IPv4Address, IPv6Address
from pathlib import Path
from urllib.parse import urlsplit
from admission import guards
ROOT=Path(__file__).parent
DEFAULT_PUBLIC_ORIGIN='https://tenant-admin.example.invalid'
DEFAULT_GRAFANA_ORIGIN='https://grafana.example.invalid'


def https_origin(name, default):
    """Accept only an HTTPS origin, safe for both runtime and static UI links."""
    value=os.environ.get(name,default)
    try:
        if not value or any(c.isspace() or ord(c)<32 for c in value) or any(c in value for c in ('?', '#', '\\')):
            raise ValueError()
        parsed=urlsplit(value)
        host=parsed.hostname or ''
        if (parsed.scheme!='https' or not host or parsed.username is not None or parsed.password is not None
                or parsed.path not in ('','/') or parsed.netloc.endswith(':')
                or (parsed.port is not None and not 1<=parsed.port<=65535)):
            raise ValueError()
        if ':' in host:
            if not re.fullmatch(r'\[[0-9A-Fa-f:.]+\](?::[0-9]+)?',parsed.netloc): raise ValueError()
            host='['+IPv6Address(host).compressed+']'
        else:
            if (not re.fullmatch(r'[A-Za-z0-9.-]+(?::[0-9]+)?',parsed.netloc) or len(host)>253
                    or not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?',label)
                               for label in host.split('.'))):
                raise ValueError()
            # Exclude shorthand/hex IPv4 spellings that browsers rewrite.
            if re.fullmatch(r'(?:[0-9]+|0[xX][0-9A-Fa-f]+)',host.rsplit('.',1)[-1]):
                host=str(IPv4Address(host))
    except ValueError:
        raise ValueError(name+' must be an HTTPS origin without credentials, path, query, or fragment') from None
    # Browsers omit the default HTTPS port and normalize host spelling in Origin.
    return 'https://'+host+(':'+str(parsed.port) if parsed.port not in (None,443) else '')


public_origin=https_origin('PUBLIC_ORIGIN',DEFAULT_PUBLIC_ORIGIN)
grafana_origin=https_origin('GRAFANA_URL',DEFAULT_GRAFANA_ORIGIN)
grafana_instance=os.environ.get('GRAFANA_INSTANCE_ID','tenant-admin-grafana')
tls_secret=os.environ.get('TLS_SECRET_NAME','tenant-admin-tls')
node_name=os.environ.get('TENANT_NODE_NAME','')
N='tenant-admin';labels={'app.kubernetes.io/name':N,'app.kubernetes.io/part-of':'ai-factory'}
def obj(api,kind,name,spec=None,**kw):
    d={'apiVersion':api,'kind':kind,'metadata':{'name':name,'labels':labels.copy()}}
    if kind not in ['ClusterRole','ClusterRoleBinding','CustomResourceDefinition']:d['metadata']['namespace']='monitoring'
    if spec is not None:d['spec']=spec
    d.update(kw);return d
from policy_schema import policy_schema
schema=policy_schema()
crd=obj('apiextensions.k8s.io/v1','CustomResourceDefinition','tenantresourcepolicies.tenancy.gpu.platform.io',{'group':'tenancy.gpu.platform.io','scope':'Cluster','names':{'plural':'tenantresourcepolicies','singular':'tenantresourcepolicy','kind':'TenantResourcePolicy','shortNames':['trp']},'versions':[{'name':'v1alpha1','served':True,'storage':True,'schema':{'openAPIV3Schema':schema},'subresources':{'status':{}},'additionalPrinterColumns':[{'name':'Tenant','type':'string','jsonPath':'.spec.tenantName'},{'name':'Phase','type':'string','jsonPath':'.status.phase'},{'name':'GPU','type':'integer','jsonPath':'.spec.resources.gpu.maximum'}]}]})
runtime_files=['server.py','workloads.py','index.html','style.css','app.js','runtime.py','tenant_api.py','http_app.py','errors.py','access.py','executions.py','context.py','kubeutil.py','namespaces.py','network_policy.py','admission.py','tenant_inventory.py','tenant_usage.py','quota_demo.py','quota_simulator.py','forecast_quota.py','dummy_profiler.py','quota_demo_config.json','quota_simulator.html','quota_simulator.js','quota_simulator.css']
runtime_files.extend(['tenant_budget_policy.py','kueue_binding.py','kueue_quota_store.py','kueue_quota_control.py',
 'kueue_quota_http.py','kueue_quota.html','kueue_quota.js','kueue_quota.css','dummy_kueue_profiler.py'])
code={n:(ROOT/n).read_text(encoding='utf-8') for n in runtime_files}
# Render the same validated Grafana origin into every shipped navigation link.
for name in ('index.html','app.js'):
    code[name]=code[name].replace(DEFAULT_GRAFANA_ORIGIN,grafana_origin)
digest=hashlib.sha256(json.dumps(code,sort_keys=True).encode()).hexdigest()
items=[crd,obj('v1','ServiceAccount',N),obj('rbac.authorization.k8s.io/v1','ClusterRole',N,rules=[
 {'apiGroups':['tenancy.gpu.platform.io'],'resources':['tenantresourcepolicies','tenantresourcepolicies/status'],'verbs':['get','list','create','update']},
 {'apiGroups':['capsule.clastix.io'],'resources':['tenants'],'verbs':['get','list','create','update']},
 {'apiGroups':[''],'resources':['namespaces'],'verbs':['get','list','create']},
 {'apiGroups':[''],'resources':['pods'],'verbs':['get','list','create','delete']},
 {'apiGroups':[''],'resources':['pods/log'],'verbs':['get']},
 {'apiGroups':[''],'resources':['events'],'verbs':['list']},
 {'apiGroups':['admissionregistration.k8s.io'],'resources':['validatingadmissionpolicybindings'],'verbs':['get','create','update']},
 {'apiGroups':[''],'resources':['resourcequotas'],'verbs':['get','create','update']},
 {'apiGroups':['networking.k8s.io'],'resources':['networkpolicies'],'verbs':['get','create','update']},
 {'apiGroups':['scheduling.run.ai'],'resources':['queues'],'verbs':['get','create','update']},
 {'apiGroups':['resource.k8s.io'],'resources':['resourceslices','resourceclaims'],'verbs':['list']},
 {'apiGroups':['resource.k8s.io'],'resources':['resourceclaimtemplates'],'verbs':['get','create']}]),
 obj('rbac.authorization.k8s.io/v1','ClusterRoleBinding',N,roleRef={'apiGroup':'rbac.authorization.k8s.io','kind':'ClusterRole','name':N},subjects=[{'kind':'ServiceAccount','name':N,'namespace':'monitoring'}]),
 obj('v1','ConfigMap',N,data=code),
 obj('apps/v1','Deployment',N,{'replicas':1,'strategy':{'type':'Recreate'},'selector':{'matchLabels':{'app.kubernetes.io/name':N}},'template':{'metadata':{'labels':labels,'annotations':{'checksum/code':digest}},'spec':{'serviceAccountName':N,'securityContext':{'runAsNonRoot':True,'runAsUser':65532,'runAsGroup':65532,'fsGroup':65532,'seccompProfile':{'type':'RuntimeDefault'}},'containers':[{'name':'console','image':'docker.io/library/python@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254','command':['python3','-B','/app/server.py'],'env':[{'name':'PUBLIC_ORIGIN','value':public_origin},{'name':'GRAFANA_URL','value':grafana_origin}],'ports':[{'name':'https','containerPort':8443}],'resources':{'requests':{'cpu':'25m','memory':'64Mi'},'limits':{'cpu':'250m','memory':'192Mi'}},'securityContext':{'allowPrivilegeEscalation':False,'readOnlyRootFilesystem':True,'capabilities':{'drop':['ALL']}},'readinessProbe':{'httpGet':{'scheme':'HTTPS','path':'/healthz','port':'https'},'initialDelaySeconds':5,'periodSeconds':10},'livenessProbe':{'tcpSocket':{'port':'https'},'initialDelaySeconds':15,'periodSeconds':20},'volumeMounts':[{'name':'code','mountPath':'/app','readOnly':True},{'name':'tls','mountPath':'/tls','readOnly':True}]}],'volumes':[{'name':'code','configMap':{'name':N}},{'name':'tls','secret':{'secretName':tls_secret,'defaultMode':288,'items':[{'key':'tls.crt','path':'tls.crt'},{'key':'tls.key','path':'tls.key'}]}}]}}}),
 obj('v1','Service',N,{'type':'NodePort','selector':{'app.kubernetes.io/name':N},'ports':[{'name':'https','port':8443,'targetPort':'https','nodePort':30443}]})]
items=items[:1]+guards()+items[1:]
items.extend([
 obj('rbac.authorization.k8s.io/v1','Role',N+'-records',rules=[{'apiGroups':[''],'resources':['configmaps'],'verbs':['get','list','create','update']}]),
 obj('rbac.authorization.k8s.io/v1','RoleBinding',N+'-records',roleRef={'apiGroup':'rbac.authorization.k8s.io','kind':'Role','name':N+'-records'},subjects=[{'kind':'ServiceAccount','name':N,'namespace':'monitoring'}])])
deployment=next(x for x in items if x['kind']=='Deployment')
if node_name:
    deployment['spec']['template']['spec']['nodeSelector']={'kubernetes.io/hostname':node_name}
container=deployment['spec']['template']['spec']['containers'][0]
container['command']=['python3','-B','/app/runtime.py']
container['env'].extend([{'name':'GRAFANA_INSTANCE_ID','value':grafana_instance},
 {'name':'TENANT_RECONCILE_ENABLED','value':'false'},{'name':'TENANT_MUTATIONS_ENABLED','value':'false'},
 {'name':'TENANT_DNS_PEERS','value':'[]'}])
container['env'].append({'name':'KUEUE_QUOTA_ENABLED','value':'false'})
from kueue_quota_manifest import budget_rbac, readonly_guard
items.extend(budget_rbac()+readonly_guard())
(ROOT/'resources.json').write_text(json.dumps({'apiVersion':'v1','kind':'List','items':items},ensure_ascii=False,indent=2),encoding='utf-8')
print('Built',len(items),'objects')
