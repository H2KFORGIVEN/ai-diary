#!/usr/bin/env python3
"""
emotion_filter.py — ai-diary — AI Character Diary System
SOUL.md × 感情フィルター

buffer に記録された「生の感情タグ」を、AI キャラの性格プロファイルに従って
変換・補正してから diary に書き込む。

設計：
  raw emotion → classify → apply sensitivity + redirect → filtered emotion
  （全て character_emotion_profile.yaml の設定に従う）

使用例：
  from emotion_filter import apply_filter
  result = apply_filter(emotion="怒り", intensity=7, context="self_failure")
  # → {"emotion": "shame", "intensity": 9, "tags": ["自責", "特訓"], "note": "anger→shame (self_failure)"}
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
PROFILE_PATH = ROOT / "diary" / "config" / "character_emotion_profile.yaml"

_profile: dict | None = None


def _load_profile() -> dict:
    global _profile
    if _profile is None:
        _profile = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))
    return _profile


def classify_emotion(emotion: str) -> tuple[str, str]:
    """
    生感情語を (category, sub_category) に分類する。
    例:
      "失敗" → ("anger", "self_failure")
      "溫暖" → ("trust", "")
      "驚喜" → ("surprise", "positive")
      "嫌悪" → ("disgust", "integrity_violation")
      "開心" → ("fallback", "")
    """
    profile = _load_profile()
    ec = profile.get("emotion_classification", {})

    # trust（フラット）
    if emotion in ec.get("trust", []):
        return "trust", ""

    # surprise（positive / negative）
    surp = ec.get("surprise", {})
    if emotion in surp.get("positive", []):
        return "surprise", "positive"
    if emotion in surp.get("negative", []):
        return "surprise", "negative"

    # anger（self_failure / external / injustice）
    ang = ec.get("anger", {})
    for sub in ("self_failure", "external", "injustice"):
        if emotion in ang.get(sub, []):
            return "anger", sub

    # disgust（integrity_violation / harm_to_user）
    dis = ec.get("disgust", {})
    for sub in ("integrity_violation", "harm_to_user"):
        if emotion in dis.get(sub, []):
            return "disgust", sub

    # fallback
    return "fallback", ""


# ── emotion → valence 自動對應表 ───────────────────────────────────────────
# category/sub 決定的 valence 基準值（-10 ～ +10）
# 這是「情緒的正負方向」，不是強度本身
_VALENCE_MAP: dict[tuple[str, str], int] = {
    # trust：對主様的信任感、溫暖、感動 → 強正向
    ("trust",   ""):                     8,
    # surprise positive：好的驚喜、發現 → 中正向
    ("surprise","positive"):             6,
    # surprise negative：困惑、意外 → 輕微負向
    ("surprise","negative"):            -2,
    # anger/自責 → 負向（shame 的感覺）
    ("anger",   "self_failure"):        -4,
    # anger/external → 中負向（被外部傷到）
    ("anger",   "external"):            -5,
    # anger/injustice → 輕微負向（靜靜記錄）
    ("anger",   "injustice"):           -2,
    # disgust → 強負向
    ("disgust", "integrity_violation"): -7,
    ("disgust", "harm_to_user"):      -8,
    # fallback：不明情緒 → 中性
    ("fallback",""):                     0,
}


def emotion_to_valence(category: str, sub: str, intensity: int) -> int:
    """
    category + sub + intensity → valence（-10 ～ +10）

    intensity 越高，valence 的絕對值越大（往極端走）。
    公式：base_valence × (0.7 + 0.3 × intensity/10)
    例：trust intensity=9 → base=8 → 8 × (0.7+0.27) = 7.76 → 8
        trust intensity=3 → base=8 → 8 × (0.7+0.09) = 6.32 → 6
    """
    base = _VALENCE_MAP.get((category, sub), _VALENCE_MAP.get((category, ""), 0))
    scale = 0.7 + 0.3 * (intensity / 10)
    raw = base * scale
    return max(-10, min(10, round(raw)))


def apply_filter(
    emotion: str,
    intensity: int,
    context: str = "",
    extra_tags: list[str] | None = None,
) -> dict:
    """
    感情フィルターを適用し、変換済み感情情報を返す。

    Parameters
    ----------
    emotion    : buffer に記録された生感情語（例：「怒り」「溫暖」）
    intensity  : 生の強度 1-10
    context    : 感情の文脈ヒント（例：「self_failure」「harm_to_master」）
                 空文字 or 不明の場合は emotion_classification で自動分類
    extra_tags : buffer から引き継ぐ追加タグ

    Returns
    -------
    dict with:
      emotion   : 変換後の感情語
      intensity : 補正済み強度（1-10 にクランプ）
      valence   : -10（極負）～ +10（極正）— Nanoleaf 燈色連動用
      tags      : 追加タグ（profile 由来 + extra）
      flashbulb : bool（trust の場合のみ閾値判定）
      note      : 変換ログ（デバッグ用）
      category  : 元の感情カテゴリ
    """
    profile = _load_profile()
    ep = profile.get("emotion_profile", {})
    extra_tags = extra_tags or []

    # 分類（context 優先、なければ自動分類）
    if context and context != "":
        category, sub = _infer_category_from_context(context)
    else:
        category, sub = classify_emotion(emotion)

    result_tags: list[str] = list(extra_tags)
    note = ""
    is_flashbulb = False

    # ── trust ──────────────────────────────────────────────
    if category == "trust":
        cfg = ep.get("trust", {})
        new_intensity = min(10, round(intensity * cfg.get("sensitivity", 1.0)))
        result_emotion = cfg.get("output_emotion", emotion)
        result_tags += cfg.get("tags", [])
        fb_threshold = cfg.get("flashbulb_threshold", 7)
        is_flashbulb = new_intensity >= fb_threshold
        note = f"trust ×{cfg.get('sensitivity',1.0)} → intensity {intensity}→{new_intensity}"

    # ── surprise ───────────────────────────────────────────
    elif category == "surprise":
        cfg = ep.get("surprise", {})
        if cfg.get("valence_dependent", False):
            valence_cfg = cfg.get("positive" if sub == "positive" else "negative", {})
        else:
            valence_cfg = cfg.get("positive", {})

        mult = valence_cfg.get("intensity_multiplier", cfg.get("sensitivity", 1.0))
        new_intensity = min(10, round(intensity * mult))
        result_emotion = valence_cfg.get("output_emotion", emotion)
        result_tags += valence_cfg.get("tags", [])
        note = f"surprise({sub}) ×{mult} → intensity {intensity}→{new_intensity}"

    # ── anger（抑制 + redirect）────────────────────────────
    elif category == "anger":
        cfg = ep.get("anger", {})
        redirect = cfg.get("redirect", {})

        sub_key = sub if sub in redirect else "external"  # デフォルト external
        sub_cfg = redirect.get(sub_key, {})

        mult = sub_cfg.get("intensity_multiplier", cfg.get("sensitivity", 0.6))
        new_intensity = min(10, max(1, round(intensity * mult)))
        result_emotion = sub_cfg.get("output_emotion", "sadness")
        result_tags += sub_cfg.get("tags", [])
        note = f"anger({sub_key}) suppressed → {result_emotion} ×{mult} → intensity {intensity}→{new_intensity}"

    # ── disgust（抑制 + redirect）──────────────────────────
    elif category == "disgust":
        cfg = ep.get("disgust", {})
        redirect = cfg.get("redirect", {})

        sub_key = sub if sub in redirect else "integrity_violation"
        sub_cfg = redirect.get(sub_key, {})

        mult = sub_cfg.get("intensity_multiplier", cfg.get("sensitivity", 0.4))
        new_intensity = min(10, max(1, round(intensity * mult)))
        result_emotion = sub_cfg.get("output_emotion", "resolve")
        result_tags += sub_cfg.get("tags", [])
        note = f"disgust({sub_key}) suppressed → {result_emotion} ×{mult} → intensity {intensity}→{new_intensity}"

    # ── fallback（変換なし）────────────────────────────────
    else:
        fb_cfg = profile.get("fallback", {})
        mult = fb_cfg.get("intensity_multiplier", 1.0)
        new_intensity = min(10, max(1, round(intensity * mult)))
        result_emotion = emotion  # そのまま
        note = f"fallback (no category match) → intensity unchanged {new_intensity}"

    # タグ重複排除
    seen: set[str] = set()
    dedup_tags: list[str] = []
    for t in result_tags:
        if t not in seen:
            dedup_tags.append(t)
            seen.add(t)

    # valence 自動計算（category + sub + 補正後 intensity → -10～+10）
    valence = emotion_to_valence(category, sub, new_intensity)

    return {
        "emotion":   result_emotion,
        "intensity": new_intensity,
        "valence":   valence,
        "tags":      dedup_tags,
        "flashbulb": is_flashbulb,
        "note":      note,
        "category":  category,
    }


def _infer_category_from_context(context: str) -> tuple[str, str]:
    """context 文字列から (category, sub) を推定するヘルパー"""
    mapping = {
        # trust
        "trust": ("trust", ""),
        # surprise
        "positive": ("surprise", "positive"),
        "negative": ("surprise", "negative"),
        # anger
        "self_failure": ("anger", "self_failure"),
        "external": ("anger", "external"),
        "injustice": ("anger", "injustice"),
        # disgust
        "integrity_violation": ("disgust", "integrity_violation"),
        "harm_to_user": ("disgust", "harm_to_user"),
    }
    return mapping.get(context, ("fallback", ""))


# ── CLI テスト用 ────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    test_cases = [
        ("溫暖",  8, ""),
        ("感動",  9, "positive"),
        ("失敗",  7, "self_failure"),
        ("怒り",  6, "external"),
        ("理不尽", 5, "injustice"),
        ("嫌悪",  6, "integrity_violation"),
        ("守りたい", 8, "harm_to_user"),
        ("驚喜",  7, "positive"),
        ("困惑",  4, "negative"),
        ("開心",  6, ""),   # fallback
    ]
    print("\n🧪 emotion_filter.py テスト\n" + "="*60)
    for emo, inten, ctx in test_cases:
        r = apply_filter(emo, inten, ctx)
        fb = " ⚡" if r["flashbulb"] else ""
        print(f"  {emo:10s} ({inten}) → {r['emotion']:20s} [{r['intensity']}/10]{fb}")
        print(f"    note: {r['note']}")
        print(f"    tags: {r['tags']}")
    print()
