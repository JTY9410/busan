#!/bin/bash

# Docker Hub 업로드 스크립트
# 사용법: ./docker-upload.sh [DOCKERHUB_USERNAME]

set -e

# Docker Hub 사용자명 확인
if [ -z "$1" ]; then
    echo "❌ 사용법: ./docker-upload.sh <DOCKERHUB_USERNAME>"
    echo "예시: ./docker-upload.sh myusername"
    exit 1
fi

DOCKER_USERNAME="$1"
IMAGE_NAME="busan-insurance"
VERSION=$(date '+%Y%m%d-%H%M%S')
TAG_LATEST="${DOCKER_USERNAME}/${IMAGE_NAME}:latest"
TAG_VERSION="${DOCKER_USERNAME}/${IMAGE_NAME}:${VERSION}"

echo "🚀 Docker 이미지 빌드 및 업로드 시작..."
echo "📦 이미지명: ${IMAGE_NAME}"
echo "👤 Docker Hub 사용자: ${DOCKER_USERNAME}"
echo ""

# Docker 로그인 확인
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker 데몬이 실행 중이지 않습니다."
    echo "Docker Desktop을 시작해주세요."
    exit 1
fi

# Docker Hub 로그인
echo "🔐 Docker Hub에 로그인..."
if ! docker login; then
    echo "❌ Docker Hub 로그인 실패"
    exit 1
fi

# Docker 이미지 빌드
echo ""
echo "🔨 Docker 이미지 빌드 중..."
docker build -t "$TAG_LATEST" -t "$TAG_VERSION" .

# 빌드 성공 확인
if [ $? -eq 0 ]; then
    echo "✅ 이미지 빌드 완료!"
else
    echo "❌ 이미지 빌드 실패"
    exit 1
fi

# Docker Hub에 푸시
echo ""
echo "📤 Docker Hub에 업로드 중..."
docker push "$TAG_LATEST"
docker push "$TAG_VERSION"

if [ $? -eq 0 ]; then
    echo ""
    echo "🎉 업로드 완료!"
    echo ""
    echo "📋 이미지 태그:"
    echo "   최신: ${TAG_LATEST}"
    echo "   버전: ${TAG_VERSION}"
    echo ""
    echo "💻 사용 예시:"
    echo "   docker pull ${TAG_LATEST}"
    echo "   docker run -d -p 8000:5000 ${TAG_LATEST}"
else
    echo "❌ 업로드 실패"
    exit 1
fi

