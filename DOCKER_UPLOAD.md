# Docker 이미지 업로드 가이드

## 사전 준비

1. **Docker Desktop 실행**
   ```bash
   # Docker Desktop이 실행 중인지 확인
   docker info
   ```

2. **Docker Hub 계정 준비**
   - [Docker Hub](https://hub.docker.com/)에서 계정 생성
   - 또는 기존 계정 사용

## 빠른 업로드

### 방법 1: 자동 스크립트 사용 (권장)

```bash
# Docker Hub 사용자명과 함께 실행
./docker-upload.sh <DOCKERHUB_USERNAME>

# 예시:
./docker-upload.sh myusername
```

이 스크립트는 다음을 자동으로 수행합니다:
1. Docker Hub 로그인
2. 이미지 빌드 (최신 및 버전 태그)
3. Docker Hub에 업로드

### 방법 2: 수동 업로드

```bash
# 1. Docker Hub 로그인
docker login

# 2. 이미지 빌드
docker build -t <DOCKERHUB_USERNAME>/busan-insurance:latest .

# 3. 버전 태그 추가 (선택사항)
docker tag <DOCKERHUB_USERNAME>/busan-insurance:latest <DOCKERHUB_USERNAME>/busan-insurance:$(date '+%Y%m%d-%H%M%S')

# 4. 업로드
docker push <DOCKERHUB_USERNAME>/busan-insurance:latest
```

## 이미지 사용

업로드된 이미지를 다른 곳에서 사용:

```bash
# 이미지 다운로드
docker pull <DOCKERHUB_USERNAME>/busan-insurance:latest

# 실행
docker run -d -p 5000:5000 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/static:/app/static \
  -v $(pwd)/uploads:/app/uploads \
  <DOCKERHUB_USERNAME>/busan-insurance:latest
```

## 로컬 테스트

업로드 전에 로컬에서 테스트:

```bash
# 이미지 빌드
./docker-build.sh

# 로컬 실행
docker run -d -p 8000:5000 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/static:/app/static \
  -v $(pwd)/uploads:/app/uploads \
  busan-insurance:local
```

## 문제 해결

### Docker 데몬이 실행되지 않음
```bash
# macOS/Linux
# Docker Desktop을 시작하세요

# 실행 확인
docker info
```

### 로그인 실패
```bash
# Docker Hub 인증 토큰 사용
docker login -u <USERNAME> -p <TOKEN>

# 또는 대화형 로그인
docker login
```

### 이미지 업로드 실패
- Docker Hub 저장소 이름이 사용자명과 일치하는지 확인
- 저장소가 Docker Hub에 생성되어 있는지 확인
- 네트워크 연결 확인

