"""Deterministic reuse policy for simplified AI image metadata."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
import re
from typing import Any

REUSE_LEVELS = {"loose", "medium", "strict"}
ASSET_CATEGORIES = {
    "learning_behavior",
    "generic_tool",
    "generic_diagram",
    "concept_scene",
    "content_specific",
    "character_action",
    "emotion_scene",
    "symbolic_material",
    "unknown",
}
FORCED_LOOSE_CATEGORIES = {"learning_behavior", "generic_tool", "generic_diagram"}
CONSTRAINT_KINDS = {
    "entity",
    "object",
    "action",
    "scene",
    "emotion",
    "text",
    "math",
    "physics",
}
STRICT_KNOWLEDGE_CONSTRAINT_KINDS = {"text", "math", "physics"}
CONSTRAINT_SUBTYPES = {
    "named_individual",
    "species_instance",
    "role",
    "generic_class",
    "teaching_carrier",
    "layout_container",
    "scene_prop",
    "decorative",
    "teaching_fact",
    "generic_motion",
    "teaching_content",
    "decorative_text",
    "story_scene",
    "generic_ambient",
    "narrative_emotion",
}
NAMED_INDIVIDUAL_SUBTYPES = {"named_individual", "species_instance"}
GENERIC_CLASS_SUBTYPES = {"generic_class", "role"}
# Linguistic quantifier markers that turn an otherwise specific value into a
# generic class reference. Structural markers, not topic words — they apply
# regardless of subject matter. Examples covered: "多种小动物", "几只小鸟",
# "一群人", "若干学生". The reverse (target specific, candidate generic) is
# intentionally NOT covered: a slide asking for "小猫" cannot be satisfied
# by an image of "小动物".
GENERIC_QUANTIFIER_MARKERS = (
    "多种", "各种", "几只", "几种", "几个", "若干",
    "一些", "多个", "众多", "成群", "一群", "群",
)
# Hardcap word list. Only terms observed to be high-frequency LLM upgrade
# mistakes (where the three-step entity decision fails to keep imp<=1) belong
# here. Other role/job terms are left to the three-step decision in the prompt
# — they classify correctly without a hardcap. Do not add a term unless there
# is concrete evidence the LLM repeatedly upgrades it to imp=2 by mistake.
ROLE_HARDCAP_TERMS = {
    # 亲缘称谓 — narrative-center vs occupant confusion is common
    "爸爸", "妈妈", "爹", "娘", "父亲", "母亲", "妈", "爸",
    "爷爷", "奶奶", "外公", "外婆", "姥爷", "姥姥",
    "叔叔", "阿姨", "伯伯", "舅舅", "姑姑", "姨妈",
    "哥哥", "姐姐", "弟弟", "妹妹",
    "儿子", "女儿", "孙子", "孙女", "外孙", "宝宝", "宝贝",
    # 教育场景高频职业 — appear in textbook lessons and frequently mis-upgraded.
    # Other professions (厨师/服务员/售货员/运动员/舞蹈家/画家/音乐家/科学家/
    # 工程师/律师/法官/记者/园丁/清洁工/邮递员/教练/司机) are removed: the
    # three-step decision in the prompt classifies them correctly to role
    # imp<=1 without a hardcap. Re-add only on observed regression.
    "老师", "教师", "学生", "同学", "医生", "护士",
    "警察", "消防员", "农民", "工人",
    # 泛类指代 — "动物/植物/人物/小朋友" are the words LLM most often
    # erroneously elevates to imp=2 as a "本课主体"
    "男孩", "女孩", "小朋友", "孩子", "小孩",
    "男人", "女人", "人物", "人", "卡通人物", "动漫人物",
    "动物", "植物",
}
# Subtypes that make a candidate's extra constraint narrative-binding: when
# candidate carries one of these at imp>=2 but target does not cover it,
# the candidate is "more specific" than the target and reuse may distort
# the page's teaching intent. These trigger a reverse-direction LLM review.
CANDIDATE_EXTRA_STRONG_SUBTYPES = {
    "named_individual",
    "species_instance",
    "teaching_content",
    "teaching_carrier",
}
# Subtypes whose target-side imp>=1 presence binds the page's narrative.
# When the target carries one of these at imp>=1 but the candidate has no
# light-match of the same kind, the candidate is materially missing a
# narrative element the page depends on — even if every imp>=2 target
# constraint happens to be covered. Used to gate the strong-cover
# short-circuit so it can't bypass scene/role/story-bound mismatches.
NARRATIVE_BINDING_SUBTYPES = {
    "named_individual",
    "species_instance",
    "story_scene",
    "narrative_emotion",
    "teaching_carrier",
    "teaching_fact",
    "teaching_content",
}

DEFAULT_POLICY = {
    "reuse_level": "medium",
    "asset_category": "unknown",
    "constraints": [],
    "generic_support_allowed": True,
}

PAGE_IMAGE_REUSE_THRESHOLDS = {
    "loose": 0.50,
    "medium": 0.55,
    "strict": 0.63,
}
BACKGROUND_REUSE_THRESHOLD = 0.38
BACKGROUND_SAME_THEME_THRESHOLD = 0.34
BACKGROUND_SAME_THEME_HIGH_EMBEDDING_THRESHOLD = 0.30
BACKGROUND_SAME_THEME_HIGH_EMBEDDING_FLOOR = 0.70
LLM_REVIEW_REQUIRED_KINDS = {"text", "math", "physics"}
CONSTRAINT_EMBEDDING_THRESHOLDS = {
    "entity": (0.92, 0.80),
    "object": (0.92, 0.80),
    "action": (0.86, 0.74),
    "scene": (0.84, 0.72),
    "emotion": (0.84, 0.72),
    "text": (0.90, 0.78),
    "math": (0.90, 0.78),
    "physics": (0.90, 0.78),
}
SEMANTIC_SIGNAL_ACCEPT_REASONS = {"embedding_gray_zone", "substring_embedding_gray_zone"}
SCORE_GATE_LLM_REVIEW_REASONS = {
    "keyword_high_review",
    "embedding_high_review",
    "text_overlap_embedding_review",
    "keyword_led_gray_review",
    "embedding_led_gray_review",
}
SEMANTIC_EMBEDDING_ACCEPT_THRESHOLD = 0.82
SEMANTIC_SCORE_FLOOR = 0.18
STRICT_EMBEDDING_REVIEW_THRESHOLD = 0.78
STRICT_SEMANTIC_GRAY_REVIEW_THRESHOLD = 0.70
STRICT_SEMANTIC_GRAY_BM25_THRESHOLD = 0.20
# DF-ratio thresholds for subtype cross-validation. A value whose library
# document-frequency ratio is below GENERIC_CLASS_MIN_DF_RATIO cannot
# reliably be treated as a real generic-class word — most likely the
# metadata LLM mislabelled a specific term (e.g. "小猴子") as generic_class.
# At runtime the generic→specific light-match is suppressed for such values.
# Calibrated against the current 73-asset library where DF ratios peak at
# ~0.08 and the median is ~0.03; 0.05 corresponds to "used as a core
# keyword in at least ~3 assets" — a minimum bar for treating a word as
# library-wide generic. Re-tune when library scale changes meaningfully.
GENERIC_CLASS_MIN_DF_RATIO = 0.05
# Library size below which DF statistics are too noisy to trust as a gate.
# Mirrors compute_keyword_df_ratio's documented stability cutoff.
DF_RATIO_MIN_LIBRARY_SIZE = 20

# Runtime context: the current library's keyword DF ratio map (term → df/N).
# Set by ``evaluate_reuse_filter`` for the duration of one candidate
# evaluation so deep helpers (light_match, has_precision_signal) can
# consult library statistics without threading the dict through every
# call site. Always restored on exit so unrelated callers see ``None``.
_CURRENT_DF_RATIO: ContextVar[dict[str, float] | None] = ContextVar(
    "edupptx_reuse_df_ratio", default=None
)
MEDIUM_EMBEDDING_REVIEW_THRESHOLD = 0.80
AUTO_ACCEPT_EMBEDDING_FLOORS = {"strict": 0.58}
@dataclass(frozen=True)
class ReuseConstraint:
    """Normalized post-retrieval reuse constraint metadata."""

    kind: str
    value: str
    importance: int
    confidence: float = 0.0
    evidence: str = ""
    reason: str = ""
    subtype: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "subtype": self.subtype,
            "value": self.value,
            "importance": self.importance,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class AssetMetadata:
    """Schema-normalized metadata view for page-image reuse assets."""

    raw: dict[str, Any]
    asset_category: str
    constraints: list[dict[str, Any]]
    reuse_level: str
    generic_support_allowed: bool
    duplicate_asset_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_category": self.asset_category,
            "constraints": [_copy_constraint_dict(item) for item in self.constraints],
            "reuse_level": self.reuse_level,
            "generic_support_allowed": self.generic_support_allowed,
            "duplicate_asset_ids": list(self.duplicate_asset_ids),
        }


def normalize_constraints(value: Any, *, max_items: int = 12) -> list[dict[str, Any]]:
    """Normalize page-image constraints and discard unsupported constraint fields."""

    if not isinstance(value, list):
        return []

    constraints: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for item in value:
        constraint = _normalize_constraint_item(item)
        if constraint is None:
            continue
        key = (
            constraint.kind,
            _normalize_constraint_value(constraint.kind, constraint.value),
            constraint.importance,
        )
        if key in seen:
            continue
        seen.add(key)
        constraints.append(constraint.to_dict())
        if len(constraints) >= max_items:
            break
    return constraints


def normalize_asset_metadata(raw: dict[str, Any]) -> AssetMetadata:
    """Return a unified metadata view using the current ``constraints`` schema."""

    asset = raw if isinstance(raw, dict) else {}
    constraints = normalize_constraints(asset.get("constraints"))

    asset_category = _clean_text(asset.get("asset_category"))
    if asset_category not in ASSET_CATEGORIES:
        asset_category = DEFAULT_POLICY["asset_category"]

    reuse_level = derive_reuse_level_from_constraints(constraints, asset_category)
    generic_support_allowed = reuse_level == "loose"
    return AssetMetadata(
        raw=asset,
        asset_category=asset_category,
        constraints=constraints,
        reuse_level=reuse_level,
        generic_support_allowed=generic_support_allowed,
        duplicate_asset_ids=_clean_string_list(asset.get("duplicate_asset_ids")),
    )


def _normalize_constraint_item(item: Any) -> ReuseConstraint | None:
    if not isinstance(item, dict):
        return None
    kind = _normalize_constraint_kind(item.get("kind"))
    raw_value = _clean_text(item.get("value"))
    if kind not in CONSTRAINT_KINDS or not raw_value:
        return None
    if _looks_like_style_or_quality_value(raw_value):
        return None
    subtype = _normalize_constraint_subtype(item.get("subtype"))
    importance = _coerce_importance(item.get("importance"), default=0)
    subtype, importance = _apply_role_hardcap(kind, raw_value, subtype, importance)
    return ReuseConstraint(
        kind=kind,
        value=raw_value,
        importance=importance,
        confidence=_coerce_confidence(item.get("confidence"), default=0.0),
        evidence=_clean_text(item.get("evidence")),
        reason=_clean_text(item.get("reason")),
        subtype=subtype,
    )


def _normalize_constraint_subtype(value: Any) -> str:
    text = _clean_text(value).casefold()
    if text in CONSTRAINT_SUBTYPES:
        return text
    return ""


def _apply_role_hardcap(
    kind: str,
    value: str,
    subtype: str,
    importance: int,
) -> tuple[str, int]:
    """Force role/generic_class subtype + importance<=1 for words in ROLE_HARDCAP_TERMS.

    The exception is when value clearly carries a full proper name (姓+名),
    in which case it stays as named_individual (e.g. '史铁生'). This protects
    against the LLM upgrading common roles like '妈妈' to imp=2.
    """

    if kind != "entity":
        return subtype, importance
    normalized = _clean_text(value)
    if normalized in ROLE_HARDCAP_TERMS:
        if subtype not in {"role", "generic_class"}:
            subtype = "role" if importance >= 1 else "generic_class"
        if importance > 1:
            importance = 1
    return subtype, importance


PRECISION_SIGNAL_DF_RATIO_THRESHOLD = 0.25


def has_precision_signal(
    target: dict[str, Any],
    candidate: dict[str, Any],
    *,
    keyword_df_ratio: dict[str, float] | None = None,
    df_ratio_threshold: float = PRECISION_SIGNAL_DF_RATIO_THRESHOLD,
    keyword_stopwords: set[str] | None = None,
) -> bool:
    """Return True when target and candidate share at least one precision signal.

    Precision signal is one of:
      1. Shared imp>=1 constraint (same kind + light value match) between sides.
      2. Shared core_keyword that is discriminative — either it is not in the
         stopword set, or (when ``keyword_df_ratio`` is provided) its
         library document-frequency ratio is at or below ``df_ratio_threshold``.

    A target with no imp>=1 constraints AND no discriminative shared keyword
    has no precision signal — score-only matching is unsafe and must defer
    to LLM review or rejection.
    """

    target_active = _active_constraints(_dict_value(target, "constraints"))
    candidate_all = normalize_constraints(_dict_value(candidate, "constraints"))
    for t in target_active:
        same_kind = [c for c in candidate_all if c["kind"] == t["kind"]]
        # _light_constraint_match_method already consults the DF context
        # for its generic_specific branch, so a mislabelled generic_class
        # target value cannot manufacture a precision signal here either.
        if any(_light_constraint_match_method(t, c) for c in same_kind):
            return True

    stopwords = keyword_stopwords or set()
    target_kw = _normalize_keyword_set(target.get("core_keywords"))
    candidate_kw = _normalize_keyword_set(candidate.get("core_keywords"))
    if not target_kw or not candidate_kw:
        return False
    shared = target_kw & candidate_kw
    if not shared:
        return False
    for term in shared:
        if term in stopwords:
            continue
        if keyword_df_ratio is not None:
            ratio = keyword_df_ratio.get(term)
            if ratio is not None and ratio > df_ratio_threshold:
                continue
        return True
    return False


def compute_keyword_df_ratio(
    assets: list[Any],
    *,
    keyword_field: str = "core_keywords",
    asset_kind_filter: str | None = "page_image",
) -> dict[str, float]:
    """Compute document-frequency ratio (df / N) for keywords across a library.

    A term with ratio close to 1.0 appears in most assets and is non-discriminative
    (e.g. '插画', '教学'). A term with low ratio carries identifying signal.
    Library scale matters: callers should treat this as advisory unless N is
    large enough that the statistic is stable (typically N >= 20).
    """

    if not isinstance(assets, list):
        return {}
    df: dict[str, int] = {}
    n = 0
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        if asset_kind_filter is not None and _clean_text(asset.get("asset_kind")) != asset_kind_filter:
            continue
        n += 1
        seen: set[str] = set()
        for kw in asset.get(keyword_field) or []:
            token = _clean_text(kw).casefold()
            if not token or token in seen:
                continue
            seen.add(token)
            df[token] = df.get(token, 0) + 1
    if n <= 0:
        return {}
    return {term: count / n for term, count in df.items()}


def _normalize_keyword_set(value: Any) -> set[str]:
    if not isinstance(value, (list, tuple)):
        return set()
    result: set[str] = set()
    for item in value:
        token = _clean_text(item).casefold()
        if token:
            result.add(token)
    return result


def extra_teaching_content_constraints(
    target_constraints: list[dict[str, Any]],
    candidate_constraints: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return candidate teaching_content constraints not in target's set.

    teaching_content (text/math/physics) is an exact-content constraint:
    when target requests "字 比" and candidate has "字 枚 + 字 比", the
    extra "字 枚" must surface as a mismatched_constraint. Otherwise LLM
    review may approve based on the matching "比" while silently inheriting
    the extra "枚" into the page. We only check when target has at least
    one teaching_content constraint (i.e. it's a teaching-fact target).
    """

    target_norm = normalize_constraints(target_constraints)
    candidate_norm = normalize_constraints(candidate_constraints)
    target_tc = [
        item for item in target_norm
        if _clean_text(item.get("kind")) in STRICT_KNOWLEDGE_CONSTRAINT_KINDS
        and _clean_text(item.get("subtype")).casefold() == "teaching_content"
    ]
    if not target_tc:
        return []
    target_values_by_kind: dict[str, set[str]] = {}
    for item in target_tc:
        kind = _clean_text(item.get("kind"))
        target_values_by_kind.setdefault(kind, set()).add(
            _normalize_constraint_value(kind, item.get("value"))
        )

    extras: list[dict[str, Any]] = []
    for c in candidate_norm:
        kind = _clean_text(c.get("kind"))
        if kind not in STRICT_KNOWLEDGE_CONSTRAINT_KINDS:
            continue
        if _clean_text(c.get("subtype")).casefold() != "teaching_content":
            continue
        target_values = target_values_by_kind.get(kind, set())
        value_norm = _normalize_constraint_value(kind, c.get("value"))
        if value_norm in target_values:
            continue
        extras.append(c)
    return extras


