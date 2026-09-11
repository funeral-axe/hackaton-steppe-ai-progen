from __future__ import annotations

import json
import os
from urllib.parse import urlparse

import httpx

from meeting.prompts import SYSTEM_PROMPT, build_user_prompt
from meeting.schemas import MeetingAnalysis, TranscriptSegment


def get_ollama_url() -> str:
    return os.getenv(
        "OLLAMA_BASE_URL",
        "http://127.0.0.1:11434",
    ).rstrip("/")


def get_llm_model() -> str:
    return os.getenv(
        "MEETING_LLM_MODEL",
        "qwen2.5:3b",
    ).strip()


def _assert_local_url(url: str) -> None:
    parsed = urlparse(url)

    host = (
        parsed.hostname
        or ""
    ).lower()

    if host not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise RuntimeError(
            "Для Meeting Intelligence разрешена только локальная LLM. "
            f"Получен запрещённый host: {host}"
        )


def _segments_for_prompt(
    segments: list[TranscriptSegment],
) -> str:
    lines: list[str] = []

    for segment in segments:
        lines.append(
            f"[S{segment.id} "
            f"{segment.start:.2f}-{segment.end:.2f}] "
            f"{segment.text}"
        )

    return "\n".join(lines)


def _extract_json(content: str) -> dict:
    source = (content or "").strip()

    if source.startswith("```"):
        source = source.strip("`")

        if source.startswith("json"):
            source = source[4:].strip()

    try:
        return json.loads(source)
    except json.JSONDecodeError:
        start = source.find("{")
        end = source.rfind("}")

        if start == -1 or end == -1 or end <= start:
            raise RuntimeError(
                "Локальная LLM вернула невалидный JSON."
            )

        return json.loads(
            source[start:end + 1]
        )


def _sanitize_evidence(
    analysis: MeetingAnalysis,
    segments: list[TranscriptSegment],
) -> MeetingAnalysis:
    valid_ids = {
        segment.id
        for segment in segments
    }

    grounded_groups = [
        analysis.topics,
        analysis.decisions,
        analysis.open_questions,
        analysis.action_items,
        analysis.risks,
    ]

    for group in grounded_groups:
        for item in group:
            ids = getattr(
                item,
                "evidence_segment_ids",
                [],
            )

            clean_ids = sorted(
                {
                    int(segment_id)
                    for segment_id in ids
                    if int(segment_id) in valid_ids
                }
            )

            item.evidence_segment_ids = clean_ids

    for decision in analysis.decisions:
        decision.confidence = (
            "high"
            if decision.evidence_segment_ids
            else "needs_review"
        )

    for question in analysis.open_questions:
        question.confidence = (
            "high"
            if question.evidence_segment_ids
            else "needs_review"
        )

    for risk in analysis.risks:
        risk.confidence = (
            "high"
            if risk.evidence_segment_ids
            else "needs_review"
        )

    for item in analysis.action_items:
        if (
            item.evidence_segment_ids
            and item.assignee
            and item.deadline
        ):
            item.confidence = "high"

        elif item.evidence_segment_ids:
            item.confidence = "medium"

        else:
            item.confidence = "needs_review"

    analysis.summary = analysis.summary[:5]

    return analysis


def analyze_transcript(
    segments: list[TranscriptSegment],
) -> MeetingAnalysis:
    if not segments:
        return MeetingAnalysis()

    base_url = get_ollama_url()
    _assert_local_url(base_url)

    model = get_llm_model()

    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": build_user_prompt(
                    _segments_for_prompt(
                        segments
                    )
                ),
            },
        ],
        "options": {
            "temperature": 0,
            "num_ctx": int(os.getenv("MEETING_LLM_NUM_CTX", "4096")),
        },
        "keep_alive": "10m",
    }

    try:
        with httpx.Client(
            timeout=180.0,
            trust_env=False,
        ) as client:
            response = client.post(
                f"{base_url}/api/chat",
                json=payload,
            )

            response.raise_for_status()

    except httpx.ConnectError as exc:
        raise RuntimeError(
            "Ollama недоступна на localhost. "
            "Запустите Ollama и проверьте локальную модель."
        ) from exc

    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:1000]

        raise RuntimeError(
            "Ошибка локальной Ollama: "
            f"HTTP {exc.response.status_code}: {body}"
        ) from exc

    data = response.json()

    content = (
        data
        .get("message", {})
        .get("content", "")
    )

    raw_json = _extract_json(content)

    analysis = MeetingAnalysis.model_validate(
        raw_json
    )

    return _sanitize_evidence(
        analysis,
        segments,
    )

