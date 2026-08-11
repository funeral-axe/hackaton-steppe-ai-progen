import io
import os
import uuid
import tempfile
import json
import numpy as np
import torch
import torch.nn as nn

if not hasattr(torch.amp, 'custom_fwd'):
    def dummy_decorator(f=None, **kwargs):
        if f is None:
            return lambda x: x
        return f

    torch.amp.custom_fwd = dummy_decorator
    torch.amp.custom_bwd = dummy_decorator

from fastapi import APIRouter, Request, UploadFile, File, Form, Depends, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import text, or_
from pydub import AudioSegment
import torchaudio.compliance.kaldi as kaldi

from speechbrain.inference.speaker import EncoderClassifier
from database import get_db, User, VoicePrint

classifier = EncoderClassifier.from_hparams(
    source="speechbrain/spkrec-ecapa-voxceleb",
    savedir="pretrained_models/ecapa"
)

router = APIRouter()
templates = Jinja2Templates(directory="templates")

AUDIO_DIR = "app/storage/audio"
os.makedirs(AUDIO_DIR, exist_ok=True)


def get_current_user(request: Request, db: Session = Depends(get_db)):
    login = request.cookies.get("session_user")
    if not login:
        return None
    return db.query(User).filter(User.login == login).first()


def create_voiceprint(audio_path: str):
    wav = classifier.load_audio(audio_path)
    wav = wav.unsqueeze(0)

    if torch.cuda.is_available():
        wav = wav.cuda()

    try:
        feats = kaldi.fbank(wav, num_mel_bins=80, sample_frequency=16000)
        feats = feats.unsqueeze(0)
    except Exception as fbank_err:
        raise RuntimeError(f"Не удалось извлечь Fbank-признаки: {fbank_err}")

    container = getattr(classifier, "mods", getattr(classifier, "modules", None))
    if container is None:
        raise AttributeError("Не удалось найти контейнер модулей модели.")

    try:
        with torch.no_grad():
            base_model = getattr(container.embedding_model, "model", container.embedding_model)
            norm_layer = container.mean_var_norm_emb

            embeddings = base_model(feats)
            embeddings = norm_layer(embeddings)

            embedding = embeddings.squeeze().cpu().numpy().astype(np.float32)
            return embedding.tolist()

    except Exception as e:
        print("\n" + "❌" * 20)
        print(f"Ошибка прямого прогона базовой модели: {e}")
        print("❌" * 20 + "\n")
        raise AttributeError(f"Ошибка вычисления вектора: {e}")


# --- СТРАНИЦА ОПЕРАТОРА (ПОИСК ПО ИИН ИЛИ НОМЕРУ ТЕЛЕФОНА) ---