def subject_coverage_undercoverage(
    target_constraints: list[dict[str, Any]],
    candidate_constraints: list[dict[str, Any]],
    *,
    min_group_size: int = 2,
) -> list[dict[str, Any]]:
    """Detect target subject groups where candidate covers fewer than ⌈N/2⌉ members.

    Groups target imp>=1 constraints by (kind, importance). When a group has
    N>=min_group_size members, count how many are covered by a same-kind
    light-match in the candidate. If matched < ceil(N/2), the candidate is
    missing too much of the page's intended subject set — the reuse should be
    rejected rather than sent to LLM review, since LLM review can't fabricate
    the missing subjects out of an image that doesn't contain them.
    """

    active = [item for item in normalize_constraints(target_constraints) if _constraint_importance(item) >= 1]
    if not active:
        return []

    groups: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for item in active:
        key = (item["kind"], _constraint_importance(item))
        groups.setdefault(key, []).append(item)

    candidates_norm = normalize_constraints(candidate_constraints)
    undercovered: list[dict[str, Any]] = []
    for (kind, importance), members in groups.items():
        if len(members) < min_group_size:
            continue
        same_kind = [c for c in candidates_norm if c["kind"] == kind]
        matched_values: list[str] = []
        missing_values: list[str] = []
        for target in members:
            if any(_light_constraint_match_method(target, c) for c in same_kind):
                matched_values.append(target["value"])
            else:
                missing_values.append(target["value"])
        required = (len(members) + 1) // 2
        if len(matched_values) < required:
            undercovered.append(
                {
                    "kind": kind,
                    "importance": importance,
                    "target_values": [t["value"] for t in members],
                    "matched_values": matched_values,
                    "missing_values": missing_values,
                    "matched_count": len(matched_values),
                    "required_count": required,
                    "group_size": len(members),
                }
            )
    return undercovered


