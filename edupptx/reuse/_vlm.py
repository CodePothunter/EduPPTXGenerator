"""复用层 R5 near-miss VLM 兜底审阅 + session VLM 预算原子 reserve（M-12）。函数体逐字一致。"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from loguru import logger as PROGRESS_LOGGER

# R5 session VLM 预算锁，随其唯一消费者 _r5_try_reserve_session_vlm_budget 归此（M-12）。
_R5_VLM_BUDGET_LOCK = threading.Lock()

from edupptx.reuse._util import (
    _clean_text,
)
from edupptx.reuse._constants import (
    R5_MAX_VLM_CALLS_PER_SESSION,
    R5_NEAR_MISS_EPSILON,
    R5_SESSION_VLM_COUNT_KEY,
)
from edupptx.reuse._assets import (
    _as_string_list,
    _asset_caption,
)
from edupptx.reuse._normalize import (
    _load_json_response,
)
from edupptx.reuse._review import (
    _clamp_score,
    _reuse_debug_asset_payload,
)


def _review_reuse_candidate_with_vlm(
    vlm_client: Any | None,
    *,
    target: dict[str, Any],
    candidate_asset: dict[str, Any],
    candidate_image_path: str | Path | None,
    accept_threshold: float,
    llm_review_result: dict[str, Any] | None,
) -> dict[str, Any]:
    """R5: VLM-side near-miss verification on the candidate image.

    Returns a dict with the canonical review-result shape (``score``,
    ``threshold``, ``decision``, ``brief_reason`` …). The caller decides
    whether to overwrite the LLM result based on the returned decision.

    Failure modes (missing client, missing image, VLM error) all degrade
    gracefully: the function returns a non-accept stub so the caller
    behaves identically to the no-fallback path.
    """

    stub = {
        "score": _clamp_score((llm_review_result or {}).get("score") or 0.0),
        "threshold": max(0.0, min(1.0, float(accept_threshold))),
        "decision": "reject",
        "brief_reason": "vlm_unavailable",
        "evidence": [],
        "risk_factors": [],
    }
    if vlm_client is None:
        stub["brief_reason"] = "vlm_client_missing"
        return stub
    if not candidate_image_path:
        stub["brief_reason"] = "vlm_image_path_missing"
        return stub
    image_path = Path(str(candidate_image_path))
    if not image_path.exists():
        stub["brief_reason"] = "vlm_image_path_not_found"
        return stub

    # Build the multimodal message. We import lazily to avoid pulling the
    # VLM module's heavy imports at module load.
    try:
        from edupptx.materials.vlm_asset_enricher import _image_data_url
    except Exception as exc:  # pragma: no cover — defensive
        stub["brief_reason"] = f"vlm_helper_import_failed: {str(exc)[:120]}"
        return stub
    try:
        data_url = _image_data_url(image_path)
    except Exception as exc:  # pragma: no cover — defensive
        stub["brief_reason"] = f"vlm_image_encode_failed: {str(exc)[:120]}"
        return stub

    target_prompt = _clean_text(_asset_caption(target))
    target_summary = {
        "caption": target_prompt,
        "context_summary": _clean_text(target.get("context_summary")),
    }

    system_text = (
        "You are a teaching-image reviewer. The candidate image was almost "
        "accepted by a text-only reviewer (score sits within "
        f"{R5_NEAR_MISS_EPSILON:.2f} of the accept threshold). Inspect the "
        "image and decide whether it can be reused for the target prompt. "
        "Answer with strict JSON only — do not add commentary."
    )
    instruction_text = (
        "Compare the image against the target requirement. Reply with "
        "{\"decision\": \"accept\"|\"reject\", \"score\": <float in 0..1>, "
        "\"brief_reason\": <string>, \"matched\": [<string>], "
        "\"missing\": [<string>]}. Set decision=accept only when the "
        "image clearly satisfies the target's content; otherwise reject."
    )
    user_content = [
        {"type": "text", "text": instruction_text},
        {"type": "text", "text": json.dumps({"target": target_summary, "candidate_text": _reuse_debug_asset_payload(candidate_asset)}, ensure_ascii=False)},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]
    messages = [
        {"role": "system", "content": system_text},
        {"role": "user", "content": user_content},
    ]

    PROGRESS_LOGGER.info(
        "AI image reuse VLM near-miss verify start: candidate_asset_id={}, llm_score={}, threshold={}",
        _clean_text(candidate_asset.get("asset_id")),
        round(stub["score"], 4),
        round(accept_threshold, 4),
    )
    chat_json = getattr(vlm_client, "chat_json", None)
    response: Any
    try:
        if callable(chat_json):
            try:
                response = chat_json(messages=messages, temperature=0.0, max_tokens=512, max_retries=1)
            except TypeError:
                response = chat_json(messages, temperature=0.0, max_tokens=512)
        else:
            chat = getattr(vlm_client, "chat", None)
            if not callable(chat):
                stub["brief_reason"] = "vlm_client_missing_chat"
                return stub
            response = _load_json_response(chat(messages=messages, temperature=0.0, max_tokens=512))
    except Exception as exc:
        PROGRESS_LOGGER.warning(
            "AI image reuse VLM near-miss verify failed: candidate_asset_id={}, error={}",
            _clean_text(candidate_asset.get("asset_id")),
            str(exc)[:200],
        )
        stub["brief_reason"] = f"vlm_call_failed: {str(exc)[:160]}"
        return stub

    if not isinstance(response, dict):
        stub["brief_reason"] = "vlm_invalid_response"
        return stub

    raw_decision = _clean_text(response.get("decision")).casefold()
    score = _clamp_score(response.get("score"))
    decision = "accept" if raw_decision == "accept" else "reject"
    PROGRESS_LOGGER.info(
        "AI image reuse VLM near-miss verify done: candidate_asset_id={}, decision={}, score={}",
        _clean_text(candidate_asset.get("asset_id")),
        decision,
        round(score, 4),
    )
    return {
        "score": score,
        "threshold": max(0.0, min(1.0, float(accept_threshold))),
        "decision": decision,
        "brief_reason": _clean_text(response.get("brief_reason")) or f"vlm_{decision}",
        "evidence": _as_string_list(response.get("matched")),
        "risk_factors": _as_string_list(response.get("missing")),
    }


def _r5_try_reserve_session_vlm_budget(near_miss_vlm_state: dict[str, Any] | None) -> bool:
    """Atomically reserve one VLM call from the session budget.

    Returns ``True`` and increments the shared counter iff budget remained.
    Uses a dedicated shared dict — *not* ``reuse_session_state`` — because the
    policy phase often runs with ``reuse_session_state`` set to ``None`` (to
    suppress occupancy races during scoring), but the near-miss VLM budget must
    still be coordinated across the parallel workers. A ``None`` dict here truly
    means "no coordination available", which conservatively denies the budget.

    The reservation happens before the VLM call (not after), so a failed/raising
    call still consumes its slot — the correct semantics for a cost ceiling.
    """

    if near_miss_vlm_state is None:
        return False
    with _R5_VLM_BUDGET_LOCK:
        used = int(near_miss_vlm_state.get(R5_SESSION_VLM_COUNT_KEY) or 0)
        if used >= R5_MAX_VLM_CALLS_PER_SESSION:
            return False
        near_miss_vlm_state[R5_SESSION_VLM_COUNT_KEY] = used + 1
        return True
