"""Loading an org directory into a validated, cross-checked aggregate.

An org is a DIRECTORY, not a database: flightdeck.yaml (identity, economics,
policy), models.yaml (the governed model registry), usecases.yaml (the backlog)
and workflows/*.yaml (the promoted ones). Everything reviewable in a pull
request — governance changes should have diffs and reviewers, like code.

Loading is strict: unknown keys, duplicate ids and dangling references fail
loudly with the offending file in the message. Runtime state (runs.sqlite3,
ledger.jsonl) lives under .flightdeck/ and is never committed.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import ValidationError

from flightdeck.directory import Directory
from flightdeck.schemas import ModelSpec, OrgConfig, UseCase, Workflow

ORG_FILE = "flightdeck.yaml"
MODELS_FILE = "models.yaml"
USECASES_FILE = "usecases.yaml"
DIRECTORY_FILE = "directory.yaml"
WORKFLOWS_DIR = "workflows"
STATE_DIR = ".flightdeck"

ConfigItem = TypeVar("ConfigItem", ModelSpec, UseCase, Workflow)


class ConfigError(Exception):
    """A human-actionable configuration problem: message always names the file."""


@dataclass
class Org:
    root: Path
    config: OrgConfig
    models: dict[str, ModelSpec] = field(default_factory=dict)
    usecases: dict[str, UseCase] = field(default_factory=dict)
    workflows: dict[str, Workflow] = field(default_factory=dict)
    #: Synced SSO directory snapshot. Empty when no directory.yaml is present, so
    #: the feature is opt-in and its absence changes nothing.
    directory: Directory = field(default_factory=Directory)

    @property
    def state_dir(self) -> Path:
        path = self.root / STATE_DIR
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def db_path(self) -> Path:
        return self.state_dir / "runs.sqlite3"

    @property
    def ledger_path(self) -> Path:
        return self.state_dir / "ledger.jsonl"

    def hourly_cost(self, workflow: Workflow) -> float:
        return workflow.baseline.hourly_cost or self.config.default_hourly_cost

    def department_headcount(self, name: str) -> int | None:
        """The adoption denominator's source of truth. When the synced directory
        has active members in ``name``, that resolved count wins over the
        hand-maintained YAML headcount; otherwise fall back to the declared
        ``departments`` headcount, else unknown."""
        directory_count = self.directory.department_headcount(name)
        if directory_count:
            return directory_count
        for dept in self.config.departments:
            if dept.name == name:
                return dept.headcount
        return None

    def eligible_users(self, workflow: Workflow) -> int | None:
        """Adoption denominator: explicit on the workflow, else the department
        headcount, else unknown (reports show 'n/a' rather than inventing one)."""
        return workflow.eligible_users or self.department_headcount(workflow.department)


def _read_yaml(path: Path) -> dict:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"{path}: file not found") from None
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML — {exc}") from None
    if raw is None:
        raise ConfigError(f"{path}: file is empty")
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: expected a mapping at the top level")
    return raw


def _read_collection(path: Path, key: str) -> list[object]:
    """Load a strict single-key document whose value is a list or null."""
    raw = _read_yaml(path)
    if unknown := sorted(repr(name) for name in raw if name != key):
        names = ", ".join(unknown)
        raise ConfigError(f"{path}: unknown top-level key(s): {names}; expected only {key!r}")
    items = raw.get(key)
    if items is None:
        return []
    if not isinstance(items, list):
        raise ConfigError(f"{path}: {key!r} must be a list, got {type(items).__name__}")
    return items


def _validation_error(path: Path, exc: ValidationError) -> ConfigError:
    lines = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err["loc"])
        lines.append(f"  {loc}: {err['msg']}")
    return ConfigError(f"{path}: invalid configuration\n" + "\n".join(lines))


def _load_indexed_items(
    raw_items: Iterable[object],
    *,
    path: Path,
    item_type: type[ConfigItem],
    kind: str,
    items: dict[str, ConfigItem] | None = None,
    before_index: Callable[[ConfigItem], None] | None = None,
) -> dict[str, ConfigItem]:
    """Validate and index config items, rejecting duplicate IDs at their source."""
    indexed = items if items is not None else {}
    for raw_item in raw_items:
        try:
            item = item_type.model_validate(raw_item)
        except ValidationError as exc:
            raise _validation_error(path, exc) from None
        if item.id in indexed:
            raise ConfigError(f"{path}: duplicate {kind} id '{item.id}'")
        if before_index:
            before_index(item)
        indexed[item.id] = item
    return indexed


def _validate_workflow_use_case(
    workflow: Workflow, path: Path, usecases: dict[str, UseCase]
) -> None:
    if workflow.use_case and workflow.use_case not in usecases:
        raise ConfigError(f"{path}: use_case '{workflow.use_case}' not found in {USECASES_FILE}")


def load_org(root: Path | str) -> Org:
    """Load and cross-validate an org directory. Workflows and use cases are
    optional (a fresh org starts empty); the org file and model registry are not."""
    root = Path(root)
    org_path = root / ORG_FILE
    if not org_path.exists():
        raise ConfigError(
            f"{org_path}: not a flightdeck org (run `flightdeck init` here, or pass --dir)"
        )

    try:
        config = OrgConfig.model_validate(_read_yaml(org_path))
    except ValidationError as exc:
        raise _validation_error(org_path, exc) from None

    models_path = root / MODELS_FILE
    models = _load_indexed_items(
        _read_collection(models_path, "models"),
        path=models_path,
        item_type=ModelSpec,
        kind="model",
    )
    if not models:
        raise ConfigError(f"{models_path}: the model registry is empty")

    usecases: dict[str, UseCase] = {}
    usecases_path = root / USECASES_FILE
    if usecases_path.exists():
        usecases = _load_indexed_items(
            _read_collection(usecases_path, "usecases"),
            path=usecases_path,
            item_type=UseCase,
            kind="use case",
        )

    workflows: dict[str, Workflow] = {}
    workflows_dir = root / WORKFLOWS_DIR
    if workflows_dir.is_dir():
        for path in sorted(workflows_dir.glob("*.yaml")) + sorted(workflows_dir.glob("*.yml")):
            _load_indexed_items(
                [_read_yaml(path)],
                path=path,
                item_type=Workflow,
                kind="workflow",
                items=workflows,
                before_index=lambda workflow, workflow_path=path: _validate_workflow_use_case(
                    workflow, workflow_path, usecases
                ),
            )

    # Optional SSO directory snapshot (like usecases.yaml, absent is fine).
    directory = Directory.from_file(root / DIRECTORY_FILE)

    return Org(
        root=root,
        config=config,
        models=models,
        usecases=usecases,
        workflows=workflows,
        directory=directory,
    )