@router.get("/operator", response_class=HTMLResponse)
def operator_page(
        request: Request,
        search: str = Query(None),
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    if not user:
        return RedirectResponse("/login", status_code=303)

    role = user.role.lower() if user.role else ""
    if role != "operator":
        return RedirectResponse("/", status_code=303)

    # Число записей в базе
    total_count = db.query(VoicePrint).count()

    # Поиск по ИИН (un) или номеру телефона (phone)
    query = db.query(VoicePrint)
    if search:
        query = query.filter(
            or_(
                VoicePrint.un.ilike(f"%{search}%"),
                VoicePrint.phone.ilike(f"%{search}%")
            )
        )
    results = query.all()

    user_context = {"username": user.login}
    return templates.TemplateResponse(
        request=request,
        name="operator.html",
        context={
            "user": user_context,
            "total_count": total_count,
            "results": results,
            "search": search
        }
    )


# --- СКАЧИВАНИЕ ДАННЫХ И АУДИО ---

@router.get("/operator/download/{record_id}")
def download_voiceprint(record_id: int, db: Session = Depends(get_db)):
    record = db.get(VoicePrint, record_id)
    if not record:
        return {"error": "Not found"}
    data = json.dumps(record.voiceprint.tolist() if hasattr(record.voiceprint, "tolist") else list(record.voiceprint))
    return StreamingResponse(
        iter([data]),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename=voiceprint_{record_id}.json"}
    )


@router.get("/operator/audio/{record_id}")
def download_audio(record_id: int, db: Session = Depends(get_db)):
    record = db.get(VoicePrint, record_id)
    if not record or not os.path.exists(record.audio_path):
        return {"error": "Audio not found"}
    return FileResponse(path=record.audio_path, filename=f"audio_{record_id}.wav", media_type="audio/wav")


# --- БИОМЕТРИЧЕСКИЙ ПОИСК ПО ОТРЕЗКУ ГОЛОСА ---

@router.post("/operator/search-by-voiceprint")
async def search_by_voiceprint(
        request: Request,
        audio: UploadFile = File(...),
        start: float = Form(...),
        end: float = Form(...),
        threshold: int = Form(70),
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    if not user:
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

    role = user.role.lower() if user.role else ""
    if role != "operator":
        return JSONResponse(status_code=403, content={"detail": "Forbidden"})

    duration = end - start
    if duration < 3 or duration > 10:
        return JSONResponse(status_code=400, content={"error": "Длина фрагмента должна быть от 3 до 10 секунд"})

    temp_file_path = None
    try:
        file_bytes = await audio.read()
        original_audio = AudioSegment.from_file(io.BytesIO(file_bytes))
        trimmed_audio = original_audio[int(start * 1000): int(end * 1000)]

        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_file:
            temp_file_path = temp_file.name
            trimmed_audio.export(temp_file_path, format="wav")

        raw_embedding = create_voiceprint(temp_file_path)
        final_vector = [float(x) for x in raw_embedding]
        if len(final_vector) != 192:
            final_vector = (final_vector + [0.0] * 192)[:192]

        pg_vector_str = "[" + ",".join(map(str, final_vector)) + "]"

        # Поиск через pgvector с явным приведением типов для PostgreSQL
        sql_query = text("""
            SELECT id, un, first_name, last_name, phone, audio_path,
                   voiceprint <=> CAST(:embedding AS vector) AS distance
            FROM voiceprints
            ORDER BY distance ASC
            LIMIT 20
        """)
        rows = db.execute(sql_query, {"embedding": pg_vector_str}).fetchall()

        results = []
        for row in rows:
            similarity = (1 - float(row.distance)) * 100
            if similarity >= float(threshold):
                results.append({
                    "id": row.id,
                    "un": row.un,
                    "name": f"{row.last_name or ''} {row.first_name or ''}".strip(),
                    "phone": row.phone,
                    "audio_url": f"/operator/audio/{row.id}",
                    "similarity": round(similarity, 2)
                })

        results.sort(key=lambda x: x["similarity"], reverse=True)
        return {"found": len(results), "threshold": threshold, "results": results[:10]}

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Internal error: {str(e)}"})
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except Exception:
                pass


# --- ЗАГРУЗКА И СОХРАНЕНИЕ НОВОГО СЛЕПКА ---

@router.post("/operator/upload")
async def upload_audio(
        request: Request,
        audio: UploadFile = File(...),
        un: str = Form(""),
        first_name: str = Form(...),
        last_name: str = Form(...),
        middle_name: str = Form(""),
        phone: str = Form(""),
        start: float = Form(...),
        end: float = Form(...),
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    if not user:
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

    role = user.role.lower() if user.role else ""
    if role != "operator":
        return JSONResponse(status_code=403, content={"detail": "Forbidden"})

    duration = end - start
    if duration < 3:
        return JSONResponse(status_code=400, content={"error": "Минимум 3 секунды"})
    if duration > 10:
        return JSONResponse(status_code=400, content={"error": "Максимум 10 секунд"})

    try:
        file_bytes = await audio.read()
        original_audio = AudioSegment.from_file(io.BytesIO(file_bytes))
        trimmed_audio = original_audio[int(start * 1000): int(end * 1000)]

        if len(trimmed_audio) == 0:
            return JSONResponse(status_code=400, content={"error": "Пустой аудиофрагмент"})

        os.makedirs(AUDIO_DIR, exist_ok=True)
        file_id = str(uuid.uuid4())
        audio_filename = f"{file_id}.wav"
        audio_path = os.path.join(AUDIO_DIR, audio_filename)

        trimmed_audio.export(audio_path, format="wav")

        raw_embedding = create_voiceprint(audio_path)
        final_vector = [float(x) for x in raw_embedding]

        if len(final_vector) != 192:
            if len(final_vector) > 192:
                final_vector = final_vector[:192]
            else:
                final_vector = final_vector + [0.0] * (192 - len(final_vector))

        record = VoicePrint(
            un=un,
            first_name=first_name,
            last_name=last_name,
            middle_name=middle_name,
            phone=phone,
            audio_path=audio_path,
            voiceprint=final_vector,
            created_by=user.id
        )

        db.add(record)
        db.commit()
        db.refresh(record)

        return {
            "status": "success",
            "id": record.id
        }

    except Exception as e:
        db.rollback()
        print("UPLOAD ERROR:", str(e))
        return JSONResponse(status_code=500, content={"error": str(e)})