"""Explicit peers only; an empty rule set remains deny-all."""
import copy
import re


def namespace_network_policy(namespace, dns_rules, monitoring_rules):
    if not re.fullmatch(r'[a-z0-9](?:[-a-z0-9]*[a-z0-9])?', namespace):
        raise ValueError('invalid namespace')
    for peer in dns_rules + monitoring_rules:
        if (set(peer) != {'namespaceSelector', 'podSelector'} or
            not peer['namespaceSelector'].get('matchLabels') or
            not peer['podSelector'].get('matchLabels')):
            raise ValueError('explicit namespace and pod selectors required')
    return {'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy',
        'metadata': {'name': 'tenant-boundary', 'namespace': namespace},
        'spec': {'podSelector': {}, 'policyTypes': ['Ingress', 'Egress'],
                 'ingress': [{'from': copy.deepcopy(monitoring_rules)}] if monitoring_rules else [],
                 'egress': [{'to': copy.deepcopy(dns_rules), 'ports': [
                     {'port': 53, 'protocol': 'UDP'}, {'port': 53, 'protocol': 'TCP'}]}] if dns_rules else []}}
