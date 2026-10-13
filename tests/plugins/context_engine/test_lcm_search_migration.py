"""An upgraded database must immediately search every retained v1 message."""
import sqlite3
import pytest
from plugins.context_engine.lcm import storage


def _legacy_database(path):
    conn = sqlite3.connect(path)
    storage._migrate_to_1(conn)
    conn.execute("INSERT INTO schema_meta VALUES ('schema_version', '1')")
    rows = [
        ("session", 1, "old-message", "user", "legacy needle", '{"content":"original"}', 0, 1.0),
        ("other-session", 1, "other-message", "user", "other needle", '{"content":"other"}', 0, 1.0),
    ]
    conn.executemany("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    return conn, rows


def test_upgrade_backfills_search_without_mutating_raw_rows_or_duplicating_index(tmp_path):
    path = tmp_path / "legacy.db"
    conn, original_rows = _legacy_database(path)
    conn.close()
    for _ in range(2):
        store = storage.LCMStore(path).open()
        assert [hit["message_id"] for hit in store.search("session", "needle")] == ["old-message"]
        assert [hit["message_id"] for hit in store.search("other-session", "needle")] == ["other-message"]
        assert store._conn.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0] == 2
        raw = [tuple(row) for row in store._conn.execute("SELECT * FROM messages ORDER BY session_id DESC")]
        assert raw == original_rows
        store.close()


def test_failed_search_migration_rolls_back_schema_and_backfill(tmp_path, monkeypatch):
    conn, original_rows = _legacy_database(tmp_path / "legacy.db")
    migration = storage._MIGRATIONS[3]
    def fail_after_backfill(connection):
        migration(connection)
        raise RuntimeError("injected migration failure")
    monkeypatch.setitem(storage._MIGRATIONS, 3, fail_after_backfill)
    try:
        with pytest.raises(RuntimeError, match="injected migration failure"):
            storage.migrate(conn)
        assert storage.schema_version(conn) == 1
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='messages_fts'").fetchone() is None
        assert conn.execute("SELECT * FROM messages ORDER BY session_id DESC").fetchall() == original_rows
    finally:
        conn.close()


@pytest.mark.parametrize("partial", [False, True])
def test_v2_search_gaps_are_repaired_once_without_duplicating_valid_rows(tmp_path, partial):
    path = tmp_path / "existing-v2.db"
    conn, original_rows = _legacy_database(path)
    storage._migrate_to_2(conn)
    conn.execute("DELETE FROM messages_fts")
    if partial:
        conn.execute("INSERT INTO messages_fts VALUES ('other-session', 'other-message', 'other needle')")
    conn.execute("UPDATE schema_meta SET value='2' WHERE key='schema_version'")
    conn.commit()
    conn.close()
    for _ in range(2):
        store = storage.LCMStore(path).open()
        try:
            assert [hit["message_id"] for hit in store.search("session", "needle")] == ["old-message"]
            assert [hit["message_id"] for hit in store.search("other-session", "needle")] == ["other-message"]
            assert store._conn.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0] == 2
            assert [tuple(row) for row in store._conn.execute("SELECT * FROM messages ORDER BY session_id DESC")] == original_rows
            assert store.version() == storage.SCHEMA_VERSION
        finally:
            store.close()


def test_failed_v3_repair_preserves_existing_v2_index_and_version(tmp_path, monkeypatch):
    conn, original_rows = _legacy_database(tmp_path / "existing-v2.db")
    storage._migrate_to_2(conn)
    conn.execute("INSERT INTO messages_fts VALUES ('other-session', 'other-message', 'other needle')")
    conn.execute("UPDATE schema_meta SET value='2' WHERE key='schema_version'")
    conn.commit()
    migration = storage._MIGRATIONS[3]
    def fail_after_backfill(connection):
        migration(connection)
        raise RuntimeError("injected repair failure")
    monkeypatch.setitem(storage._MIGRATIONS, 3, fail_after_backfill)
    try:
        with pytest.raises(RuntimeError, match="injected repair failure"):
            storage.migrate(conn)
        assert storage.schema_version(conn) == 2
        assert conn.execute("SELECT * FROM messages_fts").fetchall() == [
            ("other-session", "other-message", "other needle")]
        assert conn.execute("SELECT * FROM messages ORDER BY session_id DESC").fetchall() == original_rows
    finally:
        conn.close()
