# syntax=docker/dockerfile:1
FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# paddle / opencv가 쓰는 시스템 라이브러리
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 무거운 OCR 패키지를 먼저 깔아 둔다 (앱 코드를 고쳐도 이 레이어는 캐시된다)
COPY requirements-ocr.txt constraints-production.txt ./
RUN pip install -r requirements-ocr.txt -c constraints-production.txt
COPY requirements.txt .
RUN pip install -r requirements.txt -c constraints-production.txt

COPY . .

EXPOSE 8000
# 워커는 1개: OCR 모델이 워커마다 메모리에 올라가기 때문
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

# 운영 전용 타깃. 기본 로컬 빌드는 아래 development 타깃을 사용한다.
FROM runtime AS production
RUN groupadd --gid 10001 foodiary \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/foodiary foodiary \
    && mkdir -p /app/uploads /home/foodiary/.paddlex \
    && chown -R foodiary:foodiary /app/uploads /home/foodiary
ENV HOME=/home/foodiary \
    PADDLE_PDX_CACHE_HOME=/home/foodiary/.paddlex \
    HF_HOME=/home/foodiary/.cache/huggingface \
    XDG_CACHE_HOME=/home/foodiary/.cache \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1
USER 10001:10001
CMD ["sh", "/app/deploy/entrypoint.sh"]

# Cloud Run 이미지는 모델을 빌드 때 검증하고 런타임 다운로드 없이 시작한다.
FROM production AS cloudrun-models
RUN python scripts/bake_ocr_models.py /tmp/verified-models

FROM production AS cloudrun
COPY --from=cloudrun-models --chown=0:0 /tmp/verified-models /opt/foodiary-models
ENV OCR_MODELS_DIR=/opt/foodiary-models \
    PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True
CMD ["python", "/app/scripts/cloudrun_entrypoint.py"]

FROM runtime AS development
# Named context supplied by scripts.start_with_recipes; contains no GitHub credentials.
COPY --from=recipe_seed / /opt/recipe-seed/
ENTRYPOINT ["sh", "/app/deploy/development-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
