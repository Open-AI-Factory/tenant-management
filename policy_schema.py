"""Backward-compatible v1alpha1 policy schema."""

def policy_schema():
    budget={'type':'object','required':['guaranteed','maximum'],'properties':{'guaranteed':{'type':'integer','minimum':1},'maximum':{'type':'integer','minimum':1}},'x-kubernetes-validations':[{'rule':'self.guaranteed == self.maximum','message':'Borrowing is disabled in configuration-test mode'}]}
    schema={'type':'object','properties':{'spec':{'type':'object','required':['tenantName','namespaceName','pool','resources','mode','displayName','contact'],'properties':{
     'tenantName':{'type':'string','pattern':'^[a-z][a-z0-9-]{1,28}[a-z0-9]$'},'namespaceName':{'type':'string'},'displayName':{'type':'string','maxLength':80},'contact':{'type':'string','maxLength':120},'mode':{'type':'string','enum':['configuration-test','gpu-test']},
     'pool':{'type':'object','required':['key','model','mode','capacity','nodes'],'properties':{'key':{'type':'string'},'model':{'type':'string'},'mode':{'type':'string','enum':['whole','mig']},'profile':{'type':'string'},'capacity':{'type':'integer','minimum':1},'nodes':{'type':'array','items':{'type':'string'}}}},
     'resources':{'type':'object','required':['gpu','cpu','memory'],'properties':{x:budget for x in ['gpu','cpu','memory']}}},'x-kubernetes-validations':[{'rule':"self.namespaceName == 'tenant-' + self.tenantName",'message':'Namespace must use tenant prefix'},{'rule':'self.tenantName == oldSelf.tenantName','message':'Tenant ID is immutable'},{'rule':'self.pool.key == oldSelf.pool.key','message':'Pool is immutable'}]},'status':{'type':'object','x-kubernetes-preserve-unknown-fields':True}}}
    
    schema['properties']['spec']['properties']['namespaceName']['maxLength'] = 63
    schema['properties']['spec']['properties']['tenantName']['maxLength'] = 30
    schema['properties']['spec']['properties']['additionalNamespaceNames'] = {'type':'array','maxItems':2,'x-kubernetes-list-type':'set','items':{'type':'string','maxLength':63}}
    schema['properties']['spec']['x-kubernetes-validations'].append({'rule': "!has(self.additionalNamespaceNames) || self.additionalNamespaceNames.all(n, n.matches('^' + self.namespaceName + '-[a-z0-9]{1,10}$'))", 'message':'Additional namespaces must use the tenant prefix and a 1-10 character purpose'})
    schema['properties']['status'] = {'type':'object','x-kubernetes-preserve-unknown-fields':True,'properties': {
        **{k:{'type':'string'} for k in ['phase','message','tenantUID','namespaceUID','queue','queueUID','templateUID']},
        'workloadsEnabled':{'type':'boolean'},'observedGeneration':{'type':'integer'},
        'namespaceBindings': {'type':'object','additionalProperties': {'type':'object','required':['uid','templateUID','phase'],'properties': {k:{'type':'string'} for k in ['uid','templateUID','phase']}}}}}
    return schema