def strong_constraints_exactly_covered(
    target_strong_constraints: list[dict[str, Any]],
    candidate_constraints: list[dict[str, Any]],
) -> bool:
    """True iff every imp>=2 target constraint has a same-kind light_match in
    candidate (exact / contains / generic→specific).

    Stronger signal than embedding similarity: callers can short-circuit
    score-gated LLM review when this returns True, because the candidate
    materially carries every gating element the target asked for. This
    protects exact text/teaching_content / named-individual / strong-action
    candidates from being rejected on text-metadata similarity alone — the
    failure mode behind several observed "exact match silently rejected"
    cases (titled artwork, stroke-order glyph, named-author portrait).
    """

    target_norm = normalize_constraints(target_strong_constraints)
    if not target_norm:
        return False
    candidate_norm = normalize_constraints(candidate_constraints)
    for required in target_norm:
        if _constraint_importance(required) < 2:
            continue
        same_kind = [c for c in candidate_norm if c["kind"] == required["kind"]]
        if not same_kind:
            return False
        if not any(_light_constraint_match_method(required, c) for c in same_kind):
            return False
    return True


def target_narrative_undercoverage(
    target_constraints: list[dict[str, Any]],
    candidate_constraints: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return target imp>=1 narrative constraints the candidate fails to cover.

    Counterpart to ``candidate_extra_strong_constraints`` for the imp=1 layer
    on the target side. A constraint counts as narrative-binding when its
    subtype is in NARRATIVE_BINDING_SUBTYPES (specific entities, story
    scenes, narrative emotions, teaching carriers/facts/content). When the
    target requests such a constraint at imp>=1 but the candidate has no
    same-kind light_match, the candidate is materially missing a narrative
    element the page depends on. The strong-cover short-circuit must not
    bypass these mismatches: imp=2 cover does not imply narrative cover.
    """

    target_norm = normalize_constraints(target_constraints)
    candidate_norm = normalize_constraints(candidate_constraints)
    missing: list[dict[str, Any]] = []
    for t in target_norm:
        if _constraint_importance(t) < 1:
            continue
        subtype = _clean_text(t.get("subtype")).casefold()
        if subtype not in NARRATIVE_BINDING_SUBTYPES:
            continue
        same_kind = [c for c in candidate_norm if c["kind"] == t["kind"]]
        if any(_light_constraint_match_method(t, c) for c in same_kind):
            continue
        missing.append(t)
    return missing


def candidate_extra_strong_constraints(
    target_constraints: list[dict[str, Any]],
    candidate_constraints: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return candidate strong constraints not covered by target.

    "Strong" here means subtype in CANDIDATE_EXTRA_STRONG_SUBTYPES with
    importance>=2. "Not covered" means target has no same-kind same-value
    (light_match) constraint at any importance. These extras indicate the
    candidate is narratively more specific than the target asked for —
    reusing it would inject content the target didn't request.
    """

    target_all = normalize_constraints(target_constraints)
    candidate_norm = normalize_constraints(candidate_constraints)
    extras: list[dict[str, Any]] = []
    for c in candidate_norm:
        if _constraint_importance(c) < 2:
            continue
        subtype = _clean_text(c.get("subtype")).casefold()
        if subtype not in CANDIDATE_EXTRA_STRONG_SUBTYPES:
            continue
        same_kind = [t for t in target_all if t["kind"] == c["kind"]]
        if any(_light_constraint_match_method(t, c) for t in same_kind):
            continue
        extras.append(c)
    return extras


def _coerce_importance(value: Any, *, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(0, min(2, number))


def _coerce_confidence(value: Any, *, default: float) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = default
    return round(_clamp(score), 4)


def _normalize_constraint_kind(value: Any) -> str:
    return _clean_text(value)


def derive_reuse_level_from_constraints(
    constraints: list[dict[str, Any]],
    asset_category: str | None = None,
) -> str:
    """Derive reuse_level from asset_category + constraints.

    Strictness comes from two narrow signals only:
      * teaching content (text/math/physics imp=2)
      * named individual entity (subtype=named_individual/species_instance, imp=2)

    Generic decorative categories are forced to loose. The legacy
    'three strong constraints -> strict' rule is removed because it
    over-penalizes role/scene/emotion combinations that are not
    actually unsafe to reuse.
    """

    category = _clean_text(asset_category).casefold()
    if category in FORCED_LOOSE_CATEGORIES:
        return "loose"

    normalized = normalize_constraints(constraints)
    strong_constraints = [
        item for item in normalized if _constraint_importance(item) >= 2
    ]
    has_strict_knowledge_constraint = any(
        _clean_text(item.get("kind")) in STRICT_KNOWLEDGE_CONSTRAINT_KINDS
        for item in strong_constraints
    )
    has_named_individual = any(
        _clean_text(item.get("kind")) == "entity"
        and _clean_text(item.get("subtype")).casefold() in NAMED_INDIVIDUAL_SUBTYPES
        for item in strong_constraints
    )
    if has_strict_knowledge_constraint or has_named_individual:
        return "strict"
    if strong_constraints:
        return "medium"

    if any(_constraint_importance(item) >= 1 for item in normalized):
        return "medium"
    return "loose"


def _copy_constraint_dict(item: dict[str, Any]) -> dict[str, Any]:
    return dict(item)


def _constraint_importance(constraint: dict[str, Any]) -> int:
    return _coerce_importance(_dict_value(constraint, "importance"), default=0)


def _active_constraints(value: Any) -> list[dict[str, Any]]:
    return [
        item for item in normalize_constraints(value)
        if _constraint_importance(item) >= 1
    ]


def _strong_constraints(value: Any) -> list[dict[str, Any]]:
    return [
        item for item in normalize_constraints(value)
        if _constraint_importance(item) >= 2
    ]


def _dict_value(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else None


def normalize_reuse_policy_fields(asset: dict[str, Any]) -> dict[str, Any]:
    """Return schema-valid simplified reuse metadata for an asset."""

    if _clean_text(asset.get("asset_kind")) == "background":
        return {
            "reuse_level": "loose",
            "asset_category": "unknown",
            "constraints": [],
            "generic_support_allowed": True,
        }

    metadata = normalize_asset_metadata(asset)
    constraints = metadata.constraints
    asset_category = _clean_text(asset.get("asset_category")) or metadata.asset_category
    if asset_category not in ASSET_CATEGORIES:
        asset_category = DEFAULT_POLICY["asset_category"]

    reuse_level = derive_reuse_level_from_constraints(constraints, asset_category)
    generic_support_allowed = reuse_level == "loose"

    return {
        "reuse_level": reuse_level,
        "asset_category": asset_category,
        "constraints": normalize_constraints(constraints),
        "generic_support_allowed": generic_support_allowed,
    }


def _clean_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        text = _clean_text(value)
        return [text] if text else []
    results: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _clean_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        results.append(text)
    return results


def reuse_threshold_for_target(target: dict[str, Any], explicit_threshold: float | None = None) -> float:
    if explicit_threshold is not None:
        try:
            return _clamp(float(explicit_threshold))
        except (TypeError, ValueError):
            pass

    if _clean_text(target.get("asset_kind")) == "background":
        return BACKGROUND_REUSE_THRESHOLD

    policy = normalize_reuse_policy_fields(target)
    reuse_level = policy["reuse_level"]
    threshold = PAGE_IMAGE_REUSE_THRESHOLDS.get(reuse_level, PAGE_IMAGE_REUSE_THRESHOLDS["medium"])
    return round(_clamp(threshold, minimum=0.30, maximum=0.75), 4)


def evaluate_reuse_filter(
    target: dict[str, Any],
    candidate: dict[str, Any],
    score_details: dict[str, Any] | None = None,
    *,
    threshold: float | None = None,
) -> dict[str, Any]:
    score_details = score_details or {}
    df_lookup = score_details.get("df_ratio_lookup")
    df_token = _CURRENT_DF_RATIO.set(df_lookup if isinstance(df_lookup, dict) else None)
    try:
        return _evaluate_reuse_filter_impl(
            target, candidate, score_details, threshold=threshold
        )
    finally:
        _CURRENT_DF_RATIO.reset(df_token)


def _evaluate_reuse_filter_impl(
    target: dict[str, Any],
    candidate: dict[str, Any],
    score_details: dict[str, Any],
    *,
    threshold: float | None,
) -> dict[str, Any]:
    target_kind = _clean_text(target.get("asset_kind"))
    candidate_kind = _clean_text(candidate.get("asset_kind"))
    score = _score_from_details(score_details)
    threshold_used = reuse_threshold_for_target(target, explicit_threshold=threshold)
    score_gap = score - threshold_used

    if target_kind != candidate_kind:
        return _result(
            "reject",
            "asset_kind_mismatch",
            confidence=1.0,
            threshold=threshold_used,
            score_gap=score_gap,
        )

    transform_policy = score_details.get("transform_policy") if isinstance(score_details.get("transform_policy"), dict) else {}
    if _clean_text(transform_policy.get("decision")) == "reject":
        return _result(
            "reject",
            "aspect_transform_rejected",
            confidence=1.0,
            threshold=threshold_used,
            score_gap=score_gap,
        )

    if target_kind == "background":
        same_theme = (
            _clean_text(target.get("theme")) != ""
            and _clean_text(target.get("theme")) == _clean_text(candidate.get("theme"))
        )
        if same_theme:
            bg_embedding_score = _embedding_score_from_details(score_details)
            if bg_embedding_score >= BACKGROUND_SAME_THEME_HIGH_EMBEDDING_FLOOR:
                threshold_used = min(threshold_used, BACKGROUND_SAME_THEME_HIGH_EMBEDDING_THRESHOLD)
            else:
                threshold_used = min(threshold_used, BACKGROUND_SAME_THEME_THRESHOLD)
            score_gap = score - threshold_used
        if score >= threshold_used:
            return _result(
                "full_match",
                "background_score_above_threshold",
                confidence=0.9,
                threshold=threshold_used,
                score_gap=score_gap,
            )
        return _result(
            "reject",
            "background_score_below_threshold",
            confidence=0.9,
            threshold=threshold_used,
            score_gap=score_gap,
        )

    target_policy = normalize_reuse_policy_fields(target)
    candidate_policy = normalize_reuse_policy_fields(candidate)
    target_level = target_policy["reuse_level"]
    target_constraints = _active_constraints(target_policy.get("constraints"))
    # Candidate side: keep ALL constraints (including imp=0) so that a target
    # imp>=1 constraint can match a candidate's imp=0 entry with the same value.
    # Without this, "candidate has 笔 imp=0" would be invisible to filtering,
    # producing a spurious "missing/conflict" when target requires 笔 imp=2.
    candidate_constraints = normalize_constraints(candidate_policy.get("constraints"))
    target_strong_constraints = _strong_constraints(target_policy.get("constraints"))
    candidate_strong_constraints = _strong_constraints(candidate_policy.get("constraints"))
    semantic_signal = _has_semantic_reuse_signal(score_details, score)
    embedding_score = _embedding_score_from_details(score_details)
    accepted_by = _clean_text(score_details.get("accepted_by"))

    def high_embedding_review_result(reason: str, threshold_value: float) -> dict[str, Any]:
        return _result(
            "llm_review",
            reason,
            review_items=[
                {
                    "decision": "llm_review",
                    "kind": "embedding",
                    "reason": reason,
                    "embedding_score": round(float(embedding_score), 4),
                    "threshold": round(float(threshold_value), 4),
                }
            ],
            confidence=0.5,
            threshold=threshold_used,
            score_gap=score_gap,
            target_policy=target_policy,
            candidate_policy=candidate_policy,
        )

    def embedding_floor_review_result(reason: str) -> dict[str, Any]:
        floor = _auto_accept_embedding_floor(target_policy, candidate_policy)
        return high_embedding_review_result(reason, floor)

    def full_match_with_precision_gate(reason: str, *, confidence: float) -> dict[str, Any]:
        """Build a full_match result, but block when score_details.precision_signal
        is explicitly False.

        precision_signal is set upstream by the scoring stage: True when
        target and candidate share at least one imp>=1 constraint or
        discriminative core_keyword, False otherwise. Absent (None) →
        legacy behavior, no block.

        Block behavior depends on target reuse_level:
          * loose / forced_loose decorative slots → downgrade to llm_review
            so the LLM can rescue genuine decorative reuse.
          * medium / strict → reject outright. precision_signal=False here
            means the shared score is built on non-discriminative
            keywords (e.g. "插画 教学 卡通") with no concrete constraint
            overlap; the LLM has no extra evidence to overturn that, so
            calling it is wasted budget (empirically 4.8% accept rate on
            these candidates).
        """
        precision_signal = score_details.get("precision_signal")
        if precision_signal is False:
            target_level = target_policy.get("reuse_level")
            if target_level == "loose":
                return _result(
                    "llm_review",
                    "no_precision_signal",
                    review_items=[
                        {
                            "decision": "llm_review",
                            "kind": "precision_signal",
                            "reason": "no_precision_signal",
                            "downgraded_from": reason,
                        }
                    ],
                    confidence=0.5,
                    threshold=threshold_used,
                    score_gap=score_gap,
                    target_policy=target_policy,
                    candidate_policy=candidate_policy,
                )
            return _result(
                "reject",
                "no_precision_signal",
                confidence=0.85,
                threshold=threshold_used,
                score_gap=score_gap,
                target_policy=target_policy,
                candidate_policy=candidate_policy,
            )
        return _result(
            "full_match",
            reason,
            confidence=confidence,
            threshold=threshold_used,
            score_gap=score_gap,
            target_policy=target_policy,
            candidate_policy=candidate_policy,
        )

    # Strong-constraint exact-cover short-circuit.
    #
    # When every imp>=2 target constraint is light-matched (exact / contains /
    # generic→specific) by the candidate, and the candidate adds no extra
    # narrative-binding strong constraints the target didn't request, the
    # candidate materially carries the page's full gating content. The
    # candidate already passed the retrieval threshold to reach this function,
    # so we can sidestep the score-gated LLM review (which judges prose
    # similarity and over-rejects exact-content matches — the failure mode
    # observed for art-word titles, exact stroke-order glyphs, and
    # named-individual portraits).
    #
    # Carve-out: when any imp>=2 constraint is text/math/physics, do NOT
    # short-circuit to full_match. These are exact-content kinds where the
    # downstream VLM-less pipeline can't independently verify the candidate
    # image actually renders the right glyph/formula. Instead, downgrade the
    # outcome to llm_review with a distinguished reason so the reviewer
    # threshold can be relaxed (text metadata is exact-matched; the LLM only
    # needs to confirm visual presence rather than judge prose suitability).
    if target_strong_constraints and strong_constraints_exactly_covered(
        target_strong_constraints, candidate_constraints
    ):
        extras_pre = candidate_extra_strong_constraints(
            target_constraints, candidate_constraints
        )
        teaching_extras_pre = extra_teaching_content_constraints(
            target_strong_constraints, candidate_constraints
        )
        # Reverse-direction narrative reflux: even when every imp>=2 target
        # constraint is covered, the candidate must also reflect the
        # target's imp=1 narrative bindings (story scene, narrative emotion,
        # named/species entity, teaching carrier/fact/content). Without this
        # check the short-circuit silently accepts candidates that match the
        # central subject but drop the page's surrounding story — e.g.
        # target "frog catching pests in rice paddy" matched by a generic
        # frog portrait because only the frog is imp=2.
        narrative_missing_pre = target_narrative_undercoverage(
            target_constraints, candidate_constraints
        )
        if narrative_missing_pre:
            return _result(
                "llm_review",
                "target_narrative_undercoverage",
                review_items=[
                    _constraint_review_item(
                        item,
                        [],
                        "target_narrative_undercoverage",
                        side="target",
                    )
                    for item in narrative_missing_pre
                ],
                confidence=0.55,
                threshold=threshold_used,
                score_gap=score_gap,
                target_policy=target_policy,
                candidate_policy=candidate_policy,
            )
        if not extras_pre and not teaching_extras_pre:
            has_strict_knowledge_constraint = any(
                _clean_text(item.get("kind")) in STRICT_KNOWLEDGE_CONSTRAINT_KINDS
                for item in target_strong_constraints
            )
            if not has_strict_knowledge_constraint:
                return full_match_with_precision_gate(
                    "strong_constraints_exact_covered",
                    confidence=0.85,
                )
            return _result(
                "llm_review",
                "strict_text_exact_covered_review",
                review_items=[
                    {
                        "decision": "llm_review",
                        "kind": "strong_cover",
                        "reason": "strict_text_exact_covered_review",
                        "covered_kinds": sorted({
                            _clean_text(item.get("kind"))
                            for item in target_strong_constraints
                            if _clean_text(item.get("kind"))
                            in STRICT_KNOWLEDGE_CONSTRAINT_KINDS
                        }),
                    }
                ],
                confidence=0.7,
                threshold=threshold_used,
                score_gap=score_gap,
                target_policy=target_policy,
                candidate_policy=candidate_policy,
            )

    if accepted_by in SCORE_GATE_LLM_REVIEW_REASONS:
        return high_embedding_review_result(accepted_by, threshold_used)

    # Decorative loose path: when target asset_category is in
    # FORCED_LOOSE_CATEGORIES, the asset is by spec decorative — its
    # constraints are descriptive metadata, not gating requirements. Skip
    # compare_constraints entirely; still keep candidate_extra_strong (to
    # prevent injecting named_individual / teaching_content / teaching_carrier
    # into a decorative slot) and precision_signal gating.
    target_category = _clean_text(target_policy.get("asset_category")).casefold()
    target_is_forced_loose = target_category in FORCED_LOOSE_CATEGORIES

    if target_is_forced_loose:
        extras = candidate_extra_strong_constraints(
            target_constraints,
            candidate_constraints,
        )
        if extras:
            return _result(
                "llm_review",
                "candidate_extra_strong_constraints",
                review_items=[
                    _constraint_review_item(
                        item,
                        [],
                        "candidate_extra_strong",
                        side="candidate",
                    )
                    for item in extras
                ],
                confidence=0.55,
                threshold=threshold_used,
                score_gap=score_gap,
                target_policy=target_policy,
                candidate_policy=candidate_policy,
            )
        if score_gap >= 0 or semantic_signal:
            if score_gap >= 0 and _requires_embedding_floor_review(
                target_policy, candidate_policy, embedding_score
            ):
                return embedding_floor_review_result("embedding_below_auto_accept_floor")
            return full_match_with_precision_gate(
                "decorative_loose_match",
                confidence=0.75 if score_gap >= 0 else 0.65,
            )
        return _result(
            "reject",
            "similarity_below_threshold",
            confidence=0.85,
            threshold=threshold_used,
            score_gap=score_gap,
            target_policy=target_policy,
            candidate_policy=candidate_policy,
        )

    if target_level != "strict":
        medium_missing, medium_conflicts = compare_constraints(
            target_constraints,
            candidate_constraints,
            strict_target=False,
        )
        if medium_conflicts:
            _missing, unresolved_conflicts, conflict_reviews = compare_strong_constraints(
                target_strong_constraints,
                candidate_constraints,
                score_details=score_details,
            )
            actionable_reviews = [
                item for item in conflict_reviews
                if _clean_text(item.get("reason")) != "missing_constraint_embedding"
            ]
            if actionable_reviews:
                return _result(
                    "llm_review",
                    "medium_constraints_require_llm_review",
                    review_items=actionable_reviews,
                    confidence=0.55,
                    threshold=threshold_used,
                    score_gap=score_gap,
                    target_policy=target_policy,
                    candidate_policy=candidate_policy,
                )
            if unresolved_conflicts:
                return _result(
                    "reject",
                    "medium_constraints_conflict",
                    conflicts=unresolved_conflicts,
                    confidence=0.95,
                    threshold=threshold_used,
                    score_gap=score_gap,
                    target_policy=target_policy,
                    candidate_policy=candidate_policy,
                )
            if conflict_reviews:
                return _result(
                    "reject",
                    "medium_constraints_conflict",
                    conflicts=medium_conflicts,
                    confidence=0.95,
                    threshold=threshold_used,
                    score_gap=score_gap,
                    target_policy=target_policy,
                    candidate_policy=candidate_policy,
                )
            if not target_strong_constraints:
                return _result(
                    "reject",
                    "medium_constraints_conflict",
                    conflicts=medium_conflicts,
                    confidence=0.85,
                    threshold=threshold_used,
                    score_gap=score_gap,
                    target_policy=target_policy,
                    candidate_policy=candidate_policy,
                )
        undercovered_groups = subject_coverage_undercoverage(
            target_constraints,
            candidate_constraints,
        )
        if undercovered_groups:
            return _result(
                "reject",
                "subject_coverage_undercoverage",
                conflicts=[
                    {
                        "kind": group["kind"],
                        "target": "+".join(group["target_values"]),
                        "candidate_values": group["matched_values"],
                        "missing_values": group["missing_values"],
                        "matched_count": group["matched_count"],
                        "required_count": group["required_count"],
                        "group_size": group["group_size"],
                    }
                    for group in undercovered_groups
                ],
                confidence=0.9,
                threshold=threshold_used,
                score_gap=score_gap,
                target_policy=target_policy,
                candidate_policy=candidate_policy,
            )
        medium_strong_missing = [item for item in medium_missing if _is_specific_constraint(item)]
        if medium_missing and not (semantic_signal and not medium_strong_missing):
            return _result(
                "llm_review",
                "medium_constraints_require_llm_review",
                review_items=[
                    _constraint_review_item(item, [], "missing_same_kind", side="target")
                    for item in medium_missing
                ],
                confidence=0.55,
                threshold=threshold_used,
                score_gap=score_gap,
                target_policy=target_policy,
                candidate_policy=candidate_policy,
            )
        extras = candidate_extra_strong_constraints(
            target_constraints,
            candidate_constraints,
        )
        if extras:
            return _result(
                "llm_review",
                "candidate_extra_strong_constraints",
                review_items=[
                    _constraint_review_item(
                        item,
                        [],
                        "candidate_extra_strong",
                        side="candidate",
                    )
                    for item in extras
                ],
                confidence=0.55,
                threshold=threshold_used,
                score_gap=score_gap,
                target_policy=target_policy,
                candidate_policy=candidate_policy,
            )
        if (
            score_gap < 0
            and embedding_score >= MEDIUM_EMBEDDING_REVIEW_THRESHOLD
            and (accepted_by in {"medium_embedding_review", "embedding_high_review"} or score <= 0)
        ):
            return high_embedding_review_result(
                "medium_embedding_high_keyword_below_threshold",
                MEDIUM_EMBEDDING_REVIEW_THRESHOLD,
            )
        if score_gap >= 0 or semantic_signal:
            if score_gap >= 0 and _requires_embedding_floor_review(target_policy, candidate_policy, embedding_score):
                return embedding_floor_review_result("embedding_below_auto_accept_floor")
            return full_match_with_precision_gate(
                "medium_similarity_threshold_match",
                confidence=0.8 if score_gap >= 0 else 0.7,
            )
        return _result(
            "reject",
            "similarity_below_threshold",
            confidence=0.85,
            threshold=threshold_used,
            score_gap=score_gap,
            target_policy=target_policy,
            candidate_policy=candidate_policy,
        )

    if target_level == "strict":
        if score_gap < 0 and not semantic_signal:
            if accepted_by == "strict_semantic_gray_review":
                return high_embedding_review_result(
                    "strict_semantic_gray_review",
                    STRICT_SEMANTIC_GRAY_REVIEW_THRESHOLD,
                )
            if embedding_score >= STRICT_EMBEDDING_REVIEW_THRESHOLD:
                return high_embedding_review_result(
                    "strict_embedding_high_keyword_below_threshold",
                    STRICT_EMBEDDING_REVIEW_THRESHOLD,
                )
            return _result(
                "reject",
                "strict_similarity_below_threshold",
                confidence=0.9,
                threshold=threshold_used,
                score_gap=score_gap,
                target_policy=target_policy,
                candidate_policy=candidate_policy,
            )
        if not target_constraints and not candidate_constraints:
            if score_gap >= 0 and _requires_embedding_floor_review(target_policy, candidate_policy, embedding_score):
                return embedding_floor_review_result("embedding_below_auto_accept_floor")
            return full_match_with_precision_gate(
                "strict_unconstrained_similarity_match",
                confidence=0.75 if score_gap >= 0 else 0.65,
            )
        teaching_content_extras = extra_teaching_content_constraints(
            target_strong_constraints,
            candidate_constraints,
        )
        if teaching_content_extras:
            return _result(
                "llm_review",
                "candidate_extra_teaching_content",
                review_items=[
                    _constraint_review_item(
                        item,
                        [],
                        "candidate_extra_teaching_content",
                        side="candidate",
                    )
                    for item in teaching_content_extras
                ],
                confidence=0.55,
                threshold=threshold_used,
                score_gap=score_gap,
                target_policy=target_policy,
                candidate_policy=candidate_policy,
            )
        missing, conflicts, reviews = compare_strong_constraints(
            target_strong_constraints,
            candidate_constraints,
            score_details=score_details,
        )
        strict_missing: list[dict[str, Any]] = list(missing)
        strict_conflicts: list[dict[str, Any]] = _dedupe_conflicts(conflicts)
        strict_reviews: list[dict[str, Any]] = _dedupe_review_items(reviews)
        if strict_conflicts:
            return _result(
                "reject",
                "strict_constraints_conflict",
                conflicts=strict_conflicts,
                confidence=0.95,
                threshold=threshold_used,
                score_gap=score_gap,
                target_policy=target_policy,
                candidate_policy=candidate_policy,
            )
        if strict_reviews:
            return _result(
                "llm_review",
                "strict_constraints_require_llm_review",
                review_items=strict_reviews,
                confidence=0.55,
                threshold=threshold_used,
                score_gap=score_gap,
                target_policy=target_policy,
                candidate_policy=candidate_policy,
            )
        if strict_missing:
            return _result(
                "reject",
                "strict_constraints_missing",
                missing=strict_missing,
                confidence=0.9,
                threshold=threshold_used,
                score_gap=score_gap,
                target_policy=target_policy,
                candidate_policy=candidate_policy,
            )
        if score_gap >= 0 and _requires_embedding_floor_review(target_policy, candidate_policy, embedding_score):
            return embedding_floor_review_result("embedding_below_auto_accept_floor")
        return _result(
            "full_match",
            "strict_constraints_covered",
            confidence=0.9,
            threshold=threshold_used,
            score_gap=score_gap,
            target_policy=target_policy,
            candidate_policy=candidate_policy,
        )

    return _result(
        "full_match" if score_gap >= 0 or semantic_signal else "reject",
        "constraint_similarity_match" if score_gap >= 0 or semantic_signal else "similarity_below_threshold",
        confidence=0.75 if score_gap >= 0 or semantic_signal else 0.85,
        threshold=threshold_used,
        score_gap=score_gap,
        target_policy=target_policy,
        candidate_policy=candidate_policy,
    )


def evaluate_aspect_transform(target: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Choose a safe transform mode and score penalty for aspect-ratio mismatch."""

    candidate_label = _clean_text(candidate.get("aspect_ratio"))
    target_label = _clean_text(target.get("aspect_ratio"))
    candidate_ratio = _parse_aspect_ratio(candidate_label)
    target_ratio = _parse_aspect_ratio(target_label)
    if candidate_ratio <= 0 or target_ratio <= 0:
        return {
            "decision": "accept",
            "mode": "copy",
            "crop_loss": 0.0,
            "transform_penalty": 0.0,
            "candidate_aspect_ratio": candidate_label,
            "target_aspect_ratio": target_label,
            "reason": "missing_or_invalid_aspect_ratio",
        }

    loss = _crop_loss(candidate_ratio, target_ratio)
    reversed_orientation = _orientation(candidate_ratio) != _orientation(target_ratio) and "square" not in {
        _orientation(candidate_ratio),
        _orientation(target_ratio),
    }
    target_policy = normalize_reuse_policy_fields(target)
    reuse_level = target_policy["reuse_level"]
    role = _clean_text(target.get("role") or candidate.get("role"))
    has_constraints = bool(_active_constraints(target_policy.get("constraints")))
    asset_kind = _clean_text(target.get("asset_kind"))

    if loss <= 0.02:
        return _transform_result(
            "accept",
            "copy",
            loss,
            0.0,
            candidate_label,
            target_label,
            "aspect_ratio_aligned",
        )

    if asset_kind == "background":
        if loss <= 0.05:
            return _transform_result("accept", "micro_stretch", loss, 0.01, candidate_label, target_label, "background_micro_stretch")
        if loss <= 0.12:
            return _transform_result("penalize", "cover_crop", loss, 0.02, candidate_label, target_label, "background_light_crop")
        if loss <= 0.25:
            return _transform_result("penalize", "blur_pad", loss, 0.06, candidate_label, target_label, "background_blur_pad")
        if loss <= 0.35 and not reversed_orientation:
            return _transform_result("penalize", "blur_pad", loss, 0.10, candidate_label, target_label, "background_high_pad")
        return _transform_result("reject", "copy", loss, 0.18, candidate_label, target_label, "background_aspect_mismatch_too_large")

    if role == "hero" and loss > 0.25:
        return _transform_result("reject", "copy", loss, 0.18, candidate_label, target_label, "hero_aspect_mismatch_too_large")
    if role == "hero" and loss > 0.12:
        return _transform_result("penalize", "contain_pad", loss, 0.10, candidate_label, target_label, "hero_content_preserving_pad")

    if role == "icon":
        if loss <= 0.12:
            return _transform_result("penalize", "contain_pad", loss, 0.04, candidate_label, target_label, "icon_content_preserving_pad")
        if loss <= 0.25 and not reversed_orientation:
            return _transform_result("penalize", "contain_pad", loss, 0.09, candidate_label, target_label, "icon_medium_pad")
        return _transform_result("reject", "copy", loss, 0.18, candidate_label, target_label, "icon_aspect_mismatch_too_large")

    if reuse_level == "strict" or has_constraints:
        if loss <= 0.05:
            return _transform_result("accept", "copy", loss, 0.0, candidate_label, target_label, "strict_small_mismatch")
        if loss <= 0.12 and not reversed_orientation:
            return _transform_result("penalize", "contain_pad", loss, 0.05, candidate_label, target_label, "strict_content_preserving_pad")
        if loss <= 0.25 and not reversed_orientation:
            return _transform_result("penalize", "contain_pad", loss, 0.10, candidate_label, target_label, "strict_content_preserving_medium_pad")
        return _transform_result("reject", "copy", loss, 0.18, candidate_label, target_label, "strict_aspect_mismatch_too_large")

    if loss <= 0.05:
        return _transform_result("accept", "copy", loss, 0.0, candidate_label, target_label, "unknown_small_mismatch")
    if loss <= 0.12:
        return _transform_result("penalize", "contain_pad", loss, 0.05, candidate_label, target_label, "unknown_light_pad")
    if loss <= 0.25 and not reversed_orientation:
        return _transform_result("penalize", "contain_pad", loss, 0.10, candidate_label, target_label, "unknown_medium_pad")
    return _transform_result("reject", "copy", loss, 0.18, candidate_label, target_label, "unknown_aspect_mismatch_too_large")


def compare_constraints(
    target_constraints: list[dict[str, Any]],
    candidate_constraints: list[dict[str, Any]],
    *,
    strict_target: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    missing: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    for target in target_constraints:
        same_kind = [candidate for candidate in candidate_constraints if candidate["kind"] == target["kind"]]
        if not same_kind:
            missing.append(target)
            continue
        if any(_constraints_equivalent(target, candidate) for candidate in same_kind):
            continue
        if _is_specific_constraint(target) or any(_is_specific_constraint(candidate) for candidate in same_kind):
            conflicts.append(
                {
                    "kind": target["kind"],
                    "target": target["value"],
                    "candidate_values": [candidate["value"] for candidate in same_kind],
                }
            )
        else:
            missing.append(target)

    if strict_target and target_constraints:
        target_kinds = {item["kind"] for item in target_constraints if _is_specific_constraint(item)}
        for candidate in candidate_constraints:
            if candidate["kind"] not in target_kinds or not _is_specific_constraint(candidate):
                continue
            if any(
                target["kind"] == candidate["kind"] and _constraints_equivalent(target, candidate)
                for target in target_constraints
            ):
                continue
            conflicts.append(
                {
                    "kind": candidate["kind"],
                    "target": "",
                    "candidate_values": [candidate["value"]],
                }
            )

    return missing, _dedupe_conflicts(conflicts)


def compare_strong_constraints(
    required_constraints: list[dict[str, Any]],
    available_constraints: list[dict[str, Any]],
    *,
    score_details: dict[str, Any] | None = None,
    side: str = "target",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Require strict constraints to be covered, rejected, or LLM-reviewed."""

    missing: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    for required in required_constraints:
        same_kind = [
            available
            for available in available_constraints
            if available["kind"] == required["kind"]
        ]
        if not same_kind:
            reviews.append(_constraint_review_item(required, [], "missing_same_kind", side=side))
            continue
        match_results = [
            _strict_constraint_match_result(required, available, score_details or {}, side=side)
            for available in same_kind
        ]
        if any(item["decision"] == "matched" for item in match_results):
            continue
        review_results = [item for item in match_results if item["decision"] == "llm_review"]
        if review_results:
            reviews.append(_best_review_result(required, review_results, side=side))
            continue
        conflicts.append(
            {
                "kind": required["kind"],
                "target": required["value"],
                "candidate_values": [available["value"] for available in same_kind],
            }
        )
    return missing, _dedupe_conflicts(conflicts), _dedupe_review_items(reviews)


def _constraints_equivalent(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left["kind"] != right["kind"]:
        return False
    light_method = _light_constraint_match_method(left, right)
    if light_method:
        return True
    if _constraint_importance(left) >= 2:
        return False
    left_value = _normalize_constraint_value(left["kind"], left["value"])
    right_value = _normalize_constraint_value(right["kind"], right["value"])
    return _soft_equivalent(left_value, right_value)


def _strict_constraint_match_result(
    required: dict[str, Any],
    available: dict[str, Any],
    score_details: dict[str, Any],
    *,
    side: str,
) -> dict[str, Any]:
    kind = _clean_text(required.get("kind"))
    light_method = _light_constraint_match_method(required, available)
    if light_method:
        if kind in LLM_REVIEW_REQUIRED_KINDS:
            return _constraint_review_item(
                required,
                [available],
                light_method,
                side=side,
                decision="llm_review",
            )
        return {"decision": "matched", "match_method": light_method}

    embedding_score = _constraint_embedding_score(required, available, score_details)
    if embedding_score is None:
        return _constraint_review_item(
            required,
            [available],
            "missing_constraint_embedding",
            side=side,
            decision="llm_review",
        )

    threshold = _constraint_embedding_threshold(required)
    if embedding_score >= threshold:
        method = "embedding_high"
        if kind in LLM_REVIEW_REQUIRED_KINDS:
            return _constraint_review_item(
                required,
                [available],
                method,
                side=side,
                decision="llm_review",
                embedding_score=embedding_score,
            )
        return {"decision": "matched", "match_method": method, "embedding_score": embedding_score}
    high, low = CONSTRAINT_EMBEDDING_THRESHOLDS.get(kind, (0.88, 0.76))
    if _constraint_importance(required) == 1 and embedding_score >= min(high, max(low, threshold - 0.04)):
        return _constraint_review_item(
            required,
            [available],
            "embedding_gray",
            side=side,
            decision="llm_review",
            embedding_score=embedding_score,
        )
    return {"decision": "failed", "match_method": "embedding_low", "embedding_score": embedding_score}


def _light_constraint_match_method(left: dict[str, Any], right: dict[str, Any]) -> str:
    """Asymmetric light match. ``left`` is the side asking (target / required),
    ``right`` is the side offering (candidate / available).

    Returns one of:
      * ``"exact"`` / ``"contains"`` — symmetric string overlap.
      * ``"generic_specific"`` — left is a generic class and right is a
        concrete instance of the same kind. A slide asking for "小动物"
        is satisfied by a specific 猫/狗/兔 image; not the reverse.

    DF-ratio cross-validation: when the candidate generic_specific branch
    would fire on a left value with a very low library DF ratio (below
    ``GENERIC_CLASS_MIN_DF_RATIO``), suppress the match. A truly generic
    class word is widely used across the library (and has a high DF
    ratio); a rare value labelled ``generic_class`` is almost certainly a
    metadata-extraction mistake on a specific term (e.g. "小猴子" tagged
    as generic_class). Without DF data (None) or when the library is too
    small to be statistically trusted (empty dict), the gate is skipped
    and legacy behavior preserved.
    """

    kind = _clean_text(left.get("kind"))
    if kind != _clean_text(right.get("kind")):
        return ""
    left_value = _normalize_constraint_value(kind, left.get("value"))
    right_value = _normalize_constraint_value(kind, right.get("value"))
    if not left_value or not right_value:
        return ""
    if left_value == right_value:
        return "exact"
    if min(len(left_value), len(right_value)) >= 2 and (left_value in right_value or right_value in left_value):
        return "contains"
    if _is_generic_class_constraint(left) and _is_specific_instance_constraint(right):
        if not _df_ratio_supports_generic(left_value):
            return ""
        return "generic_specific"
    return ""


def _df_ratio_supports_generic(value: str) -> bool:
    """Return True when the current library's DF ratio supports treating
    ``value`` as a real generic-class word.

    Tri-state semantics:
      * No DF data at all (None / empty dict) → legacy behavior, return True.
        Libraries below DF_RATIO_MIN_LIBRARY_SIZE intentionally yield an
        empty dict from the caller so this branch fires.
      * Value present in DF map with ratio below GENERIC_CLASS_MIN_DF_RATIO
        → return False (probable specific term mislabelled as generic_class).
      * Value present with ratio at/above the threshold → return True.
      * Value absent from the DF map (the library doesn't use it as a
        core keyword anywhere) → return False. This catches rare specific
        terms the metadata LLM may have mislabelled; the false-negative
        cost (rejecting a truly-generic-but-library-unused word) is nil
        because no candidate would carry it as a same-kind specific match
        anyway, so the generic→specific bridge would be vacuous.
    """

    df_ratio = _CURRENT_DF_RATIO.get(None)
    if not df_ratio:
        return True
    normalized = _clean_text(value).casefold()
    if not normalized:
        return True
    ratio = df_ratio.get(normalized)
    if ratio is None:
        return False
    return ratio >= GENERIC_CLASS_MIN_DF_RATIO


def _is_generic_class_constraint(constraint: dict[str, Any]) -> bool:
    """True when the constraint refers to a class rather than a specific instance.

    A constraint is generic if its subtype is generic_class/role OR its value
    contains a generic quantifier marker (多种/各种/几只/一群/...). The
    quantifier check is what makes "多种小动物" generic even when the value
    string happens to also contain a class noun.
    """

    subtype = _clean_text(constraint.get("subtype")).casefold()
    if subtype in GENERIC_CLASS_SUBTYPES:
        return True
    value = _clean_text(constraint.get("value"))
    return any(marker in value for marker in GENERIC_QUANTIFIER_MARKERS)


def _is_specific_instance_constraint(constraint: dict[str, Any]) -> bool:
    subtype = _clean_text(constraint.get("subtype")).casefold()
    return subtype in NAMED_INDIVIDUAL_SUBTYPES


def _constraint_embedding_threshold(constraint: dict[str, Any]) -> float:
    kind = _clean_text(constraint.get("kind"))
    importance = _constraint_importance(constraint)
    if importance <= 0:
        return 0.0
    high, low = CONSTRAINT_EMBEDDING_THRESHOLDS.get(kind, (0.88, 0.76))
    if importance >= 2:
        return high
    return low


def _constraint_embedding_score(
    required: dict[str, Any],
    available: dict[str, Any],
    score_details: dict[str, Any],
) -> float | None:
    kind = _clean_text(required.get("kind"))
    required_value = _normalize_constraint_value(kind, required.get("value"))
    available_value = _normalize_constraint_value(kind, available.get("value"))
    if not kind or not required_value or not available_value:
        return None
    items = score_details.get("constraint_embedding_scores")
    if not isinstance(items, list):
        return None
    best: float | None = None
    for item in items:
        if not isinstance(item, dict) or _normalize_constraint_kind(item.get("kind")) != kind:
            continue
        left = _normalize_constraint_value(kind, item.get("target"))
        right = _normalize_constraint_value(kind, item.get("candidate"))
        if {left, right} != {required_value, available_value}:
            continue
        try:
            score = float(item.get("score"))
        except (TypeError, ValueError):
            continue
        best = score if best is None else max(best, score)
    return best


def _constraint_review_item(
    required: dict[str, Any],
    available_constraints: list[dict[str, Any]],
    reason: str,
    *,
    side: str,
    decision: str = "llm_review",
    embedding_score: float | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "decision": decision,
        "kind": required.get("kind"),
        "value": required.get("value"),
        "importance": _constraint_importance(required),
        "side": side,
        "reason": reason,
        "candidate_values": [available.get("value") for available in available_constraints],
    }
    if embedding_score is not None:
        item["embedding_score"] = round(float(embedding_score), 4)
    return item


def _best_review_result(required: dict[str, Any], reviews: list[dict[str, Any]], *, side: str) -> dict[str, Any]:
    if not reviews:
        return _constraint_review_item(required, [], "missing_same_kind", side=side)
    def rank(item: dict[str, Any]) -> tuple[int, float]:
        reason = _clean_text(item.get("reason"))
        reason_rank = {"exact": 4, "contains": 3, "embedding_high": 2, "embedding_gray": 1}.get(reason, 0)
        try:
            score = float(item.get("embedding_score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        return reason_rank, score

    return max(reviews, key=rank)


def _parse_aspect_ratio(value: Any) -> float:
    text = _clean_text(value).lower()
    if not text:
        return 0.0
    parts = re.split(r"[:/x×]", text)
    if len(parts) == 2:
        try:
            width = float(parts[0])
            height = float(parts[1])
        except ValueError:
            return 0.0
        return width / height if width > 0 and height > 0 else 0.0
    try:
        value_float = float(text)
    except ValueError:
        return 0.0
    return value_float if value_float > 0 else 0.0


def _crop_loss(candidate_ratio: float, target_ratio: float) -> float:
    if candidate_ratio <= 0 or target_ratio <= 0:
        return 0.25
    return 1.0 - min(candidate_ratio, target_ratio) / max(candidate_ratio, target_ratio)


def _orientation(ratio: float) -> str:
    if ratio <= 0:
        return ""
    if abs(ratio - 1.0) <= 0.03:
        return "square"
    return "landscape" if ratio > 1.0 else "portrait"


def _transform_result(
    decision: str,
    mode: str,
    crop_loss: float,
    penalty: float,
    candidate_aspect_ratio: str,
    target_aspect_ratio: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "decision": decision,
        "mode": mode,
        "crop_loss": round(_clamp(crop_loss), 4),
        "transform_penalty": round(_clamp(penalty), 4),
        "candidate_aspect_ratio": candidate_aspect_ratio,
        "target_aspect_ratio": target_aspect_ratio,
        "reason": reason,
    }


def _soft_equivalent(left: str, right: str) -> bool:
    if left == right:
        return True
    if min(len(left), len(right)) >= 3 and (left in right or right in left):
        return True
    left_terms = set(re.findall(r"[a-z0-9]+", left))
    right_terms = set(re.findall(r"[a-z0-9]+", right))
    if not left_terms or not right_terms:
        return False
    return len(left_terms & right_terms) / max(1, len(left_terms | right_terms)) >= 0.6


def _normalize_constraint_value(kind: str, value: Any) -> str:
    text = _clean_text(value).casefold()
    text = re.sub(r"^[a-z_ -]{1,24}[:：]\s*", "", text)
    if kind in {"math", "physics", "text"}:
        text = re.sub(r"\s+", "", text)
    else:
        text = re.sub(r"\s+", " ", text)
    return text.strip(" ,;:()[]{}<>")


def _is_specific_constraint(constraint: dict[str, Any]) -> bool:
    return _constraint_importance(constraint) >= 2


def _embedding_score_from_details(score_details: dict[str, Any]) -> float:
    try:
        return float(score_details.get("embedding_score") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _auto_accept_embedding_floor(target_policy: dict[str, Any], candidate_policy: dict[str, Any]) -> float:
    if target_policy.get("reuse_level") == "strict" or candidate_policy.get("reuse_level") == "strict":
        return float(AUTO_ACCEPT_EMBEDDING_FLOORS["strict"])
    return 0.0


def _requires_embedding_floor_review(
    target_policy: dict[str, Any],
    candidate_policy: dict[str, Any],
    embedding_score: float,
) -> bool:
    levels = {target_policy.get("reuse_level"), candidate_policy.get("reuse_level")}
    if not ({"loose", "medium", "strict"} & levels):
        return False
    floor = _auto_accept_embedding_floor(target_policy, candidate_policy)
    return floor > 0 and embedding_score < floor


def _has_semantic_reuse_signal(score_details: dict[str, Any], score: float) -> bool:
    accepted_by = _clean_text(score_details.get("accepted_by"))
    if accepted_by in SEMANTIC_SIGNAL_ACCEPT_REASONS:
        return True
    embedding_score = _embedding_score_from_details(score_details)
    try:
        substring_score = float(score_details.get("substring_score") or 0.0)
    except (TypeError, ValueError):
        substring_score = 0.0
    return embedding_score >= SEMANTIC_EMBEDDING_ACCEPT_THRESHOLD and (
        score >= SEMANTIC_SCORE_FLOOR or substring_score > 0.0
    )


def _looks_like_style_or_quality_value(value: str) -> bool:
    normalized = _clean_text(value).casefold().replace(" ", "")
    if not normalized:
        return True
    style_markers = (
        "style",
        "prompt",
        "highquality",
        "hd",
        "4k",
        "16:9",
        "1:1",
        "watercolor",
        "cartoon",
        "illustration",
        "composition",
        "palette",
        "color",
        "插画",
        "教学插画",
        "教学配图",
        "配图",
        "课堂导入",
        "适合课堂导入",
        "页面功能",
        "高清",
        "高质量",
        "画风",
        "风格",
    )
    return normalized in style_markers


def _score_from_details(score_details: dict[str, Any]) -> float:
    for key in ("keyword_score", "score", "background_reuse_score"):
        try:
            value = float(score_details.get(key) or 0.0)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return _clamp(value)
    return 0.0


def _result(
    decision: str,
    reason: str,
    *,
    missing: list[dict[str, Any]] | None = None,
    conflicts: list[dict[str, Any]] | None = None,
    review_items: list[dict[str, Any]] | None = None,
    confidence: float = 0.5,
    threshold: float = 0.0,
    score_gap: float = 0.0,
    target_policy: dict[str, Any] | None = None,
    candidate_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "decision": decision,
        "reason": reason,
        "missing": missing or [],
        "conflicts": conflicts or [],
        "review_items": review_items or [],
        "confidence": _clamp(confidence),
        "threshold_used": round(float(threshold or 0.0), 4),
        "score_gap": round(float(score_gap or 0.0), 4),
    }
    if target_policy is not None:
        payload["target_policy"] = target_policy
    if candidate_policy is not None:
        payload["candidate_policy"] = candidate_policy
    return payload


def _dedupe_conflicts(conflicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    for item in conflicts:
        values = tuple(_clean_text(value) for value in item.get("candidate_values") or [])
        key = (_clean_text(item.get("kind")), _clean_text(item.get("target")), values)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _dedupe_review_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, tuple[str, ...], str]] = set()
    for item in items:
        values = tuple(_clean_text(value) for value in item.get("candidate_values") or [])
        key = (
            _clean_text(item.get("side")),
            _clean_text(item.get("kind")),
            _clean_text(item.get("value")),
            values,
            _clean_text(item.get("reason")),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clamp(value: float, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))
