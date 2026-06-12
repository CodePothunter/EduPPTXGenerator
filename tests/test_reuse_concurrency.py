"""Concurrency regressions for the reuse pipeline (M-12 / M-13).

The reuse policy phase fans slides out across a ThreadPoolExecutor. Two shared
mutations on that hot path were unsynchronized:

* M-13 — ``_append_reuse_debug_record`` did a read-modify-write of one JSON file
  with no lock, silently losing records and clobbering a shared ``.tmp`` file.
* M-12 — the R5 near-miss VLM budget used a split check-then-act, letting
  parallel workers overshoot ``R5_MAX_VLM_CALLS_PER_SESSION``.

These tests reproduce the races under real thread contention.
"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

from edupptx.materials.ai_image_asset_db import (
    R5_MAX_VLM_CALLS_PER_SESSION,
    R5_SESSION_VLM_COUNT_KEY,
    _append_reuse_debug_record,
    _r5_try_reserve_session_vlm_budget,
)


def test_append_reuse_debug_record_no_lost_updates_under_contention(tmp_path):
    debug_path = tmp_path / "materials" / "ai_image_reuse_debug.json"
    workers = 48
    barrier = threading.Barrier(workers)

    def append(i: int) -> None:
        barrier.wait()  # release all workers at once to maximize interleaving
        _append_reuse_debug_record(debug_path, {"slot": i})

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(append, range(workers)))

    payload = json.loads(debug_path.read_text(encoding="utf-8"))
    slots = sorted(record["slot"] for record in payload["queries"])
    assert slots == list(range(workers))  # every record survived, none lost
    # No staging file should leak after the writes settle.
    assert not list(debug_path.parent.glob("*.tmp"))


def test_r5_vlm_budget_reservation_never_overshoots(tmp_path):
    state: dict[str, object] = {}
    workers = 64
    barrier = threading.Barrier(workers)

    def reserve(_: int) -> bool:
        barrier.wait()
        return _r5_try_reserve_session_vlm_budget(state)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        granted = list(pool.map(reserve, range(workers)))

    assert sum(granted) == R5_MAX_VLM_CALLS_PER_SESSION
    assert state[R5_SESSION_VLM_COUNT_KEY] == R5_MAX_VLM_CALLS_PER_SESSION


def test_r5_vlm_budget_none_state_denies():
    # No coordination dict available -> conservatively refuse.
    assert _r5_try_reserve_session_vlm_budget(None) is False
