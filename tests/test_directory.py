"""The SSO directory: resolution, denominators, loud loading, and the offline
run-attribution flow. Everything here is deterministic and never touches a network."""

import pytest
import yaml
from typer.testing import CliRunner

from flightdeck.cli import app
from flightdeck.config import ConfigError, load_org
from flightdeck.directory import Directory, DirectorySyncError
from flightdeck.schemas import DirectoryUser
from flightdeck.store import Store
from tests.conftest import write_org

runner = CliRunner()


def _invoke(*args: str):
    return runner.invoke(app, list(args), env={"COLUMNS": "220"})


# A snapshot pulled from Azure AD: stable ids carry uppercase/dashes (not slugs),
# Support has 2 active members (Ana, Bob) and one inactive (Carol), Finance has 1.
DIRECTORY = {
    "provider": "azure_ad",
    "users": [
        {
            "id": "AAD-001", "display_name": "Ana García", "email": "ana.garcia@example.com",
            "department": "Support", "aliases": ["ana", "agarcia"],
        },
        {"id": "AAD-002", "display_name": "Bob Smith", "email": "bob@example.com", "department": "Support"},
        {
            "id": "AAD-003", "display_name": "Carol Lee", "email": "carol@example.com",
            "department": "Support", "active": False,
        },
        {"id": "AAD-004", "display_name": "Dan Ray", "email": "dan@example.com", "department": "Finance"},
    ],
}


def _directory() -> Directory:
    return Directory(users=[DirectoryUser(**u) for u in DIRECTORY["users"]], provider="azure_ad")


# --------------------------------------------------------------------- resolution


def test_resolve_by_id():
    assert _directory().resolve("AAD-001").id == "AAD-001"


def test_resolve_by_email_is_case_insensitive():
    assert _directory().resolve("ANA.GARCIA@EXAMPLE.COM").id == "AAD-001"


def test_resolve_by_alias():
    assert _directory().resolve("agarcia").id == "AAD-001"


def test_resolve_by_display_name_is_case_insensitive():
    assert _directory().resolve("ana garcía").id == "AAD-001"


def test_resolve_unmatched_returns_none():
    directory = _directory()
    assert directory.resolve("nobody@example.com") is None
    assert directory.resolve("") is None


def test_resolve_precedence_id_beats_email():
    users = [
        DirectoryUser(id="shared", display_name="First", email="first@example.com"),
        DirectoryUser(id="U2", display_name="Second", email="shared"),
    ]
    # "shared" is U1's id and U2's email — the id match wins.
    assert Directory(users=users).resolve("shared").id == "shared"


def test_resolve_precedence_email_beats_alias():
    users = [
        DirectoryUser(id="U1", display_name="First", email="dup@example.com"),
        DirectoryUser(id="U2", display_name="Second", aliases=["dup@example.com"]),
    ]
    assert Directory(users=users).resolve("dup@example.com").id == "U1"


def test_resolve_precedence_alias_beats_display_name():
    users = [
        DirectoryUser(id="U1", display_name="First", aliases=["token"]),
        DirectoryUser(id="U2", display_name="token"),
    ]
    assert Directory(users=users).resolve("token").id == "U1"


# --------------------------------------------------------------- headcount + render


def test_department_headcount_counts_active_members_only():
    directory = _directory()
    assert directory.department_headcount("Support") == 2  # Ana + Bob; Carol is inactive
    assert directory.department_headcount("Finance") == 1
    assert directory.department_headcount("Unknown") == 0


def test_display_name_renders_and_falls_back_to_id():
    directory = _directory()
    assert directory.display_name("AAD-001") == "Ana García"
    assert directory.display_name("not-in-directory") == "not-in-directory"


def test_empty_directory_resolves_nothing_and_counts_zero():
    directory = Directory()
    assert directory.users == []
    assert directory.resolve("ana") is None
    assert directory.department_headcount("Support") == 0
    assert directory.display_name("x") == "x"


# --------------------------------------------------------------------- from_file


def test_from_file_absent_is_empty(tmp_path):
    assert Directory.from_file(tmp_path / "directory.yaml").users == []


def test_from_file_reads_users_and_provider(tmp_path):
    path = tmp_path / "directory.yaml"
    path.write_text(yaml.safe_dump(DIRECTORY), encoding="utf-8")
    directory = Directory.from_file(path)
    assert directory.provider == "azure_ad"
    assert directory.resolve("bob@example.com").id == "AAD-002"


