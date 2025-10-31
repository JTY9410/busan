FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_APP=app.py

WORKDIR /app

# 시스템 패키지 업데이트 및 필요한 패키지 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Python 의존성 설치
COPY requirements.txt /app/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 애플리케이션 코드 복사 (템플릿 캐시 방지)
COPY . /app
RUN find /app -name "*.pyc" -delete
RUN find /app -name "__pycache__" -type d -exec rm -rf {} + || true

# 필요한 디렉토리 생성
RUN mkdir -p /app/static /app/uploads /app/templates

# 볼륨 마운트 포인트 (데이터베이스와 업로드 파일 영구 저장)
VOLUME ["/app/data"]

# 포트 노출
EXPOSE 5000

# 애플리케이션 실행
CMD ["python", "app.py"]


