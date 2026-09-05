"""IAM collector — users and roles over v4 `iam` when served, projects always v3.

**Users and roles move cleanly; projects have nowhere to move to.** v4 serves
``iam/v4.0/authn/users`` and ``iam/v4.0/authz/roles``, but there is no projects
endpoint in any namespace on pc.7.5.1.1 — ``iam/v4.0/config/projects``,
``vmm/v4.0/config/projects`` and ``prism/v4.0/config/projects`` all 404. Projects are a
Self-Service (Calm) concept that v4 has not lifted, so they stay on ``v3/projects/list``
and are collected the same way whichever path served the rest.

**v4 is strictly richer for users.** v3's ``users/list`` returns an empty ``spec`` for
local and service accounts, so the existing v3 path leaves ``username`` null for every
one of them — 9 of the lab's 23 users had a blank Username column. v4 always carries it.

Two v4 values are deliberately *not* forced back into v3's shape, because they are more
accurate rather than differently spelled:

* ``state`` — v3 reported ``status.state``, the entity spec-application state, which is
  ``COMPLETE`` for every user ever returned. v4's ``status`` is the account state
  (``ACTIVE`` / ``INACTIVE``), which is what a column headed "State" on a user table is
  read as meaning.
* ``user_type`` — v3 has no notion of a service account, so its payload types the
  built-in service identities (lcm_service, catalog-images, …) as ``LOCAL``. v4 types
  them ``SERVICE_ACCOUNT``. For the three types v3 does know, v4 already uses the same
  vocabulary this collector emits (``LOCAL`` / ``LDAP`` / ``SAML``), so nothing needs
  normalizing.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.models import Project, Role, User


class IamCollector(Collector):
    name = "iam"
    label = "IAM (Users / Roles / Projects)"

    def collect(self, env: Any) -> None:
        if self.conn.v4_ready("iam"):
            self._users_v4(env)
            self._roles_v4(env)
        else:
            self._users_v3(env)
            self._roles_v3(env)
        # No v4 equivalent exists; see the module docstring.
        self._projects_v3(env)
        env.collection_notes[self.name] = "ok"

    # ─── v4 ────────────────────────────────────────────────────────

    def _users_v4(self, env: Any) -> None:
        directories = self._v4_identity_providers()
        for entity in self.conn.v4_list("iam/v4.0/authn/users"):
            idp_id = _text(entity.get("idpId")) or ""
            env.users.append(
                User(
                    uuid=entity.get("extId"),
                    username=_text(entity.get("username")),
                    display_name=_display_name(entity),
                    user_type=entity.get("userType"),
                    # v4 names the identity provider only by id. Local and service
                    # accounts also carry an idpId (the built-in providers), which is
                    # absent from the directory list — so the lookup misses and the
                    # column stays blank for them, exactly as on v3.
                    directory=directories.get(idp_id),
                    state=entity.get("status"),
                    api_source="v4",
                )
            )

    def _v4_identity_providers(self) -> dict[str, str]:
        """extId → display name, for every provider a user's ``idpId`` can point at.

        Best-effort: an unreadable provider list costs the Directory column, not the run.
        """
        out: dict[str, str] = {}
        for path in (
            "iam/v4.0/authn/directory-services",
            "iam/v4.0/authn/saml-identity-providers",
        ):
            try:
                entities = self.conn.v4_list(path)
            except NutanixApiError:
                continue
            for entity in entities:
                ext_id, name = entity.get("extId"), _text(entity.get("name"))
                if ext_id and name:
                    out[ext_id] = name
        return out

    def _roles_v4(self, env: Any) -> None:
        rows = [
            Role(
                uuid=entity.get("extId"),
                name=_text(entity.get("displayName")),
                description=_text(entity.get("description")),
                is_system=entity.get("isSystemDefined"),
                # `operations` is v4's permission list — same 61 counts as v3's
                # permission_reference_list, role for role, on the lab PC.
                permission_count=len(entity.get("operations") or []),
                api_source="v4",
            )
            for entity in self.conn.v4_list("iam/v4.0/authz/roles")
        ]
        # v4 returns roles in an order unrelated to v3's (both are arbitrary), which
        # would reshuffle the Roles sheet purely on which path ran. Sort by name.
        env.roles.extend(sorted(rows, key=lambda r: (r.name or "").lower()))

    # ─── v3 ────────────────────────────────────────────────────────

    def _users_v3(self, env: Any) -> None:
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

    def _roles_v3(self, env: Any) -> None:
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

    def _projects_v3(self, env: Any) -> None:
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


def _display_name(entity: dict[str, Any]) -> str | None:
    """The best human name v4 offers for a user.

    v4 pads unset strings with ``""`` rather than omitting them, and leaves
    ``displayName`` empty for service accounts where v3 filled it with the username.
    Falling back through the name parts and then the username reproduces v3's value for
    every user on the lab PC instead of blanking three of them.
    """
    display = _text(entity.get("displayName"))
    if display:
        return display
    parts = [p for p in (_text(entity.get("firstName")), _text(entity.get("lastName"))) if p]
    return " ".join(parts) if parts else _text(entity.get("username"))


def _text(value: Any) -> str | None:
    """A non-empty string, or None — v4 uses "" where v3 omits the key entirely."""
    return value if isinstance(value, str) and value else None