def test_from_file_duplicate_id_fails_naming_the_file(tmp_path):
    path = tmp_path / "directory.yaml"
    path.write_text(
        yaml.safe_dump({"users": [{"id": "X", "display_name": "A"}, {"id": "X", "display_name": "B"}]}),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"directory\.yaml: duplicate directory id 'X'"):
        Directory.from_file(path)


def test_from_file_unknown_key_fails_naming_the_file(tmp_path):
    path = tmp_path / "directory.yaml"
    path.write_text(
        yaml.safe_dump({"users": [{"id": "X", "display_name": "A", "manager": "boss"}]}),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"directory\.yaml"):
        Directory.from_file(path)


# ------------------------------------------------------------------- sync stub


def test_sync_stub_azure_ad_asks_for_the_extra():
    with pytest.raises(DirectorySyncError, match="azure"):
        Directory.from_provider("azure_ad")


def test_sync_stub_google_workspace_asks_for_the_extra():
    with pytest.raises(DirectorySyncError, match="google"):
        Directory.from_provider("google_workspace")


def test_sync_stub_unknown_provider_is_rejected():
    with pytest.raises(DirectorySyncError, match="unknown directory provider"):
        Directory.from_provider("ldap")


# ------------------------------------------------------- load_org integration


def test_load_org_without_directory_is_empty(tmp_path):
    org = load_org(write_org(tmp_path / "org"))
    assert org.directory.users == []


def test_load_org_attaches_the_directory(tmp_path):
    org = load_org(write_org(tmp_path / "org", directory=DIRECTORY))
    assert org.directory.resolve("ana.garcia@example.com").id == "AAD-001"


def test_load_org_fails_on_duplicate_directory_id(tmp_path):
    directory = {"users": [{"id": "X", "display_name": "A"}, {"id": "X", "display_name": "B"}]}
    with pytest.raises(ConfigError, match=r"directory\.yaml"):
        load_org(write_org(tmp_path / "org", directory=directory))


def test_directory_sources_the_adoption_denominator(tmp_path):
    org = load_org(write_org(tmp_path / "org", directory=DIRECTORY))
    workflow = org.workflows["support-reply"]
    # Directory has 2 active Support members; the YAML headcount is 12 → directory wins.
    assert org.department_headcount("Support") == 2
    assert org.eligible_users(workflow) == 2
    # An explicit per-workflow override still wins over both.
    explicit = workflow.model_copy(deep=True, update={"eligible_users": 4})
    assert org.eligible_users(explicit) == 4


def test_denominator_falls_back_to_yaml_when_directory_lacks_the_department(tmp_path):
    directory = {"users": [{"id": "AAD-004", "display_name": "Dan Ray", "department": "Finance"}]}
    org = load_org(write_org(tmp_path / "org", directory=directory))
    assert org.department_headcount("Support") == 12  # no Support member in directory → YAML
    assert org.department_headcount("Finance") == 1  # directory wins where it has members


# ---------------------------------------------------------- run attribution (CLI)


def _run(root, user: str):
    return _invoke(
        "run", "support-reply", "--dir", str(root),
        "--var", "ticket=I was double charged", "--user", user,
    )


def test_run_without_directory_stores_the_raw_user(tmp_path):
    root = write_org(tmp_path / "org")  # no directory.yaml → existing behavior
    result = _run(root, "ana")
    assert result.exit_code == 0, result.output
    with Store(load_org(root).db_path) as store:
        assert store.latest_runs(1)[0].user == "ana"


def test_run_with_directory_stores_the_stable_id(tmp_path):
    root = write_org(tmp_path / "org", directory=DIRECTORY)
    result = _run(root, "ana.garcia@example.com")  # an email/alias resolves to the id
    assert result.exit_code == 0, result.output
    with Store(load_org(root).db_path) as store:
        assert store.latest_runs(1)[0].user == "AAD-001"


def test_run_with_directory_keeps_unresolved_user_verbatim(tmp_path):
    root = write_org(tmp_path / "org", directory=DIRECTORY)
    result = _run(root, "stranger")
    assert result.exit_code == 0, result.output
    with Store(load_org(root).db_path) as store:
        assert store.latest_runs(1)[0].user == "stranger"
