"""A backup destination must never alias the source database."""
import os
import pytest
from plugins.context_engine.lcm.storage import LCMStore, backup_db


@pytest.mark.parametrize("alias", ["same", "relative", "symlink", "directory_symlink", "hardlink"])
def test_backup_refuses_source_alias_without_changing_original(tmp_path, monkeypatch, alias):
    source = tmp_path / "data" / "lcm.db"
    store = LCMStore(source).open()
    store.sync_transcript("session", [{"role": "user", "content": "retained original"}])
    store.close()
    original = source.read_bytes()
    destination = source
    if alias == "relative":
        monkeypatch.chdir(source.parent)
        destination = source.relative_to(source.parent)
    elif alias == "symlink":
        destination = tmp_path / "alias.db"
        destination.symlink_to(source)
    elif alias == "directory_symlink":
        directory = tmp_path / "alias-directory"
        directory.symlink_to(source.parent, target_is_directory=True)
        destination = directory / source.name
    elif alias == "hardlink":
        destination = tmp_path / "hardlink.db"
        os.link(source, destination)
    with pytest.raises(ValueError, match="destination.*source"):
        backup_db(source, destination)
    assert source.read_bytes() == original
    assert destination.exists()
    check = LCMStore(source).open()
    try:
        assert check.stats("session")["messages"] == 1
        assert check.search("session", "retained original")
    finally:
        check.close()
