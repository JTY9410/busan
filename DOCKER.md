# Docker 빌드 및 실행 가이드

## 사전 요구사항
- Docker 설치 (Docker Desktop 또는 Docker Engine)
- Docker Compose (선택사항, 자동으로 설치됨)

## 빠른 시작

### 방법 1: Docker Compose 사용 (권장)

```bash
# Docker Compose로 빌드 및 실행
docker-compose up -d --build

# 로그 확인
docker-compose logs -f

# 중지
docker-compose down
```

### 방법 2: Docker 직접 사용

```bash
# 이미지 빌드
docker build -t busan-insurance:latest .

# 컨테이너 실행
docker run -d \
  --name busan-insurance-app \
  -p 5000:5000 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/static:/app/static \
  -v $(pwd)/uploads:/app/uploads \
  -e SECRET_KEY=your-secret-key-here \
  busan-insurance:latest

# 로그 확인
docker logs -f busan-insurance-app

# 중지 및 제거
docker stop busan-insurance-app
docker rm busan-insurance-app
```

## 접속

브라우저에서 http://localhost:8000 접속

## 데이터 영구 저장

다음 디렉토리가 호스트에 마운트되어 데이터가 영구 저장됩니다:
- `./data/` - 데이터베이스 파일
- `./static/` - 정적 파일 (로고 등)
- `./uploads/` - 업로드된 파일

## 환경 변수

`docker-compose.yml` 또는 `docker run` 명령에서 다음 환경 변수를 설정할 수 있습니다:

- `SECRET_KEY`: Flask 세션 시크릿 키 (기본값: dev-secret-key-change-in-production)
- `FLASK_ENV`: Flask 환경 (기본값: production)

## 프로덕션 배포

프로덕션 환경에서는:
1. `SECRET_KEY`를 강력한 랜덤 문자열로 변경
2. 리버스 프록시 (Nginx 등) 설정
3. HTTPS 적용
4. 정기적인 백업 설정

## 문제 해결

### 컨테이너가 시작되지 않는 경우
```bash
# 로그 확인
docker-compose logs

# 컨테이너 재시작
docker-compose restart
```

### 데이터베이스 초기화
```bash
# data 디렉토리 삭제 후 재시작
rm -rf data/*
docker-compose restart
```

### 포트 충돌
`docker-compose.yml`에서 포트를 변경:
```yaml
ports:
  - "8080:5000"  # 8080 포트로 변경
```

## Docker Hub 업로드

### 스크립트 사용 (권장)

```bash
# Docker Hub에 빌드 및 업로드
./docker-upload.sh YOUR_DOCKERHUB_USERNAME
```

이 스크립트는:
1. Docker Hub 로그인
2. 이미지 빌드
3. Docker Hub에 업로드 (latest 및 버전 태그)

### 수동 업로드

```bash
# 1. Docker Hub 로그인
docker login

# 2. 이미지 빌드 (태그 포함)
docker build -t YOUR_USERNAME/busan-insurance:latest .

# 3. Docker Hub에 푸시
docker push YOUR_USERNAME/busan-insurance:latest
```

### 업로드된 이미지 사용

```bash
# Docker Hub에서 이미지 가져오기
docker pull YOUR_USERNAME/busan-insurance:latest

# 이미지 실행
docker run -d \
  -p 8000:5000 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/static:/app/static \
  -v $(pwd)/uploads:/app/uploads \
  YOUR_USERNAME/busan-insurance:latest
```

## 로컬 빌드만 하기

Docker Hub에 업로드하지 않고 로컬에서만 빌드하려면:

```bash
# 빌드 스크립트 사용
./docker-build.sh

# 또는 직접 빌드
docker build -t busan-insurance:local .
```

