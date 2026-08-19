"""Tenant usage behavior at the real API boundary, with Kubernetes fixtures."""
import copy
import json
import unittest

from executions import ACCESS
from kubeutil import BASE, TENANTS, OWNER, MANAGED
from tenant_api import TenantAPI
from test_support import seeded
from workloads import pod_manifest


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.k, self.p = seeded()
        self.api = TenantAPI(self.k, b'usage-test')
        self.user = dict(providerInstance='g1', subjectKind='uid', subjectId='alice',
                         login='A', platformAdmin=False)
        self.k.objects[ACCESS] = {'metadata': {'name': 'tenant-access-v1', 'resourceVersion': '1'},
            'data': {'bindings.json': json.dumps([dict(providerInstance='g1', subjectKind='uid',
                subjectId='alice', tenantUID='t-a', role='viewer')])}}
        self.ns = 'tenant-team-a'

    def namespace(self, name='tenant-team-a-dev'):
        ns = copy.deepcopy(self.k.objects['/api/v1/namespaces/' + self.ns])
        ns['metadata'].update(name=name, uid='uid-' + name)
        self.k.objects['/api/v1/namespaces/' + name] = ns
        p = self.k.objects[BASE + '/team-a']
        p['spec'].setdefault('additionalNamespaceNames', []).append(name)
        p['status']['namespaceBindings'][name] = dict(uid='uid-' + name,
                                                     templateUID='tpl-' + name, phase='Ready')
        template_path = '/apis/resource.k8s.io/v1/namespaces/'
        template = copy.deepcopy(self.k.objects[template_path + self.ns + '/resourceclaimtemplates/tenant-gpu'])
        template['metadata']['uid'] = 'tpl-' + name
        self.k.objects[template_path + name + '/resourceclaimtemplates/tenant-gpu'] = template
        return name

    def pod(self, name, namespace=None, phase='Pending', node=None, scheduled=None, deleting=False):
        ns = namespace or self.ns
        policy = copy.deepcopy(self.p); policy['spec']['namespaceName'] = ns
        pod = pod_manifest(policy, 30, name)
        pod['metadata']['uid'] = 'uid-' + name
        pod['status'] = {'phase': phase}
        if node: pod['spec']['nodeName'] = node
        if scheduled: pod['status']['conditions'] = [{'type': 'PodScheduled', 'status': scheduled}]
        if deleting: pod['metadata']['deletionTimestamp'] = '2026-10-06T00:00:00Z'
        self.k.objects['/api/v1/namespaces/' + ns + '/pods/' + name] = pod
        return pod

    def claim(self, name, devices=(), namespace=None, driver='gpu.nvidia.com', pool='node1'):
        ns = namespace or self.ns
        claim = {'metadata': {'name': name, 'namespace': ns, 'uid': 'uid-' + name},
            'spec': {'devices': {'requests': [{'name': 'gpu', 'exactly': {
                'deviceClassName': 'mig.nvidia.com', 'allocationMode': 'ExactCount', 'count': 1}}]}},
            'status': {}}
        if devices:
            claim['status']['allocation'] = {'devices': {'results': [
                {'request': 'gpu', 'driver': driver, 'pool': pool, 'device': device} for device in devices]}}
        self.k.objects['/apis/resource.k8s.io/v1/namespaces/' + ns + '/resourceclaims/' + name] = claim
        return claim

    def usage(self):
        status, state = self.api.dispatch(self.user, 'GET', '/api/state')
        self.assertEqual(status, 200)
        self.assertIn('usage', state['tenants'][0], 'tenant state must expose authoritative usage')
        return state['tenants'][0]['usage']

    def assert_counts(self, allocated, waiting):
        usage = self.usage()
        self.assertEqual(usage['status'], 'Observed')
        self.assertEqual((usage['allocatedDevices'], usage['waitingJobs']), (allocated, waiting))
        self.assertTrue(usage['observedAt'])
        return usage

    def assert_unknown(self):
        usage = self.usage()
        self.assertEqual(usage['status'], 'Unknown')
        self.assertIsNone(usage['allocatedDevices'])
        self.assertIsNone(usage['waitingJobs'])
        self.assertIsNone(usage['observedAt'])
        self.assertTrue(usage['reason'])

    def test_empty_complete_observation_is_zero_and_read_only(self):
        self.assert_counts(0, 0)
        self.assertTrue(all(method == 'GET' for method, _ in self.k.calls))

    def test_two_namespaces_sum_allocations_and_unscheduled_jobs(self):
        second = self.namespace()
        self.claim('first', ['mig1'])
        self.claim('second', ['mig2'], namespace=second)
        self.pod('waiting-a'); self.pod('waiting-b', namespace=second)
        self.assert_counts(2, 2)

    def test_running_is_not_allocation_and_scheduled_pending_is_not_waiting(self):
        self.pod('running', phase='Running', node='node1')
        self.pod('starting', node='node1', scheduled='True')
        self.pod('binding', scheduled='True')
        self.pod('over-limit', scheduled='False')
        self.assert_counts(0, 1)

    def test_device_identity_deduplicates_and_distinguishes_driver_pool(self):
        second = self.namespace()
        self.claim('one', ['mig1', 'mig1'])
        self.claim('same-device', ['mig1'], namespace=second)
        self.claim('other-pool', ['mig1'], pool='node2')
        self.claim('not-gpu', ['mig1'], driver='network.example')
        self.assert_counts(2, 0)

    def test_terminal_or_deleted_pod_does_not_hide_unreleased_claim(self):
        self.pod('finished', phase='Succeeded')
        self.pod('failed', phase='Failed')
        self.pod('cancelling', deleting=True)
        claim = self.claim('retained', ['mig1'])
        claim['metadata']['deletionTimestamp'] = '2026-10-06T00:00:00Z'
        self.assert_counts(1, 0)
        claim['status'].pop('allocation')
        self.assert_counts(0, 0)

    def test_claim_deletion_releases_observed_allocation(self):
        self.claim('retained', ['mig1'])
        self.assert_counts(1, 0)
        del self.k.objects['/apis/resource.k8s.io/v1/namespaces/' + self.ns + '/resourceclaims/retained']
        self.assert_counts(0, 0)

    def test_non_gpu_and_foreign_policy_pods_are_not_console_waiting_jobs(self):
        cpu = self.pod('cpu'); cpu['spec'].pop('resourceClaims')
        foreign = self.pod('foreign'); foreign['metadata']['labels'][OWNER] = 'another-policy'
        self.assert_counts(0, 0)

    def test_policy_missing_is_unknown_not_zero(self):
        del self.k.objects[BASE + '/team-a']
        self.assert_unknown()

    def test_duplicate_policies_report_conflict_instead_of_missing_policy(self):
        duplicate = copy.deepcopy(self.p)
        duplicate['metadata'].update(name='duplicate', uid='p-duplicate')
        self.k.objects[BASE + '/duplicate'] = duplicate
        self.assert_unknown()
        self.assertEqual(self.usage()['reason'], 'IdentityMismatch')

    def test_disabling_submission_keeps_existing_usage_observable(self):
        self.k.objects[BASE + '/team-a']['spec']['mode'] = 'configuration-test'
        self.claim('active', ['mig1']); self.pod('waiting')
        self.assert_counts(1, 1)

    def test_claim_or_pod_query_failure_is_unknown_not_partial_total(self):
        second = self.namespace()
        self.claim('known', ['mig1'])
        for path in ('/apis/resource.k8s.io/v1/namespaces/' + second + '/resourceclaims',
                     '/api/v1/namespaces/' + second + '/pods'):
            with self.subTest(path=path):
                self.k.fail_paths = {path}
                self.assert_unknown()

    def test_missing_expected_namespace_is_unknown(self):
        second = self.namespace()
        del self.k.objects['/api/v1/namespaces/' + second]
        self.assert_unknown()

    def test_namespace_uid_replacement_and_foreign_owner_are_unknown(self):
        ns = self.k.objects['/api/v1/namespaces/' + self.ns]
        ns['metadata']['uid'] = 'replacement'
        self.assert_unknown()
        ns['metadata']['uid'] = 'ns-a'
        ns['metadata']['ownerReferences'][0]['uid'] = 'foreign-tenant'
        self.assert_unknown()

    def test_tenant_uid_change_during_observation_is_unknown(self):
        original = self.k.call
        def racing(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if path.endswith('/resourceclaims'):
                self.k.objects[TENANTS + '/team-a']['metadata']['uid'] = 'replacement'
            return result
        self.k.call = racing
        self.assert_unknown()

    def test_namespace_uid_change_during_observation_is_unknown(self):
        original = self.k.call
        def racing(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if path.endswith('/resourceclaims'):
                self.k.objects['/api/v1/namespaces/' + self.ns]['metadata']['uid'] = 'replacement'
            return result
        self.k.call = racing
        self.assert_unknown()

    def test_foreign_tenant_usage_is_not_returned_or_collected(self):
        self.k.objects[TENANTS + '/team-b'] = {'metadata': {'name': 'team-b', 'uid': 't-b'}}
        self.k.objects['/api/v1/namespaces/tenant-team-b'] = {'metadata': {
            'name': 'tenant-team-b', 'uid': 'ns-b', 'labels': {'capsule.clastix.io/tenant': 'team-b'},
            'ownerReferences': [{'kind': 'Tenant', 'name': 'team-b', 'uid': 't-b'}]}}
        self.claim('secret-allocation', ['private-device'], namespace='tenant-team-b')
        _, state = self.api.dispatch(self.user, 'GET', '/api/state')
        self.assertEqual([row['name'] for row in state['tenants']], ['team-a'])
        self.assertIn('usage', state['tenants'][0])
        self.assertNotIn('private-device', json.dumps(state))
        self.assertFalse(any('/namespaces/tenant-team-b/' in path for _, path in self.k.calls))

    def test_revoked_membership_has_no_usage_collection(self):
        self.k.objects[ACCESS]['data']['bindings.json'] = '[]'
        _, state = self.api.dispatch(self.user, 'GET', '/api/state')
        self.assertEqual(state['tenants'], [])
        self.assertFalse(any(path.endswith(('/pods', '/resourceclaims')) for _, path in self.k.calls))

    def test_aggregation_reads_all_kubernetes_pages(self):
        first = self.claim('first', ['mig1'])
        second = self.claim('second', ['mig2'])
        path = '/apis/resource.k8s.io/v1/namespaces/' + self.ns + '/resourceclaims'
        self.k.objects[path] = {'items': [first], 'metadata': {'continue': 'next'}}
        original = self.k.call
        def paged(url, *args, **kwargs):
            if url == path + '?continue=next':
                return {'items': [second], 'metadata': {}}
            return original(url, *args, **kwargs)
        self.k.call = paged
        self.assert_counts(2, 0)

    def test_incomplete_allocation_and_unknown_pod_phase_are_unknown(self):
        claim = self.claim('broken', ['mig1'])
        del claim['status']['allocation']['devices']['results'][0]['pool']
        self.assert_unknown()
        claim['status'] = {}
        self.pod('uncertain', phase='Unknown')
        self.assert_unknown()

    def test_unavailable_native_path_is_not_reported_as_zero_dra_usage(self):
        del self.k.objects['/apis/resource.k8s.io/v1/resourceslices/s1']
        self.assert_unknown()


if __name__ == '__main__':
    unittest.main()
