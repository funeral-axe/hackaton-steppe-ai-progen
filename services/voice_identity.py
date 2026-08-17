import json
import os

import torch
from sqlalchemy import text

from database import SessionLocal
from services.speaker_model import get_speaker_model
from services.speaker_scoring import (
    clamp_score,
    is_match,
    score_gap as calculate_score_gap,
)
from services.voice_engine import process_audio_file


DEFAULT_IDENTITY_THRESHOLD = 0.34
DEFAULT_AMBIGUITY_MARGIN = 0.12
DEFAULT_TOP_VOICEPRINTS = 20
DEFAULT_MAX_CANDIDATES = 5


def _embedding_to_pgvector(embedding):
    values = embedding.reshape(-1)

    if len(values) != 192:
        raise RuntimeError(
            f"Expected 192-dimensional embedding, got {len(values)}"
        )

    return (
        "["
        + ",".join(
            f"{float(value):.10f}"
            for value in values
        )
        + "]"
    )


def _build_embedding(audio_path):
    audio_path = os.path.abspath(
        str(audio_path)
    )

    if not os.path.isfile(audio_path):
        raise FileNotFoundError(
            f"Identity audio file not found: {audio_path}"
        )

    converted_wav_path = None

    try:
        waveform, converted_wav_path = (
            process_audio_file(audio_path)
        )

        model = get_speaker_model()

        with torch.no_grad():
            embedding = model.encode_batch(
                waveform
            )

        embedding_np = (
            embedding
            .detach()
            .cpu()
            .numpy()
            .reshape(-1)
        )

        return _embedding_to_pgvector(
            embedding_np
        )

    finally:
        if (
            converted_wav_path
            and os.path.isfile(
                converted_wav_path
            )
        ):
            try:
                os.remove(
                    converted_wav_path
                )
            except OSError:
                pass


def _person_key(row):
    un = str(
        row.get("un") or ""
    ).strip()

    if un:
        return f"un:{un}"

    return f"voiceprint:{row['id']}"


def _full_name(row):
    return " ".join(
        part
        for part in [
            row.get("last_name"),
            row.get("first_name"),
            row.get("middle_name"),
        ]
        if part
    )


