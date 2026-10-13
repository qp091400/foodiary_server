FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# paddle / opencv가 쓰는 시스템 라이브러리
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 무거운 OCR 패키지를 먼저 깔아 둔다 (앱 코드를 고쳐도 이 레이어는 캐시된다)
COPY requirements-ocr.txt .
RUN pip install -r requirements-ocr.txt
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8000
# 워커는 1개: OCR 모델이 워커마다 메모리에 올라가기 때문
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
