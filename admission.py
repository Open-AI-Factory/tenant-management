from workloads import IMAGE,ACTOR,MANAGED,OWNER

def guards():
    labels={'app.kubernetes.io/name':'tenant-admin','app.kubernetes.io/part-of':'ai-factory'}
    def policy(name,rules,validations,param=False):
        p={'apiVersion':'admissionregistration.k8s.io/v1','kind':'ValidatingAdmissionPolicy','metadata':{'name':name,'labels':labels},'spec':{'failurePolicy':'Fail','matchConstraints':{'resourceRules':rules},'validations':[{'expression':expr,'message':msg} for expr,msg in validations]}}
        if param:p['spec']['paramKind']={'apiVersion':'tenancy.gpu.platform.io/v1alpha1','kind':'TenantResourcePolicy'}
        return p
    checks=[
      ("request.operation != 'CREATE' || request.userInfo.username == '"+ACTOR+"'",'Submit tenant GPU jobs through the authenticated tenant console'),
      ("params != null && params.spec.mode == 'gpu-test' && has(params.status) && params.status.phase == 'Ready' && params.status.workloadsEnabled && params.status.observedGeneration == params.metadata.generation",'Tenant policy is not ready for GPU execution'),
      ("has(params.status.namespaceBindings) && namespaceObject.metadata.name in params.status.namespaceBindings && params.status.namespaceBindings[namespaceObject.metadata.name].phase == 'Ready' && dyn(namespaceObject).metadata.uid == params.status.namespaceBindings[namespaceObject.metadata.name].uid && namespaceObject.metadata.labels['"+OWNER+"'] == params.metadata.uid && dyn(namespaceObject).metadata.ownerReferences.exists(o, o.kind == 'Tenant' && o.name == params.spec.tenantName && o.uid == params.status.tenantUID)",'Tenant namespace identity mismatch'),
      ("has(object.metadata.labels) && '"+OWNER+"' in object.metadata.labels && object.metadata.labels['"+OWNER+"'] == params.metadata.uid && 'kai.scheduler/queue' in object.metadata.labels && object.metadata.labels['kai.scheduler/queue'] == params.status.queue",'Foreign or missing tenant queue'),
      ("object.spec.schedulerName == 'kai-scheduler' && (request.operation != 'CREATE' || !has(object.spec.nodeName) || object.spec.nodeName == '')",'KAI scheduler is required; direct node binding is forbidden'),
      ("has(object.spec.resourceClaims) && size(object.spec.resourceClaims) == 1 && object.spec.resourceClaims[0].name == 'gpu' && has(object.spec.resourceClaims[0].resourceClaimTemplateName) && object.spec.resourceClaims[0].resourceClaimTemplateName == 'tenant-gpu' && !has(object.spec.resourceClaims[0].resourceClaimName)",'Use exactly one approved GPU claim template'),
      ("size(object.spec.containers) == 1 && object.spec.containers[0].image == '"+IMAGE+"' && has(object.spec.containers[0].resources.claims) && size(object.spec.containers[0].resources.claims) == 1 && object.spec.containers[0].resources.claims[0].name == 'gpu'",'Use the approved single-container GPU test workload'),
      ("!has(object.spec.initContainers) && !has(object.spec.ephemeralContainers) && !has(object.spec.volumes) && (!has(object.spec.hostNetwork) || !object.spec.hostNetwork) && (!has(object.spec.hostPID) || !object.spec.hostPID) && (!has(object.spec.hostIPC) || !object.spec.hostIPC)",'Host access and extra containers or volumes are forbidden'),
      ("has(object.spec.automountServiceAccountToken) && !object.spec.automountServiceAccountToken && !has(object.spec.runtimeClassName)",'Service account token and runtime override are forbidden'),
      ("has(object.spec.affinity) && has(object.spec.affinity.nodeAffinity) && has(object.spec.affinity.nodeAffinity.requiredDuringSchedulingIgnoredDuringExecution) && size(object.spec.affinity.nodeAffinity.requiredDuringSchedulingIgnoredDuringExecution.nodeSelectorTerms) == 1 && size(object.spec.affinity.nodeAffinity.requiredDuringSchedulingIgnoredDuringExecution.nodeSelectorTerms[0].matchExpressions) == 1 && object.spec.affinity.nodeAffinity.requiredDuringSchedulingIgnoredDuringExecution.nodeSelectorTerms[0].matchExpressions[0].key == 'kubernetes.io/hostname' && object.spec.affinity.nodeAffinity.requiredDuringSchedulingIgnoredDuringExecution.nodeSelectorTerms[0].matchExpressions[0].operator == 'In' && object.spec.affinity.nodeAffinity.requiredDuringSchedulingIgnoredDuringExecution.nodeSelectorTerms[0].matchExpressions[0].values == params.spec.pool.nodes",'Requested nodes must match the tenant resource pool'),
      ("has(object.spec.containers[0].resources.requests) && size(object.spec.containers[0].resources.requests) == 2 && size(object.spec.containers[0].resources.limits) == 2 && object.spec.containers[0].resources.requests['cpu'] == '500m' && object.spec.containers[0].resources.requests['memory'] == '1Gi' && object.spec.containers[0].resources.limits['cpu'] == '1' && object.spec.containers[0].resources.limits['memory'] == '1Gi'",'GPU test CPU/memory accounting cannot be changed'),
    ]
    checks[-1]=(checks[-1][0].replace('object.spec.containers[0].resources','dyn(object.spec.containers[0].resources)'),checks[-1][1])
    pod=policy('tenant-workload-guard',[{'apiGroups':[''],'apiVersions':['v1'],'operations':['CREATE','UPDATE'],'resources':['pods','pods/ephemeralcontainers']}],checks,True)
    claim=policy('tenant-claim-guard',[{'apiGroups':['resource.k8s.io'],'apiVersions':['v1'],'operations':['CREATE','UPDATE'],'resources':['resourceclaimtemplates','resourceclaims']}],[
        ("request.resource.resource == 'resourceclaimtemplates' ? (request.userInfo.username == '"+ACTOR+"' || (request.operation == 'UPDATE' && object.spec == oldObject.spec)) : (request.operation == 'UPDATE' ? object.spec == oldObject.spec : request.userInfo.username in ['system:kube-controller-manager','system:serviceaccount:kube-system:resource-claim-controller'])",'GPU templates are console-managed; claims must be generated by the Kubernetes resource-claim controller')])
    b={'apiVersion':'admissionregistration.k8s.io/v1','kind':'ValidatingAdmissionPolicyBinding','metadata':{'name':'tenant-claim-guard','labels':labels},'spec':{'policyName':'tenant-claim-guard','validationActions':['Deny'],'matchResources':{'namespaceSelector':{'matchLabels':{MANAGED:'true'}}}}}
    scope=policy('tenant-queue-scope',[{'apiGroups':[''],'apiVersions':['v1'],'operations':['CREATE','UPDATE'],'resources':['pods']}],[checks[1],checks[2],checks[3]],True)
    return [pod,claim,b,scope]


def queue_scope_binding(p,queue):
    return {'apiVersion':'admissionregistration.k8s.io/v1','kind':'ValidatingAdmissionPolicyBinding',
        'metadata':{'name':'tenant-queue-scope-'+p['metadata']['name'],'labels':{MANAGED:'true',OWNER:p['metadata']['uid']}},
        'spec':{'policyName':'tenant-queue-scope','validationActions':['Deny'],
                'paramRef':{'name':p['metadata']['name'],'parameterNotFoundAction':'Deny'},
                'matchResources':{'objectSelector':{'matchLabels':{'kai.scheduler/queue':queue}}}}}
