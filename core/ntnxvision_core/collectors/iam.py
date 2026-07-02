"""IAM collector — users, roles, and projects (v3)."""

from __future__ import annotations

from typing import Any

from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.models import Project, Role, User


class IamCollector(Collector):
    name = "iam"
    label = "IAM (Users / Roles / Projects)"

    def collect(self, env: Any) -> None:
        self._users(env)
        self._roles(env)
        self._projects(env)
        env.collection_notes[self.name] = "ok"

    def _users(self, env: Any) -> None:
        for entity in self.conn.v3_list("users"):
            spec = entity.get("spec", {})
            resources = spec.get("resources", {})
            # The human display name lives on the *status* side for directory/SAML
            # users; the spec often has none.
            status_res = (entity.get("status", {}) or {}).get("resources", {}) or {}
            directory = resources.get("directory_service_user", {}) or status_res.get(
                "directory_service_user", {}
            )
            saml = resources.get("identity_provider_user", {}) or status_res.get(
                "identity_provider_user", {}
            )
            if directory:
                user_type, name, dirname = "LDAP", directory.get("user_principal_name"), \
                    directory.get("directory_service_reference", {}).get("name")
            elif saml:
                user_type, name, dirname = "SAML", saml.get("username"), \
                    saml.get("identity_provider_reference", {}).get("name")
            else:
                local = resources.get("local_user", {})
                user_type, name, dirname = "LOCAL", local.get("username") or spec.get("name"), None
            env.users.append(
                User(
                    uuid=entity.get("metadata", {}).get("uuid"),
                    username=name,
                    display_name=status_res.get("display_name")
                    or resources.get("display_name")
                    or (directory or {}).get("display_name")
                    or (saml or {}).get("display_name"),
                    user_type=user_type,
                    directory=dirname,
                    state=entity.get("status", {}).get("state"),
                    api_source="v3",
                )
            )

    def _roles(self, env: Any) -> None:
        for entity in self.conn.v3_list("roles"):
            spec = entity.get("spec", {})
            resources = spec.get("resources", {})
            env.roles.append(
                Role(
                    uuid=entity.get("metadata", {}).get("uuid"),
                    name=spec.get("name"),
                    description=spec.get("description"),
                    is_system=entity.get("metadata", {}).get("owner_reference") is None,
                    permission_count=len(resources.get("permission_reference_list", []) or []),
                    api_source="v3",
                )
            )

    def _projects(self, env: Any) -> None:
        for entity in self.conn.v3_list("projects"):
            spec = entity.get("spec", {})
            resources = spec.get("resources", {})
            env.projects.append(
                Project(
                    uuid=entity.get("metadata", {}).get("uuid"),
                    name=spec.get("name"),
                    description=spec.get("description"),
                    user_count=len(resources.get("user_reference_list", []) or []),
                    api_source="v3",
                )
            )
