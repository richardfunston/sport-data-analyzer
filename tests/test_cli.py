"""Creating the first account from the shell, or from the environment.

The password for the first member is never committed, so both paths exist: a
CLI for a live host, and an environment bootstrap for a fresh clone.
"""

from __future__ import annotations

import pytest

from app import create_app
from app.cli import main
from app.config import Config
from app.storage import Store


def _config_for(data_dir):
    class TmpConfig(Config):
        DATA_DIR = data_dir
        TESTING = True

    return TmpConfig


# -- the CLI -----------------------------------------------------------------


def test_the_cli_creates_a_member(tmp_path, capsys):
    data = tmp_path / "data"
    code = main(
        [
            "create-member",
            "--email", "rpf@funston.com",
            "--name", "Richard",
            "--password", "not-a-real-password",
            "--admin",
            "--data-dir", str(data),
        ]
    )
    assert code == 0

    store = Store(data)
    user = store.get_member_by_email("rpf@funston.com")
    assert user is not None
    assert user["name"] == "Richard"
    assert user["is_admin"] == 1
    assert store.verify_member("rpf@funston.com", "not-a-real-password") is not None


def test_the_cli_creates_a_plain_member_without_admin(tmp_path):
    data = tmp_path / "data"
    main(
        [
            "create-member",
            "--email", "ben@funston.com",
            "--name", "Ben",
            "--password", "pw",
            "--data-dir", str(data),
        ]
    )
    assert Store(data).get_member_by_email("ben@funston.com")["is_admin"] == 0


def test_the_cli_never_echoes_the_password(tmp_path, capsys):
    main(
        [
            "create-member",
            "--email", "rpf@funston.com",
            "--name", "Richard",
            "--password", "hunter2-should-not-be-printed",
            "--data-dir", str(tmp_path / "data"),
        ]
    )
    captured = capsys.readouterr()
    assert "hunter2-should-not-be-printed" not in captured.out
    assert "hunter2-should-not-be-printed" not in captured.err


def test_the_cli_refuses_a_duplicate_email(tmp_path, capsys):
    data = tmp_path / "data"
    args = [
        "create-member",
        "--email", "rpf@funston.com",
        "--name", "Richard",
        "--password", "pw",
        "--data-dir", str(data),
    ]
    assert main(args) == 0
    assert main(args) != 0
    assert "already" in capsys.readouterr().err.lower()
    assert len(Store(data).list_members()) == 1


def test_the_cli_creates_nothing_without_a_password(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SDA_INITIAL_MEMBER_PASSWORD", raising=False)
    data = tmp_path / "data"
    code = main(
        [
            "create-member",
            "--email", "rpf@funston.com",
            "--name", "Richard",
            "--data-dir", str(data),
        ]
    )
    assert code != 0
    assert "password" in capsys.readouterr().err.lower()
    assert Store(data).get_member_by_email("rpf@funston.com") is None


def test_the_cli_refuses_an_unusable_email(tmp_path):
    data = tmp_path / "data"
    code = main(
        [
            "create-member",
            "--email", "not-an-address",
            "--name", "Richard",
            "--password", "pw",
            "--data-dir", str(data),
        ]
    )
    assert code != 0
    assert Store(data).list_members() == []


def test_the_cli_takes_the_password_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SDA_INITIAL_MEMBER_PASSWORD", "from-the-environment")
    data = tmp_path / "data"
    code = main(
        [
            "create-member",
            "--email", "rpf@funston.com",
            "--name", "Richard",
            "--data-dir", str(data),
        ]
    )
    assert code == 0
    assert Store(data).verify_member("rpf@funston.com", "from-the-environment") is not None


# -- the environment bootstrap ----------------------------------------------


def _bootstrap_env(monkeypatch, **values):
    for key in (
        "SDA_INITIAL_MEMBER_EMAIL",
        "SDA_INITIAL_MEMBER_NAME",
        "SDA_INITIAL_MEMBER_PASSWORD",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in values.items():
        monkeypatch.setenv(f"SDA_INITIAL_MEMBER_{key.upper()}", value)


def test_a_fresh_install_can_bootstrap_its_first_admin(tmp_path, monkeypatch):
    _bootstrap_env(
        monkeypatch,
        email="rpf@funston.com",
        name="Richard",
        password="not-a-real-password",
    )
    data = tmp_path / "data"

    create_app(_config_for(data))

    store = Store(data)
    richard = store.get_member_by_email("rpf@funston.com")
    assert richard is not None
    assert richard["is_admin"] == 1
    assert store.verify_member("rpf@funston.com", "not-a-real-password") is not None


def test_the_bootstrap_does_nothing_without_the_variables(tmp_path, monkeypatch):
    _bootstrap_env(monkeypatch)
    data = tmp_path / "data"

    create_app(_config_for(data))

    assert Store(data).list_members() == []


def test_the_bootstrap_does_not_fire_once_a_member_exists(tmp_path, monkeypatch):
    """It must not resurrect an account or reset a password on every restart."""
    data = tmp_path / "data"
    Store(data).create_member("someone@funston.com", "Someone", "their-own-pw")

    _bootstrap_env(
        monkeypatch, email="rpf@funston.com", name="Richard", password="not-a-real-password"
    )
    create_app(_config_for(data))

    store = Store(data)
    assert store.get_member_by_email("rpf@funston.com") is None
    assert len(store.list_members()) == 1


def test_the_bootstrap_ignores_an_unusable_email(tmp_path, monkeypatch, caplog):
    """A typo must not create an account nobody can sign in as."""
    _bootstrap_env(monkeypatch, email="nonsense", name="Richard", password="pw")
    data = tmp_path / "data"

    create_app(_config_for(data))

    assert Store(data).list_members() == []
