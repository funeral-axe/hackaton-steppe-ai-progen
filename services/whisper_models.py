from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class WhisperModelOption:
    key: str
    label: str
    source: str


def _trained_large_v3_path() -> str:
    return (
        os.getenv(
            "WHISPER_LARGE_V3_TRAINED_PATH",
            r"C:\Users\ltd4i\Videos\model\whisper-large-trained-ct2",
        )
        .strip()
    )


def get_whisper_models() -> dict[str, WhisperModelOption]:
    trained_path = _trained_large_v3_path()

    return {
        "small": WhisperModelOption(
            key="small",
            label="Small",
            source="small",
        ),

        "medium": WhisperModelOption(
            key="medium",
            label="Medium",
            source="medium",
        ),

        "large-v3": WhisperModelOption(
            key="large-v3",
            label="Large v3",
            source="large-v3",
        ),

        "large-v3-turbo": WhisperModelOption(
            key="large-v3-turbo",
            label="Large v3 Turbo",
            source="large-v3-turbo",
        ),

        "large-v3-trained": WhisperModelOption(
            key="large-v3-trained",
            label="Large v3 — дообученная",
            source=trained_path,
        ),
    }


def get_whisper_model_choices() -> list[dict[str, str]]:
    return [
        {
            "key": option.key,
            "label": option.label,
            "source": option.source,
        }
        for option in get_whisper_models().values()
    ]


def resolve_whisper_model(
    selection: str | None,
) -> WhisperModelOption:
    key = str(
        selection or ""
    ).strip()

    if not key:
        key = "medium"

    models = get_whisper_models()

    option = models.get(key)

    if option is None:
        raise ValueError(
            f"Неизвестная модель Whisper: {key}"
        )

    return option
