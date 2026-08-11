FROM python:3.11-slim

# 1. Установка системных утилит для обработки аудио
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 2. Установка зависимостей Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3. Настройка путей для кэша моделей
ENV SB_LOCAL_DATASET_CACHE=/app/pretrained_models
ENV HF_HOME=/app/pretrained_models

# 4. Скачиваем веса модели, ПОКА ИНТЕРНЕТ ЕЩЁ ДОСТУПЕН
RUN python -c "from speechbrain.inference.speaker import EncoderClassifier; \
    EncoderClassifier.from_hparams(source='speechbrain/spkrec-ecapa-voxceleb', savedir='/app/pretrained_models/spkrec-ecapa-voxceleb')"

# 5. Включаем офлайн-режим ПОСЛЕ завершения скачивания
ENV TRANSFORMERS_OFFLINE=1
ENV HF_DATASETS_OFFLINE=1
ENV HF_HUB_DISABLE_SYMLINKS_WARNING=1

# 6. Копируем исходный код проекта
COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]