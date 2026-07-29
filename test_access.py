import importlib.util
import unittest


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('access'), 'tenant authorization required')
        import access
        self.a = access
        self.member = access.principal_from_user({'id': 11, 'uid': 'alice', 'login': 'a'}, 'g1')
        self.bind = [dict(providerInstance='g1', subjectKind='uid', subjectId='alice',
                          tenantUID='tenant-a', role='member')]

    def denied(self, principal, tenant, action, bindings, owner=None):
        with self.assertRaises(self.a.Error) as cm:
            self.a.authorize(principal, tenant, action, bindings, owner)
        self.assertEqual(cm.exception.status, 403)

    def test_member_sees_only_bound_tenant(self):
        self.a.authorize(self.member, 'tenant-a', 'read', self.bind)
        self.denied(self.member, 'tenant-b', 'read', self.bind)

    def test_forged_login_header_denied(self):
        attacker = self.a.principal_from_user({'id': 12, 'uid': 'mallory', 'login': 'a'}, 'g1')
        self.denied(attacker, 'tenant-a', 'submit', self.bind)

    def test_viewer_cannot_submit(self):
        self.bind[0]['role'] = 'viewer'
        self.a.authorize(self.member, 'tenant-a', 'read', self.bind)
        self.denied(self.member, 'tenant-a', 'submit', self.bind)

    def test_member_cannot_cancel_peer(self):
        self.a.authorize(self.member, 'tenant-a', 'cancel', self.bind, self.member)
        self.denied(self.member, 'tenant-a', 'cancel', self.bind,
                    dict(self.member, subjectId='someone-else'))

    def test_deleted_recreated_tenant_requires_new_binding(self):
        self.denied(self.member, 'new-tenant-a-uid', 'submit', self.bind)

    def test_revocation_applies_next_request(self):
        self.a.authorize(self.member, 'tenant-a', 'submit', self.bind)
        self.denied(self.member, 'tenant-a', 'submit', [])

    def test_provider_and_key_kind_cannot_be_swapped(self):
        self.denied(dict(self.member, providerInstance='g2'), 'tenant-a', 'read', self.bind)
        self.denied(dict(self.member, subjectKind='id'), 'tenant-a', 'read', self.bind)

    def test_unknown_actions_and_roles_fail_closed(self):
        admin = dict(self.member, platformAdmin=True)
        self.denied(admin, 'tenant-a', 'delete_tenant', self.bind)
        self.bind[0]['role'] = 'owner-of-everything'
        self.denied(self.member, 'tenant-a', 'read', self.bind)

    def test_role_action_matrix(self):
        for role, allows in [('viewer', {'read'}), ('member', {'read', 'submit'}),
                             ('tenant-admin', {'read', 'submit', 'cancel', 'add_namespace'})]:
            self.bind[0]['role'] = role
            for action in ['read', 'submit', 'cancel', 'add_namespace', 'configure', 'members']:
                if action in allows:
                    self.a.authorize(self.member, 'tenant-a', action, self.bind)
                else:
                    self.denied(self.member, 'tenant-a', action, self.bind)

    def test_malformed_binding_is_rejected(self):
        for field in ['providerInstance', 'subjectKind', 'subjectId', 'tenantUID', 'role']:
            bad = dict(self.bind[0]); bad.pop(field)
            with self.assertRaises(self.a.Error): self.a.validate_bindings([bad])
        with self.assertRaises(self.a.Error): self.a.validate_bindings(self.bind * 2)

    def test_id_fallback_is_strict(self):
        p = self.a.principal_from_user({'id': 11}, 'g1')
        self.assertEqual((p['subjectKind'], p['subjectId']), ('id', '11'))
        for user in [{}, {'id': True}, {'id': -1}, {'uid': '', 'id': None}]:
            with self.assertRaises(self.a.Error): self.a.principal_from_user(user, 'g1')


if __name__ == '__main__': unittest.main()
