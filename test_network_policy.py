import importlib.util
import unittest


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('network_policy'), 'network policy generation required')
        from network_policy import namespace_network_policy
        self.make = namespace_network_policy

    def test_default_denies_both_directions(self):
        p = self.make('tenant-a', [], [])
        self.assertEqual(p['spec']['podSelector'], {})
        self.assertEqual(p['spec']['policyTypes'], ['Ingress', 'Egress'])
        self.assertEqual(p['spec']['egress'], [])
        self.assertEqual(p['spec']['ingress'], [])

    def test_dns_only_targets_selected_pods_on_53(self):
        peer = {'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'kube-system'}},
                'podSelector': {'matchLabels': {'k8s-app': 'kube-dns'}}}
        p = self.make('tenant-a', [peer], [])
        self.assertEqual(p['spec']['egress'][0]['to'], [peer])
        self.assertEqual({(x['port'], x['protocol']) for x in p['spec']['egress'][0]['ports']}, {(53, 'UDP'), (53, 'TCP')})
        self.assertEqual(p['spec']['ingress'], [])

    def test_unbounded_peer_is_rejected(self):
        for peer in [{}, {'namespaceSelector': {}}, {'ipBlock': {'cidr': '0.0.0.0/0'}}]:
            with self.assertRaises(ValueError): self.make('tenant-a', [peer], [])

if __name__ == '__main__': unittest.main()
