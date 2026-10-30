"""Check-store path resolution: where the diff memory lives decides
whether it survives a redeploy. Durability is measured (nearest
existing ancestor is a mount point), never assumed from the path
string — the probes are module-level so tests can fake a volume.
"""

import app.config as config


def test_resolve_db_path_explicit_env_wins(monkeypatch, tmp_path):
    target = str(tmp_path / "checks.db")
    monkeypatch.setenv("ROUTE_VERDICT_DB", target)
    path, persistent = config.resolve_db_path()
    assert path == target
    assert persistent is False  # tmp_path is not a mount


def test_resolve_db_path_default_is_ephemeral(monkeypatch):
    monkeypatch.delenv("ROUTE_VERDICT_DB", raising=False)
    monkeypatch.setattr(config, "_isdir", lambda p: False)
    path, persistent = config.resolve_db_path()
    assert path == "route-verdict.db"
    assert persistent is False


def test_resolve_db_path_uses_mounted_volume(monkeypatch):
    monkeypatch.delenv("ROUTE_VERDICT_DB", raising=False)
    monkeypatch.setattr(config, "_isdir", lambda p: True)
    monkeypatch.setattr(config, "_ismount", lambda p: True)
    monkeypatch.setattr(config, "_writable", lambda p, mode: True)
    path, persistent = config.resolve_db_path()
    assert path == "/data/route-verdict.db"
    assert persistent is True
