"""The v4 IAM path: what v4 fills in that v3 left blank, and where it means something else."""

from __future__ import annotations

from typing import Any

from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.collectors.iam import IamCollector
from ntnxvision_core.models import Environment

# All payloads trimmed from a live pc.7.5.1.1.

# A directory user: the only kind v3 also named correctly.
V4_LDAP_USER = {
    "extId": "00007d92-3cb3-4ec3-a509-1dce253dba06",
    "username": "administrator",
    "displayName": "Administrator",
    "firstName": "Administrator",
    "lastName": "",
    "idpId": "0000463b-575a-4fa8-aaa1-b1c8a0a8e3c2",
    "status": "ACTIVE",
    "userType": "LDAP",
}

# A built-in service identity. v3 returned an empty spec for these, so its username was
# null; v4 also leaves displayName empty and types it SERVICE_ACCOUNT rather than LOCAL.
V4_SERVICE_USER = {
    "extId": "00009c13-170e-49e6-a73e-2765996274a6",
    "username": "ovasystemuser123",
    "displayName": "",
    "firstName": "",
    "lastName": "",
    "description": "",
    # The built-in provider — deliberately absent from the directory-services list.
    "idpId": "00001798-34bb-4da6-a486-5b3bedf76b70",
    "status": "ACTIVE",
    "userType": "SERVICE_ACCOUNT",
}

V4_DIRECTORY_SERVICE = {
    "extId": "0000463b-575a-4fa8-aaa1-b1c8a0a8e3c2",
    "name": "example",
    "domainName": "example.lab",
    "directoryType": "ACTIVE_DIRECTORY",
}

V4_ROLES = [
    {
        "extId": "00001658-9f08-4761-ac3f-9d2149c05b03",
        "displayName": "Super Admin",
        "description": "Super Admin Role",
        "isSystemDefined": True,
        "operations": ["op-1", "op-2", "op-3"],
    },
    {
        "extId": "0000a62c-d805-4a60-a4bf-bde5f2e63cd3",
        "displayName": "Category Viewer",
        "description": "",
        "isSystemDefined": True,
        "operations": [],
    },
]

# The v3 shapes the fallback path still has to read.
V3_LDAP_USER = {
    "metadata": {"uuid": V4_LDAP_USER["extId"]},
    "spec": {
        "resources": {
            "directory_service_user": {
                "user_principal_name": "administrator",
                "directory_service_reference": {"name": "example"},
            }
        }
    },
    "status": {"state": "COMPLETE", "resources": {"display_name": "Administrator"}},
}

V3_SERVICE_USER = {
    "metadata": {"uuid": V4_SERVICE_USER["extId"]},
    # Empty spec — the reason v3 leaves username null for local/service accounts.
    "spec": {"resources": {}},
    "status": {"state": "COMPLETE", "resources": {"display_name": "ovasystemuser123"}},
}


class _Conn:
    def __init__(
        self,
        v4: bool = True,
        users: list[dict[str, Any]] | None = None,
        directories: list[dict[str, Any]] | None = None,
        directories_fail: bool = False,
    ) -> None:
        self._v4 = v4
        self.users = users if users is not None else [dict(V4_LDAP_USER), dict(V4_SERVICE_USER)]
        self.directories = (
            directories if directories is not None else [dict(V4_DIRECTORY_SERVICE)]
        )
        self.directories_fail = directories_fail
        self.v3_calls: list[str] = []
        self.v4_calls: list[str] = []

    def v4_ready(self, area: str) -> bool:
        return self._v4

    def v4_list(self, path: str) -> list[dict[str, Any]]:
        self.v4_calls.append(path)
        if path.endswith("/users"):
            return [dict(u) for u in self.users]
        if path.endswith("/roles"):
            return [dict(r) for r in V4_ROLES]
        if path.endswith("/directory-services"):
            if self.directories_fail:
                raise NutanixApiError("gone", status_code=404)
            return [dict(d) for d in self.directories]
        if path.endswith("/saml-identity-providers"):
            return []
        raise AssertionError(f"unexpected v4 path {path}")

    def v3_list(self, kind: str) -> list[dict[str, Any]]:
        self.v3_calls.append(kind)
        if kind == "users":
            return [dict(V3_LDAP_USER), dict(V3_SERVICE_USER)]
        if kind == "roles":
            return [
                {
                    "metadata": {"uuid": V4_ROLES[0]["extId"]},
                    "spec": {
                        "name": "Super Admin",
                        "description": "Super Admin Role",
                        "resources": {"permission_reference_list": [1, 2, 3]},
                    },
                }
            ]
        if kind == "projects":
            return []
        raise AssertionError(f"unexpected v3 kind {kind}")


