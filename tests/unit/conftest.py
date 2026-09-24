"""Keep unit tests independent of the live application's resource pause switch."""

import pytest


@pytest.fixture(autouse=True)
def isolated_local_inference_switch(monkeypatch, tmp_path):
    monkeypatch.setattr("eoa.resources.gate.LOCAL_INFERENCE_PAUSE_FILE", tmp_path / "local-inference.pause")


@pytest.fixture(autouse=True)
def isolated_status_cache_and_inference_lock(monkeypatch, tmp_path):
    monkeypatch.setattr("eoa.resources.inference.LOCK_PATH", tmp_path / "inference.lock")
    monkeypatch.setattr("eoa.api.services._services_cache", (0, {}))


def _raise_unmocked_db_access(*_args, **_kwargs):
    raise RuntimeError(
        "Unmocked DB access in a unit test: eoa.db.connection()/get_pool() was called for real. "
        "Stub the specific DB-touching function (e.g. _tender_exists, _patent_exists, "
        "_candidate_duplicate_exists, log_llm_call) or monkeypatch eoa.db.connection/eoa.db.get_pool "
        "yourself. Real-Postgres coverage belongs in a tests/unit/*_postgres.py file (the existing "
        "convention, gated by its own DATABASE_URL/pg-availability skipif)."
    )


@pytest.fixture(autouse=True)
def _guard_unmocked_db_access(request, monkeypatch):
    """R-DB (round-5 fix, SOL-REVIEW3/4-2026-09-24 DB-leak audit): several unit tests called
    ``eoa.db.connection()`` for real -- no ``DATABASE_URL`` -> ``psycopg_pool.PoolTimeout`` after
    ~30s; WITH it loaded -> silent reads/writes against the live dev DB (confirmed: a stray
    ``patents`` row, pub_number='US1', from `test_patents_scan.py`). This fails those fast and
    loud instead of hanging or touching the live DB.

    Exempts ``tests/unit/*_postgres.py`` by filename -- the existing, already-reused convention for
    deliberate real-Postgres coverage (each such file gates itself on ``DATABASE_URL``/pg-
    availability via its own ``pytestmark = pytest.mark.skipif(...)``; see
    ``tests/unit/test_insert_item_upsert_postgres.py``).

    Patches ``eoa.db.connection``/``eoa.db.get_pool`` themselves (not the call site) precisely so a
    test that legitimately needs a real connection stays in control: any monkeypatch/patch it
    applies to ``eoa.db.connection``, ``eoa.db.get_pool``, or a module's own
    ``from eoa.db import connection`` binding (e.g. ``eoa.memory.vector.connection`` in
    ``test_vector.py``) runs inside the test body, i.e. strictly after this fixture's setup, and
    therefore wins. ``get_pool`` is patched too (not just ``connection``) because every call site in
    this codebase imports via ``from eoa.db import connection`` -- a module-local binding that
    patching only ``eoa.db.connection`` would never reach -- while ``connection()``'s own body
    resolves ``get_pool`` from ``eoa.db``'s module globals on every call, making it the one choke
    point that is actually shared by every caller regardless of import style."""
    if str(request.node.fspath).endswith("_postgres.py"):
        return
    import eoa.db as db

    monkeypatch.setattr(db, "connection", _raise_unmocked_db_access)
    monkeypatch.setattr(db, "get_pool", _raise_unmocked_db_access)
