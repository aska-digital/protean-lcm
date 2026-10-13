"""Retained messages and DAG lineage survive ongoing turns after compaction."""
from plugins.context_engine.lcm.compaction import LCM_NODE_METADATA_KEY
from plugins.context_engine.lcm.config import LCMConfig
from plugins.context_engine.lcm.engine import LCMEngine


def test_turns_after_compaction_are_durable_and_have_exact_lineage(tmp_path):
    engine = LCMEngine(config=LCMConfig({
        "db_path": str(tmp_path / "lcm.db"),
        "protect_first_n": 1, "protect_last_n": 2,
    }))
    engine.on_session_start("session")
    original = [{"role": "user", "content": f"original {i}"} for i in range(8)]
    compacted = engine.compress(original)
    first_node = engine._last_node_id
    fresh = [{"role": "assistant", "content": f"new turn {i}"} for i in range(4)]
    transcript = compacted + fresh
    engine.on_turn_complete(transcript)
    assert engine._store.stats("session")["messages"] == len(original) + len(fresh)
    assert engine._store.search("session", "new turn 3")
    ids = engine._store.sync_transcript("session", transcript)
    assert all(ids[i] for i, message in enumerate(transcript) if LCM_NODE_METADATA_KEY not in message)
    engine.compress(transcript)
    sources = engine._store.node_sources(engine._last_node_id)
    assert sources == [("node", first_node)] + [("message", mid) for mid in ids[2:-2]]
    engine.on_session_end("session", transcript)
    engine.on_session_start("session")
    assert engine._store.stats("session")["messages"] == len(original) + len(fresh)


def test_no_protected_tail_still_retains_repeated_new_message(tmp_path):
    engine = LCMEngine(config=LCMConfig({
        "db_path": str(tmp_path / "lcm.db"),
        "protect_first_n": 1, "protect_last_n": 0,
    }))
    engine.on_session_start("session")
    original = [{"role": "user", "content": f"original {i}"} for i in range(5)]
    compacted = engine.compress(original)
    engine.on_turn_complete(compacted + [dict(original[-1])])
    assert engine._store.stats("session")["messages"] == len(original) + 1