def _collect(conn: _Conn) -> Environment:
    env = Environment()
    IamCollector(conn).collect(env)  # type: ignore[arg-type]
    return env


def test_v4_user_maps_the_core_fields() -> None:
    user = _collect(_Conn()).users[0]
    assert user.uuid == V4_LDAP_USER["extId"]
    assert user.username == "administrator"
    assert user.display_name == "Administrator"
    assert user.user_type == "LDAP"
    assert user.api_source == "v4"


def test_username_is_filled_in_where_v3_left_it_null() -> None:
    """The gain: v3's empty spec meant no username for local or service accounts."""
    assert _collect(_Conn()).users[1].username == "ovasystemuser123"
    # …which is exactly what the v3 path could not produce.
    assert _collect(_Conn(v4=False)).users[1].username is None


def test_display_name_falls_back_when_v4_pads_it_with_an_empty_string() -> None:
    """v4 sends "" rather than omitting the key; v3 filled it with the username."""
    v4_user = _collect(_Conn()).users[1]
    v3_user = _collect(_Conn(v4=False)).users[1]
    assert v4_user.display_name == "ovasystemuser123"
    assert v4_user.display_name == v3_user.display_name


def test_display_name_prefers_the_name_parts_over_the_username() -> None:
    user = dict(V4_SERVICE_USER, firstName="Ada", lastName="Lovelace")
    assert _collect(_Conn(users=[user])).users[0].display_name == "Ada Lovelace"


def test_directory_is_resolved_from_the_idp_id() -> None:
    """v4 names the provider only by id, so it has to be looked up."""
    assert _collect(_Conn()).users[0].directory == "example"


def test_built_in_providers_leave_the_directory_column_blank() -> None:
    """Local and service accounts carry an idpId too — it is not a directory service.

    Resolving it to *something* would put a fake directory next to every local user.
    """
    assert _collect(_Conn()).users[1].directory is None


def test_an_unreadable_provider_list_costs_the_column_not_the_run() -> None:
    env = _collect(_Conn(directories_fail=True))
    assert env.users[0].directory is None
    assert env.users[0].username == "administrator"
    assert env.collection_notes["iam"] == "ok"


def test_state_is_the_account_status_not_v3s_entity_state() -> None:
    """Deliberate semantic change: v3's `state` was COMPLETE for every user, always."""
    assert _collect(_Conn()).users[0].state == "ACTIVE"
    assert _collect(_Conn(v4=False)).users[0].state == "COMPLETE"


def test_service_accounts_keep_v4s_more_precise_type() -> None:
    """v3 has no service-account concept and types these LOCAL; v4's answer is right."""
    assert _collect(_Conn()).users[1].user_type == "SERVICE_ACCOUNT"
    assert _collect(_Conn(v4=False)).users[1].user_type == "LOCAL"


def test_v4_roles_map_onto_the_v3_fields() -> None:
    role = {r.name: r for r in _collect(_Conn()).roles}["Super Admin"]
    assert role.uuid == V4_ROLES[0]["extId"]
    assert role.description == "Super Admin Role"
    assert role.is_system is True
    # v4's `operations` is v3's permission_reference_list under another name.
    assert role.permission_count == 3
    assert role.api_source == "v4"


def test_empty_role_description_becomes_none_rather_than_an_empty_cell() -> None:
    role = {r.name: r for r in _collect(_Conn()).roles}["Category Viewer"]
    assert role.description is None
    assert role.permission_count == 0


def test_roles_are_sorted_so_the_order_does_not_depend_on_the_path() -> None:
    """v4 returns roles in an order unrelated to v3's; both are arbitrary."""
    assert [r.name for r in _collect(_Conn()).roles] == ["Category Viewer", "Super Admin"]


def test_projects_stay_on_v3_because_v4_serves_none() -> None:
    conn = _Conn()
    env = _collect(conn)
    assert conn.v3_calls == ["projects"]
    assert env.projects == []
    assert env.collection_notes["iam"] == "ok"


def test_v3_path_is_used_when_v4_is_not_served() -> None:
    conn = _Conn(v4=False)
    env = _collect(conn)
    assert conn.v4_calls == []
    assert [u.api_source for u in env.users] == ["v3", "v3"]
    assert env.roles[0].api_source == "v3"
    assert env.roles[0].permission_count == 3
