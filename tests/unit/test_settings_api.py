"""Unit tests for the settings API (findings #17/#18/#19 in
``output/reviews/codex_security_review.md``): fixed-path name resolution,
request-size cap, YAML node-count/depth limits, revision/If-Match
preconditions, and the optional shared-token check on PUT.

Service-level tests write into a temp `CONFIG_DIR` (never the repo's real
`config/*.yaml`); route-level tests use `fastapi.testclient.TestClient`
following the pattern in `tests/unit/test_api_smoke.py`.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from eoa.api import services


@pytest.fixture()
def settings_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the settings module at a throwaway config dir seeded with valid files."""
    (tmp_path / "watchlist.yaml").write_text("companies: []\n", encoding="utf-8")
    (tmp_path / "taxonomy.yaml").write_text("domains:\n  airborne_pods:\n    label: test\n", encoding="utf-8")
    (tmp_path / "models.yaml").write_text(
        "allowed_origins: [US]\nallowed_formats: [gguf]\nmodels: {}\n", encoding="utf-8"
    )
    (tmp_path / "sources.yaml").write_text("sources: []\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text("placeholder: true\n", encoding="utf-8")
    monkeypatch.setattr(services, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(
        services,
        "_SETTINGS_PATHS",
        {name: tmp_path / fname for name, fname in services.SETTINGS_FILES.items()},
    )
    return tmp_path


# --------------------------------------------------------------------------
# name resolution never joins user input into a path (finding #18)
# --------------------------------------------------------------------------


def test_settings_path_is_a_fixed_dict_lookup(settings_tmp: Path) -> None:
    for name in services.SETTINGS_FILES:
        assert services._settings_path(name) == settings_tmp / services.SETTINGS_FILES[name]


@pytest.mark.parametrize(
    "bad_name",
    ["does-not-exist", "../config", "config\\..\\..\\windows\\win.ini", "config/../../etc/passwd", ""],
)
def test_settings_path_unknown_name_raises_keyerror(settings_tmp: Path, bad_name: str) -> None:
    with pytest.raises(KeyError):
        services._settings_path(bad_name)


def test_read_settings_yaml_unknown_name_raises_keyerror(settings_tmp: Path) -> None:
    with pytest.raises(KeyError):
        services.read_settings_yaml("not-a-real-name")


def test_write_settings_yaml_unknown_name_raises_keyerror(settings_tmp: Path) -> None:
    with pytest.raises(KeyError):
        services.write_settings_yaml("not-a-real-name", "companies: []\n")


# --------------------------------------------------------------------------
# request-size cap (finding #19)
# --------------------------------------------------------------------------


def test_write_settings_yaml_rejects_oversized_payload(settings_tmp: Path) -> None:
    huge_yaml = "note: " + ("x" * (services.MAX_SETTINGS_BYTES + 1000))
    original = (settings_tmp / "watchlist.yaml").read_text(encoding="utf-8")

    errors = services.write_settings_yaml("watchlist", huge_yaml)

    assert errors
    assert any("גודל" in e for e in errors)
    assert (settings_tmp / "watchlist.yaml").read_text(encoding="utf-8") == original


def test_write_settings_yaml_accepts_payload_under_the_cap(settings_tmp: Path) -> None:
    errors = services.write_settings_yaml("watchlist", "companies: []\n")
    assert errors == []


# --------------------------------------------------------------------------
# YAML node-count / depth limits (finding #19)
# --------------------------------------------------------------------------


def test_write_settings_yaml_rejects_excessive_node_count(settings_tmp: Path) -> None:
    original = (settings_tmp / "watchlist.yaml").read_text(encoding="utf-8")
    # Flow-style list of bare numbers: >20,000 nodes but comfortably under
    # the 256 KB size cap, so this exercises the node-count limit
    # specifically rather than tripping the size check first.
    huge_list_yaml = "companies: [" + ",".join(str(i) for i in range(30_000)) + "]\n"
    assert len(huge_list_yaml.encode("utf-8")) < services.MAX_SETTINGS_BYTES

    errors = services.write_settings_yaml("watchlist", huge_list_yaml)

    assert errors
    assert any("צמתים" in e for e in errors)
    assert (settings_tmp / "watchlist.yaml").read_text(encoding="utf-8") == original


def test_write_settings_yaml_rejects_excessive_depth(settings_tmp: Path) -> None:
    nested: dict = {"companies": []}
    node = nested
    for _ in range(20):
        node["nested"] = {}
        node = node["nested"]
    deep_yaml = yaml.dump(nested)

    errors = services.write_settings_yaml("watchlist", deep_yaml)

    assert errors
    assert any("עומק" in e for e in errors)


def test_write_settings_yaml_accepts_moderate_nesting(settings_tmp: Path) -> None:
    nested: dict = {"companies": []}
    node = nested
    for _ in range(5):
        node["nested"] = {}
        node = node["nested"]
    moderate_yaml = yaml.dump(nested)

    # "companies" plus a few nested maps isn't valid per the taxonomy/models
    # structural checks, but for "watchlist" the only structural rule is
    # that `companies`, if present, must be a list -- so this should pass
    # the size/limit gate and reach (and pass) validation.
    errors = services.write_settings_yaml("watchlist", moderate_yaml)
    assert errors == []


def test_write_settings_yaml_self_referential_alias_does_not_hang(settings_tmp: Path) -> None:
    """A crafted self-referential YAML anchor must be rejected quickly, never recurse forever."""
    cyclic_yaml = "companies: &a\n  - *a\n"

    # Should return promptly (no infinite recursion) -- either accepted (if
    # under the node/depth budget) or rejected with a limit error; either
    # way this call completing at all is the regression test.
    errors = services.write_settings_yaml("watchlist", cyclic_yaml)
    assert isinstance(errors, list)


# --------------------------------------------------------------------------
# revision / If-Match precondition (finding #19)
# --------------------------------------------------------------------------


def test_settings_revision_changes_after_a_successful_write(settings_tmp: Path) -> None:
    rev_before = services.settings_revision("watchlist")
    errors = services.write_settings_yaml("watchlist", "companies: []\n# a comment to change the bytes\n")
    assert errors == []
    rev_after = services.settings_revision("watchlist")
    assert rev_before != rev_after


def test_write_settings_yaml_conflict_on_stale_revision(settings_tmp: Path) -> None:
    current_rev = services.settings_revision("watchlist")

    # Someone else writes first.
    services.write_settings_yaml("watchlist", "companies: []\n# changed out from under us\n")

    with pytest.raises(services.SettingsConflict):
        services.write_settings_yaml(
            "watchlist", "companies: []\n# my stale edit\n", expected_revision=current_rev
        )


def test_write_settings_yaml_succeeds_with_matching_revision(settings_tmp: Path) -> None:
    current_rev = services.settings_revision("watchlist")
    errors = services.write_settings_yaml(
        "watchlist", "companies: []\n# my up-to-date edit\n", expected_revision=current_rev
    )
    assert errors == []


# --------------------------------------------------------------------------
# route-level tests (TestClient), matching tests/unit/test_api_smoke.py's pattern
# --------------------------------------------------------------------------


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from eoa import db

    monkeypatch.setattr(db, "get_pool", lambda: object())
    monkeypatch.setattr(db, "close_pool", lambda: None)

    from eoa.api.app import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def test_get_settings_returns_revision(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(services, "read_settings_yaml", lambda name: "companies: []\n")
    monkeypatch.setattr(services, "settings_revision", lambda name: "abc123")

    r = client.get("/api/settings/watchlist")
    assert r.status_code == 200
    assert r.json() == {"yaml": "companies: []\n", "revision": "abc123"}


def test_put_settings_conflict_returns_409(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_write(name, yaml_text, *, expected_revision=None):
        raise services.SettingsConflict("newer-revision")

    monkeypatch.setattr(services, "write_settings_yaml", fake_write)

    r = client.put("/api/settings/watchlist", json={"yaml": "companies: []\n", "revision": "stale-revision"})
    assert r.status_code == 409
    body = r.json()
    assert body["error"]["code"] == "conflict"
    assert body["error"]["detail"]["current_revision"] == "newer-revision"


def test_put_settings_if_match_header_used_as_revision(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = {}

    def fake_write(name, yaml_text, *, expected_revision=None):
        captured["expected_revision"] = expected_revision
        return []

    monkeypatch.setattr(services, "write_settings_yaml", fake_write)

    r = client.put(
        "/api/settings/watchlist",
        json={"yaml": "companies: []\n"},
        headers={"If-Match": '"etag-value"'},
    )
    assert r.status_code == 200
    assert captured["expected_revision"] == "etag-value"


def test_put_settings_unknown_name_404(client: TestClient) -> None:
    r = client.put("/api/settings/does-not-exist", json={"yaml": "x: 1\n"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


# --------------------------------------------------------------------------
# shared-token check on PUT (finding #17)
# --------------------------------------------------------------------------


def test_put_settings_requires_token_when_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EOA_API_TOKEN", "s3cr3t")
    monkeypatch.setattr(services, "write_settings_yaml", lambda *a, **k: [])

    r_no_token = client.put("/api/settings/watchlist", json={"yaml": "companies: []\n"})
    assert r_no_token.status_code == 401
    assert r_no_token.json()["error"]["code"] == "unauthorized"

    r_wrong_token = client.put(
        "/api/settings/watchlist",
        json={"yaml": "companies: []\n"},
        headers={"X-EOA-Token": "wrong"},
    )
    assert r_wrong_token.status_code == 401

    r_right_token = client.put(
        "/api/settings/watchlist",
        json={"yaml": "companies: []\n"},
        headers={"X-EOA-Token": "s3cr3t"},
    )
    assert r_right_token.status_code == 200


def test_get_settings_unaffected_by_token(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EOA_API_TOKEN", "s3cr3t")
    monkeypatch.setattr(services, "read_settings_yaml", lambda name: "companies: []\n")
    monkeypatch.setattr(services, "settings_revision", lambda name: "abc123")

    r = client.get("/api/settings/watchlist")
    assert r.status_code == 200


def test_put_settings_no_token_required_when_env_unset(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("EOA_API_TOKEN", raising=False)
    monkeypatch.setattr(services, "write_settings_yaml", lambda *a, **k: [])

    r = client.put("/api/settings/watchlist", json={"yaml": "companies: []\n"})
    assert r.status_code == 200
