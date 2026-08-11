from pathlib import Path
import threading

import torch

from speechbrain.inference.speaker import EncoderClassifier
from speechbrain.utils.fetching import LocalStrategy


MODEL_ID = "speechbrain/spkrec-ecapa-voxceleb"

PROJECT_ROOT = Path(__file__).resolve().parents[1]

MODEL_DIR = (
    PROJECT_ROOT
    / "pretrained_models"
    / "spkrec-ecapa-voxceleb"
)

DEVICE = "cuda:0" if torch.cuda.is_available() else "cpu"


_model = None
_model_lock = threading.Lock()


def _model_is_available_locally() -> bool:
    required_files = (
        "hyperparams.yaml",
        "embedding_model.ckpt",
        "mean_var_norm_emb.ckpt",
        "classifier.ckpt",
        "label_encoder.txt",
    )

    return all(
        (MODEL_DIR / filename).is_file()
        for filename in required_files
    )


def _load_model():
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    if _model_is_available_locally():
        source = str(MODEL_DIR)
        local_strategy = LocalStrategy.NO_LINK

        print(
            f"Loading local ECAPA model "
            f"from {MODEL_DIR} on {DEVICE}"
        )
    else:
        source = MODEL_ID
        local_strategy = LocalStrategy.COPY_SKIP_CACHE

        print(
            f"Downloading ECAPA model "
            f"to {MODEL_DIR} on {DEVICE}"
        )

    load_kwargs = {
        "source": source,
        "savedir": str(MODEL_DIR),
        "run_opts": {"device": DEVICE},
        "local_strategy": local_strategy,
    }

    if source == str(MODEL_DIR):
        load_kwargs["overrides"] = {
            "pretrained_path": str(MODEL_DIR),
        }

    model = EncoderClassifier.from_hparams(**load_kwargs)

    return model


def get_speaker_model():
    global _model

    if _model is None:
        with _model_lock:
            if _model is None:
                _model = _load_model()

    return _model
