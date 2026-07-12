"""The org directory — a synced, file-backed identity snapshot.

flightdeck attributes every run to a user. Left to free text, "ana", "Ana G."
and "ana.garcia@example.com" are three different people to the KPIs, and the
adoption denominator (eligible vs. active users per department) comes from
hand-maintained YAML headcounts instead of the source of truth.

This module resolves a free-text identity against a SYNCED DIRECTORY SNAPSHOT
(``directory.yaml``) to a STABLE id, and exposes the directory's active
headcount per department as the adoption denominator. It is deliberately
OFFLINE and DETERMINISTIC, exactly like the provider adapters: the actual pull
from Azure AD / Google Workspace is a documented, pluggable sync (``from_provider``)
that writes ``directory.yaml`` out-of-band; the core only ever reads the file.

Privacy stance: runs store the STABLE id (an opaque directory key), and reports
render the display name at read time. A run attributed before the person joined
the directory still renders — ``display_name`` falls back to the id.
"""

from collections.abc import Iterable
from pathlib import Path

from pydantic import ValidationError

from flightdeck.schemas import DirectorySnapshot, DirectoryUser

#: The optional extra needed for each live sync adapter (mirrors the provider extras).
_SYNC_EXTRAS = {"azure_ad": "azure", "google_workspace": "google"}


class DirectorySyncError(Exception):
    """A live directory sync could not run (missing extra, missing credentials,
    unknown provider). Mirrors ``ProviderError``: the offline core never triggers
    it — it is raised only by the documented, network-backed ``from_provider``
    adapter, so the file-backed core and the test suite never touch a network."""


class Directory:
    """A resolved, in-memory view over a directory snapshot.

    Construct empty (the feature is opt-in — an absent ``directory.yaml`` yields
    an empty directory and changes nothing), from a file, or — for a real sync —
    from a live provider adapter."""

    def __init__(self, users: Iterable[DirectoryUser] = (), provider: str = "file") -> None:
        self.provider = provider
        self.users: list[DirectoryUser] = list(users)
        # Resolution indexes, built once. First-in-file wins on any collision, so
        # resolution is deterministic regardless of dict iteration order.
        self._by_id: dict[str, DirectoryUser] = {}
        self._by_email: dict[str, DirectoryUser] = {}
        self._by_alias: dict[str, DirectoryUser] = {}
        self._by_name: dict[str, DirectoryUser] = {}
        for user in self.users:
            self._by_id.setdefault(user.id, user)
            if user.email:
                self._by_email.setdefault(user.email.lower(), user)
            for alias in user.aliases:
                self._by_alias.setdefault(alias.lower(), user)
            self._by_name.setdefault(user.display_name.lower(), user)

    # ------------------------------------------------------------------ loading

    @classmethod
    def from_file(cls, path: Path) -> "Directory":
        """Load and validate a directory snapshot. An absent file yields an empty
        directory (opt-in; absence must not change existing behavior). A duplicate
        id or an unknown key fails loudly with the file named, exactly like the
        rest of the config loader."""
        # Local import breaks the config <-> directory import cycle: config.py
        # imports Directory at module load; directory.py touches config only here,
        # at call time, when both modules are fully initialized.
        from flightdeck.config import ConfigError, _read_yaml, _validation_error

        if not path.exists():
            return cls()
        try:
            snapshot = DirectorySnapshot.model_validate(_read_yaml(path))
        except ValidationError as exc:
            raise _validation_error(path, exc) from None
        seen: set[str] = set()
        for user in snapshot.users:
            if user.id in seen:
                raise ConfigError(f"{path}: duplicate directory id '{user.id}'")
            seen.add(user.id)
        return cls(users=snapshot.users, provider=snapshot.provider)

    @classmethod
    def from_provider(cls, provider: str, **options: object) -> "Directory":
        """Populate a directory snapshot from a LIVE SSO provider — the documented
        extension point for a real, network-backed sync.

        The offline core never calls this; a scheduled job runs it out-of-band,
        then writes the result to ``directory.yaml`` (which the core reads). It
        mirrors ``providers.get_provider``: an unwired provider fails with a clear,
        actionable message instead of importing a network SDK into the core.

        To wire a real adapter, install the extra, authenticate, page the
        directory (Azure AD: Microsoft Graph ``/users``; Google Workspace: Admin
        SDK Directory ``users.list``), map each record to a ``DirectoryUser``
        (stable id → ``id``), and serialize a ``DirectorySnapshot`` to disk."""
        if provider in _SYNC_EXTRAS:
            extra = _SYNC_EXTRAS[provider]
            raise DirectorySyncError(
                f"live '{provider}' directory sync needs the optional extra and credentials — "
                f"pip install 'ai-flightdeck[{extra}]', configure the adapter, and write the "
                f"result to directory.yaml (see docs/governance.md). The offline core only reads "
                f"directory.yaml; it never calls the directory API."
            )
        raise DirectorySyncError(
            f"unknown directory provider '{provider}' — offline snapshot source: file; "
            f"live sync adapters: azure_ad, google_workspace (see docs/governance.md)"
        )

    # --------------------------------------------------------------- resolution

    def resolve(self, identity: str) -> DirectoryUser | None:
        """Resolve a free-text identity to a directory user, deterministically.

        Match precedence: exact ``id``, then ``email``, then ``alias``, then
        ``display_name`` (every match after id is case-insensitive). Returns
        ``None`` when nothing matches — the caller then keeps the raw string, so
        an unknown user is attributed exactly as before the directory existed."""
        if not identity:
            return None
        user = self._by_id.get(identity)
        if user is not None:
            return user
        lowered = identity.lower()
        for index in (self._by_email, self._by_alias, self._by_name):
            user = index.get(lowered)
            if user is not None:
                return user
        return None

    def department_headcount(self, department: str) -> int:
        """Number of ACTIVE members in a department — the directory-sourced
        adoption denominator. Zero when the department is absent or has no active
        members (the caller then falls back to the declared YAML headcount)."""
        return sum(1 for user in self.users if user.active and user.department == department)

    def display_name(self, stable_id: str) -> str:
        """Render a stored stable id as a human name at report time. Falls back to
        the id itself when unknown, so a run attributed before the person was in
        the directory still renders."""
        user = self._by_id.get(stable_id)
        return user.display_name if user is not None else stable_id
