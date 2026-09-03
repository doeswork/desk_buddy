"""Managed MQTT identities shared by the Network and Vision workspaces."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from PySide6.QtCore import QCoreApplication, QObject, QStandardPaths, Signal

from ...storage.user_config import keys
from ...storage.user_config.settings import APP, ORG, Settings, settings
from ..network import accounts as broker_accounts
from ..network import broker_commands
from ..network.broker_finder import DEFAULT_PORT
from .manifests import BUILTIN_MANIFESTS, ProviderManifestV1, imported_manifests


@dataclass(frozen=True)
class VisionAccessIdentity:
    identity_id: str
    role: str
    worker_id: str = ""
    controller: bool = False

    @property
    def account_name(self) -> str:
        if self.controller:
            return "studio-vision"
        return "vw-" + hashlib.sha256(self.worker_id.encode()).hexdigest()[:12]

    @property
    def environment_prefix(self) -> str:
        if self.controller:
            return "DESK_BUDDY_STUDIO_MQTT"
        worker = "".join(
            character if character.isalnum() or character == "_" else "_"
            for character in self.worker_id.upper()
        )
        return f"DESK_BUDDY_MANAGED_{worker}_MQTT"

    @property
    def acl_summary(self) -> str:
        if self.controller:
            return "All MQTT topics, including firmware commands"
        return "Read request + artifact/in; write event + artifact/out + status; no firmware"


@dataclass(frozen=True)
class VisionAccessPlan:
    identities: tuple[VisionAccessIdentity, ...]

    def identity(self, identity_id: str) -> VisionAccessIdentity | None:
        return next((item for item in self.identities if item.identity_id == identity_id), None)

    def worker(self, worker_id: str) -> VisionAccessIdentity | None:
        return next((item for item in self.identities if item.worker_id == worker_id), None)


@dataclass(frozen=True)
class ManagedCredentialState:
    identity_id: str
    role: str
    worker_id: str
    account_name: str
    username: str = ""
    password: str = ""
    source: str = "missing"
    status: str = "missing"
    error: str = ""
    acl_summary: str = ""
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    can_manage: bool = True

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AccessOperationResult:
    states: tuple[ManagedCredentialState, ...]

    @property
    def ok(self) -> bool:
        return all(item.ready for item in self.states)

    @property
    def errors(self) -> tuple[str, ...]:
        return tuple(f"{item.role}: {item.error}" for item in self.states if item.error)


class CredentialStore:
    """Application-private plaintext copy used to launch managed children."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def get(self, identity: str) -> tuple[str, str] | None:
        values = self._read().get(identity)
        if not isinstance(values, dict):
            return None
        username, password = str(values.get("username") or ""), str(values.get("password") or "")
        return (username, password) if username and password else None

    def set(self, identity: str, username: str, password: str) -> None:
        values = self._read()
        values[identity] = {"username": username, "password": password}
        self._write(values)

    def delete(self, identity: str) -> None:
        values = self._read()
        if identity in values:
            values.pop(identity)
            self._write(values)

    def usernames(self) -> set[str]:
        return {
            str(item.get("username") or "")
            for item in self._read().values()
            if isinstance(item, dict) and item.get("username")
        }

    def identities(self) -> set[str]:
        return {str(key) for key in self._read()}

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return dict(value) if isinstance(value, dict) else {}

    def _write(self, values: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(dict(values), separators=(",", ":")), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(self.path)
        self.path.chmod(0o600)


class VisionAccessManager(QObject):
    """Creates and exposes credentials only for Studio's localhost broker."""

    changed = Signal()

    def __init__(
        self,
        root: str | Path,
        *,
        preferences: Settings | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__()
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        self.preferences = preferences or settings()
        self.environment = os.environ if environment is None else environment
        self.credentials = CredentialStore(self.root / "credentials.json")
        self.last_result: AccessOperationResult | None = None

    @property
    def broker_host(self) -> str:
        return str(self.environment.get("DESK_BUDDY_VISION_MQTT_HOST") or "127.0.0.1")

    @property
    def broker_port(self) -> int:
        try:
            return int(self.environment.get("DESK_BUDDY_VISION_MQTT_PORT") or DEFAULT_PORT)
        except (TypeError, ValueError):
            return DEFAULT_PORT

    @property
    def can_manage_broker(self) -> bool:
        return self.broker_host == "127.0.0.1" and self.broker_port == DEFAULT_PORT

    @property
    def topic_root(self) -> str:
        return str(self.environment.get("DESK_BUDDY_VISION_TOPIC_ROOT") or "desk_buddy")

    def current_plan(
        self, manifests: Iterable[ProviderManifestV1] | None = None
    ) -> VisionAccessPlan:
        catalog = tuple(manifests or self._catalog())
        detector_worker = self._selected_worker(
            "DESK_BUDDY_DETECTOR_MODEL", "DESK_BUDDY_DETECTOR_WORKER",
            keys.VISION_DETECTOR_CANDIDATE, keys.VISION_DETECTOR_WORKER,
            "detection", catalog,
        )
        depth_worker = self._selected_worker(
            "DESK_BUDDY_DEPTH_MODEL", "DESK_BUDDY_DEPTH_WORKER",
            keys.VISION_DEPTH_CANDIDATE, keys.VISION_DEPTH_WORKER,
            "depth", catalog,
        )
        identities = (
            VisionAccessIdentity("controller", "Studio controller", controller=True),
            VisionAccessIdentity(f"worker:{detector_worker}", "Detection", detector_worker),
            VisionAccessIdentity(f"worker:{depth_worker}", "Depth", depth_worker),
            VisionAccessIdentity(
                f"worker:{self.environment.get('DESK_BUDDY_INFERENCE_WORKER') or 'ik-inference-1'}",
                "Custom MLP inference",
                str(self.environment.get("DESK_BUDDY_INFERENCE_WORKER") or "ik-inference-1"),
            ),
            VisionAccessIdentity(
                f"worker:{self.environment.get('DESK_BUDDY_TRAINER_WORKER') or 'ik-trainer-1'}",
                "MLP trainer",
                str(self.environment.get("DESK_BUDDY_TRAINER_WORKER") or "ik-trainer-1"),
            ),
        )
        unique: dict[str, VisionAccessIdentity] = {}
        for identity in identities:
            unique.setdefault(identity.identity_id, identity)
        return VisionAccessPlan(tuple(unique.values()))

    def known_plan(self) -> VisionAccessPlan:
        """Current roles plus older managed routes that can still be revoked."""
        current = self.current_plan()
        values = {item.identity_id: item for item in current.identities}
        worker_ids = {key for key in self.credentials.identities() if key != "controller"}
        for account in broker_accounts.accounts():
            if account.managed_identity.startswith("worker:"):
                worker_ids.add(account.managed_identity.split(":", 1)[1])
            elif account.vision_worker_id and account.managed:
                worker_ids.add(account.vision_worker_id)
        for worker_id in sorted(worker_ids):
            identity_id = f"worker:{worker_id}"
            values.setdefault(
                identity_id,
                VisionAccessIdentity(identity_id, "Inactive Vision worker", worker_id),
            )
        return VisionAccessPlan(tuple(values.values()))

    def state(self, identity: VisionAccessIdentity) -> ManagedCredentialState:
        username, password, source, env_error = self._configured_credentials(identity)
        accounts = {item.name: item for item in broker_accounts.accounts()}
        expected_name = username or identity.account_name
        account = accounts.get(expected_name)
        exact = self._account_matches(account, identity)
        error = env_error
        if env_error:
            status = "error"
        elif source == "environment":
            status = "ready"
        elif not self.can_manage_broker:
            status = "error"
            error = "External brokers require credentials supplied through environment variables."
        elif username and password and exact:
            status = "ready"
        elif account is not None and exact:
            status = "secret_missing"
            error = "Broker account exists, but Studio no longer has its password. Rotate access to recover it."
        elif account is not None:
            status = "acl_mismatch"
            error = "The account exists with permissions that do not match this managed identity."
        else:
            status = "missing"
        return ManagedCredentialState(
            identity_id=identity.identity_id,
            role=identity.role,
            worker_id=identity.worker_id,
            account_name=identity.account_name,
            username=username,
            password=password,
            source=source,
            status=status,
            error=error,
            acl_summary=identity.acl_summary,
            host=self.broker_host,
            port=self.broker_port,
            can_manage=source != "environment" and self.can_manage_broker,
        )

    def states(self, plan: VisionAccessPlan | None = None) -> tuple[ManagedCredentialState, ...]:
        selected = plan or self.current_plan()
        return tuple(self.state(identity) for identity in selected.identities)

    def ensure_all(self, plan: VisionAccessPlan | None = None) -> AccessOperationResult:
        selected = plan or self.current_plan()
        states = []
        for identity in selected.identities:
            try:
                states.append(self.ensure(identity, notify=False))
            except Exception as exc:
                current = self.state(identity)
                states.append(ManagedCredentialState(**{**current.as_dict(), "status": "error", "error": str(exc)}))
        self.last_result = AccessOperationResult(tuple(states))
        self.changed.emit()
        return self.last_result

    def ensure_worker(self, worker_id: str, role: str = "Vision worker") -> ManagedCredentialState:
        return self.ensure(VisionAccessIdentity(f"worker:{worker_id}", role, worker_id))

    def ensure_controller(self) -> ManagedCredentialState:
        return self.ensure(VisionAccessIdentity("controller", "Studio controller", controller=True))

    def ensure(
        self, identity: VisionAccessIdentity, *, notify: bool = True
    ) -> ManagedCredentialState:
        current = self.state(identity)
        if current.source == "environment":
            if not current.ready:
                raise RuntimeError(current.error)
            return self._finished(current, notify)
        if not self.can_manage_broker:
            raise RuntimeError("Studio cannot create accounts on an external MQTT broker.")
        if not broker_commands.is_ours():
            raise RuntimeError("Start Studio's localhost broker before creating managed Vision access.")
        if current.ready:
            problem = broker_accounts.mark_managed(current.username, identity.identity_id)
            if problem:
                raise RuntimeError(problem)
            return self._finished(self.state(identity), notify)
        if current.status == "secret_missing":
            raise RuntimeError(current.error)
        if current.status == "acl_mismatch":
            saved = self.credentials.get(self._store_key(identity))
            if not saved or saved[0] != identity.account_name:
                raise RuntimeError(current.error)
            problem = broker_accounts.repair_access(
                identity.account_name,
                full_access=identity.controller,
                vision_worker_id=identity.worker_id,
                managed_identity=identity.identity_id,
            )
            if problem:
                raise RuntimeError(problem)
            return self._finished(self.state(identity), notify)
        created, problem = broker_accounts.add(
            identity.account_name,
            full_access=identity.controller,
            vision_worker_id=identity.worker_id,
            managed_identity=identity.identity_id,
        )
        if problem or created is None:
            raise RuntimeError(problem or "could not create MQTT access")
        self.credentials.set(self._store_key(identity), created.account.name, created.password)
        return self._finished(self.state(identity), notify)

    def rotate(self, identity: VisionAccessIdentity) -> ManagedCredentialState:
        current = self.state(identity)
        if not current.can_manage:
            raise RuntimeError("These credentials cannot be rotated by Studio.")
        if not broker_commands.is_ours():
            raise RuntimeError("Studio's localhost broker is not running")
        account = next((item for item in broker_accounts.accounts() if item.name == identity.account_name), None)
        if account is None:
            return self.ensure(identity)
        if not self._account_matches(account, identity):
            raise RuntimeError("refusing to rotate an account whose ACLs do not match this identity")
        password, problem = broker_accounts.reset_password(account.name)
        if problem or not password:
            raise RuntimeError(problem or "could not rotate MQTT password")
        self.credentials.set(self._store_key(identity), account.name, password)
        marker_problem = broker_accounts.mark_managed(account.name, identity.identity_id)
        if marker_problem:
            raise RuntimeError(marker_problem)
        result = self.state(identity)
        self.last_result = AccessOperationResult((result,))
        self.changed.emit()
        return result

    def revoke(self, identity: VisionAccessIdentity) -> ManagedCredentialState:
        current = self.state(identity)
        if not current.can_manage:
            raise RuntimeError("These credentials cannot be revoked by Studio.")
        if not broker_commands.is_ours():
            raise RuntimeError("Studio's localhost broker is not running")
        account = next((item for item in broker_accounts.accounts() if item.name == identity.account_name), None)
        if account is not None:
            if not self._account_matches(account, identity):
                raise RuntimeError("refusing to remove an account whose ACLs do not match this identity")
            problem = broker_accounts.remove(account.name)
            if problem:
                raise RuntimeError(problem)
        self.credentials.delete(self._store_key(identity))
        result = self.state(identity)
        self.last_result = AccessOperationResult((result,))
        self.changed.emit()
        return result

    def setup_bundle(self, identity: VisionAccessIdentity) -> str:
        state = self.state(identity)
        if not state.ready or not state.username or not state.password:
            raise RuntimeError("create or repair this MQTT identity before copying its setup")
        prefix = identity.environment_prefix
        values = [
            f"# {identity.role}: {identity.worker_id or 'controller'}",
            f"export {prefix}_USERNAME={shlex.quote(state.username)}",
            f"export {prefix}_PASSWORD={shlex.quote(state.password)}",
            "",
            "[mqtt]",
            f"host = {json.dumps(state.host)}",
            f"port = {state.port}",
            f"topic_root = {json.dumps(self.topic_root)}",
            f'username_env = "{prefix}_USERNAME"',
            f'password_env = "{prefix}_PASSWORD"',
        ]
        if identity.worker_id:
            values.extend(("", "[worker]", f'worker_id = "{identity.worker_id}"'))
        return "\n".join(values) + "\n"

    def is_managed_account(self, account: broker_accounts.Account | str) -> bool:
        item = account if isinstance(account, broker_accounts.Account) else next(
            (candidate for candidate in broker_accounts.accounts() if candidate.name == account), None
        )
        if item is None:
            return False
        if item.managed:
            return True
        expected = {identity.account_name for identity in self.current_plan().identities}
        return item.name in expected or item.name in self.credentials.usernames()

    def _finished(
        self, state: ManagedCredentialState, notify: bool
    ) -> ManagedCredentialState:
        if notify:
            self.last_result = AccessOperationResult((state,))
            self.changed.emit()
        return state

    def _configured_credentials(
        self, identity: VisionAccessIdentity
    ) -> tuple[str, str, str, str]:
        prefix = identity.environment_prefix
        username = str(self.environment.get(f"{prefix}_USERNAME") or "")
        password = str(self.environment.get(f"{prefix}_PASSWORD") or "")
        if bool(username) != bool(password):
            owner = "Studio MQTT" if identity.controller else "managed worker MQTT"
            return username, password, "environment", f"Provide both {owner} username and password."
        if username and password:
            return username, password, "environment", ""
        saved = self.credentials.get(self._store_key(identity))
        return (*saved, "stored", "") if saved else ("", "", "missing", "")

    @staticmethod
    def _account_matches(
        account: broker_accounts.Account | None, identity: VisionAccessIdentity
    ) -> bool:
        if account is None:
            return False
        if identity.controller:
            return account.full_access and not account.vision_worker_id
        return account.vision_worker_id == identity.worker_id and not account.full_access

    @staticmethod
    def _store_key(identity: VisionAccessIdentity) -> str:
        return "controller" if identity.controller else identity.worker_id

    def _catalog(self) -> tuple[ProviderManifestV1, ...]:
        directory = self.root / "manifests"
        return (*BUILTIN_MANIFESTS, *imported_manifests(directory))

    def _selected_worker(
        self,
        model_environment: str,
        worker_environment: str,
        candidate_key,
        worker_key,
        family: str,
        catalog: tuple[ProviderManifestV1, ...],
    ) -> str:
        explicit_worker = str(self.environment.get(worker_environment) or "")
        if explicit_worker:
            return explicit_worker
        model_id = str(self.environment.get(model_environment) or self.preferences.get(candidate_key))
        manifest = next(
            (item for item in catalog if item.family == family and item.model_id == model_id), None
        )
        return manifest.worker_id if manifest else str(self.preferences.get(worker_key))


_DEFAULT_ACCESS: VisionAccessManager | None = None


def vision_data_root() -> Path:
    QCoreApplication.setOrganizationName(ORG)
    QCoreApplication.setApplicationName(APP)
    root = Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)) / "vision"
    root.mkdir(parents=True, exist_ok=True)
    return root


def default_vision_access() -> VisionAccessManager:
    global _DEFAULT_ACCESS
    if _DEFAULT_ACCESS is None:
        _DEFAULT_ACCESS = VisionAccessManager(vision_data_root() / "services")
    return _DEFAULT_ACCESS