def identify_voice_owner(
    audio_path,
    threshold=DEFAULT_IDENTITY_THRESHOLD,
    ambiguity_margin=DEFAULT_AMBIGUITY_MARGIN,
    top_voiceprints=DEFAULT_TOP_VOICEPRINTS,
    max_candidates=DEFAULT_MAX_CANDIDATES,
):
    threshold = float(threshold)
    ambiguity_margin = float(
        ambiguity_margin
    )

    vector_literal = (
        _build_embedding(
            audio_path
        )
    )

    db = SessionLocal()

    try:
        rows = (
            db.execute(
                text(
                    """
                    SELECT
                        id,
                        un,
                        first_name,
                        last_name,
                        middle_name,
                        phone,
                        (
                            1 - (
                                voiceprint
                                <=> CAST(:embedding AS vector)
                            )
                        ) AS similarity
                    FROM voiceprints
                    ORDER BY
                        voiceprint
                        <=> CAST(:embedding AS vector)
                    LIMIT :limit
                    """
                ),
                {
                    "embedding": vector_literal,
                    "limit": int(
                        top_voiceprints
                    ),
                },
            )
            .mappings()
            .all()
        )

    finally:
        db.close()

    if not rows:
        return {
            "identity": {
                "status": "no_profiles",
                "identified": False,
                "threshold": threshold,
                "ambiguity_margin": ambiguity_margin,
                "similarity": None,
                "candidates_count": 0,
            },
            "identity_candidates": [],
        }

    grouped = {}

    for row in rows:
        similarity = float(
            row["similarity"] or 0.0
        )

        similarity_score = round(
            clamp_score(
                similarity
            ),
            4,
        )

        key = _person_key(row)

        if key not in grouped:
            grouped[key] = {
                "un": row["un"],
                "first_name": row["first_name"],
                "last_name": row["last_name"],
                "middle_name": row["middle_name"],
                "full_name": _full_name(row),
                "phone": row["phone"],
                "similarity": similarity_score,
                "voiceprints": [],
                "profile_variants": [],
                "data_conflict": False,
            }

        candidate = grouped[key]

        profile_variant = {
            "voiceprint_id": row["id"],
            "first_name": row["first_name"],
            "last_name": row["last_name"],
            "middle_name": row["middle_name"],
            "full_name": _full_name(row),
            "phone": row["phone"],
        }

        candidate[
            "profile_variants"
        ].append(
            profile_variant
        )

        canonical_signature = (
            str(candidate.get("first_name") or "").strip(),
            str(candidate.get("last_name") or "").strip(),
            str(candidate.get("middle_name") or "").strip(),
        )

        current_signature = (
            str(row["first_name"] or "").strip(),
            str(row["last_name"] or "").strip(),
            str(row["middle_name"] or "").strip(),
        )

        if (
            current_signature
            != canonical_signature
        ):
            candidate[
                "data_conflict"
            ] = True

        candidate["voiceprints"].append(
            {
                "voiceprint_id": row["id"],
                "similarity": similarity_score,
                "full_name": _full_name(row),
            }
        )

        if (
            similarity_score
            > candidate["similarity"]
        ):
            candidate["similarity"] = (
                similarity_score
            )

    candidates = list(
        grouped.values()
    )

    for candidate in candidates:
        candidate["voiceprints"].sort(
            key=lambda item: item[
                "similarity"
            ],
            reverse=True,
        )

        candidate["voiceprints_count"] = (
            len(
                candidate["voiceprints"]
            )
        )

        if candidate.get(
            "data_conflict"
        ):
            candidate[
                "full_name"
            ] = None

    candidates.sort(
        key=lambda item: item[
            "similarity"
        ],
        reverse=True,
    )

    candidates = candidates[
        :int(max_candidates)
    ]

    above_threshold = [
        candidate
        for candidate in candidates
        if is_match(
            candidate["similarity"],
            threshold,
        )
    ]

    best_candidate = (
        candidates[0]
        if candidates
        else None
    )

    if not above_threshold:
        identity = {
            "status": "not_identified",
            "identified": False,
            "threshold": threshold,
            "ambiguity_margin": ambiguity_margin,
            "similarity": (
                best_candidate["similarity"]
                if best_candidate
                else None
            ),
            "candidates_count": 0,
        }

        return {
            "identity": identity,
            "identity_candidates": [],
        }

    best = above_threshold[0]

    if len(above_threshold) >= 2:
        second = above_threshold[1]

        score_gap = (
            calculate_score_gap(
                best["similarity"],
                second["similarity"],
            )
        )

    else:
        second = None
        score_gap = None

    has_data_conflict = bool(
        best.get(
            "data_conflict"
        )
    )

    is_ambiguous = (
        has_data_conflict
        or (
            second is not None
            and score_gap
            <= ambiguity_margin
        )
    )

    ambiguity_reason = None

    if has_data_conflict:
        ambiguity_reason = (
            "data_conflict"
        )

    elif is_ambiguous:
        ambiguity_reason = (
            "close_candidates"
        )

    if is_ambiguous:
        identity = {
            "status": "ambiguous",
            "identified": False,
            "reason": ambiguity_reason,
            "threshold": threshold,
            "ambiguity_margin": ambiguity_margin,
            "similarity": best[
                "similarity"
            ],
            "score_gap": score_gap,
            "candidates_count": len(
                above_threshold
            ),
        }

    else:
        identity = {
            "status": "identified",
            "identified": True,
            "threshold": threshold,
            "ambiguity_margin": ambiguity_margin,
            "similarity": best[
                "similarity"
            ],
            "score_gap": score_gap,
            "candidates_count": len(
                above_threshold
            ),
            "un": best["un"],
            "first_name": best[
                "first_name"
            ],
            "last_name": best[
                "last_name"
            ],
            "middle_name": best[
                "middle_name"
            ],
            "full_name": best[
                "full_name"
            ],
            "phone": best["phone"],
            "voiceprints_count": best[
                "voiceprints_count"
            ],
        }

    return {
        "identity": identity,
        "identity_candidates": (
            above_threshold[
                :int(max_candidates)
            ]
        ),
    }


def attach_voice_identity_to_results(
    results_dir,
    audio_path,
    threshold=DEFAULT_IDENTITY_THRESHOLD,
):
    results_dir = os.path.abspath(
        str(results_dir)
    )

    results_path = os.path.join(
        results_dir,
        "results.json",
    )

    if not os.path.isfile(
        results_path
    ):
        raise FileNotFoundError(
            f"results.json not found: {results_path}"
        )

    identity_result = (
        identify_voice_owner(
            audio_path=audio_path,
            threshold=threshold,
        )
    )

    with open(
        results_path,
        "r",
        encoding="utf-8",
    ) as handle:
        result_data = json.load(handle)

    result_data["identity"] = (
        identity_result["identity"]
    )

    result_data[
        "identity_candidates"
    ] = identity_result[
        "identity_candidates"
    ]

    temporary_path = (
        results_path + ".tmp"
    )

    with open(
        temporary_path,
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            result_data,
            handle,
            ensure_ascii=False,
            indent=2,
        )

    os.replace(
        temporary_path,
        results_path,
    )

    return identity_result
