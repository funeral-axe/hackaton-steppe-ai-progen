"""
Shared speaker similarity scoring helpers for SonAr.

Internal canonical representation:
    raw cosine score in range [-1.0, 1.0]

Legacy interfaces may still expose a 0..100 value.
Do not treat score * 100 as identity probability.
"""


SCORE_MIN = -1.0
SCORE_MAX = 1.0


def clamp_score(value):
    """Clamp raw cosine score to the valid range."""
    value = float(value)

    return max(
        SCORE_MIN,
        min(
            SCORE_MAX,
            value,
        ),
    )


def score_from_cosine_distance(distance):
    """
    Convert pgvector cosine distance to raw cosine score.

    pgvector:
        cosine distance = 1 - cosine similarity
    """
    return clamp_score(
        1.0 - float(distance)
    )


def score_to_legacy_percent(score):
    """
    Convert raw cosine score to SonAr's historical 0..100 scale.

    This is NOT a probability.
    It exists only for backward-compatible UI/search behaviour.
    """
    score = clamp_score(score)

    return round(
        max(
            0.0,
            score,
        )
        * 100.0,
        2,
    )


def legacy_percent_to_score(percent):
    """
    Convert historical 0..100 threshold back to raw cosine score.
    """
    return clamp_score(
        float(percent) / 100.0
    )


def is_match(score, threshold):
    """Return True when raw score reaches the raw threshold."""
    return (
        float(score)
        >= float(threshold)
    )


def score_gap(best_score, second_score):
    """Difference between two raw speaker scores."""
    return round(
        float(best_score)
        - float(second_score),
        4,
    )


def describe_match_strength(
    score,
    threshold,
):
    """
    Human-readable category for UI.

    These labels are relative to the current threshold.
    They are not calibrated probabilities.
    """
    score = float(score)
    threshold = float(threshold)

    delta = score - threshold

    if delta >= 0.15:
        return "strong"

    if delta >= 0.07:
        return "medium"

    if delta >= 0.0:
        return "possible"

    return "below_threshold"
