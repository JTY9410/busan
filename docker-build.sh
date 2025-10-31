#!/bin/bash

# Docker 이미지 빌드 스크립트 (로컬 테스트용)
# 사용법: ./docker-build.sh

set -e

IMAGE_NAME="busan-insurance"
TAG="local"

echo "🔨 Docker 이미지 빌드 시작..."
echo "📦 이미지명: ${IMAGE_NAME}:${TAG}"
echo ""

# Docker 데몬 확인
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker 데몬이 실행 중이지 않습니다."
    echo "Docker Desktop을 시작해주세요."
    exit 1
fi

# Docker 이미지 빌드
echo "🔨 Docker 이미지 빌드 중..."
docker build -t "${IMAGE_NAME}:${TAG}" .

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ 이미지 빌드 완료!"
    echo ""
    echo "📋 다음 명령으로 실행할 수 있습니다:"
    echo "   docker run -d -p 8000:5000 \\"
    echo "     -v \$(pwd)/data:/app/data \\"
    echo "     -v \$(pwd)/static:/app/static \\"
    echo "     -v \$(pwd)/uploads:/app/uploads \\"
    echo "     ${IMAGE_NAME}:${TAG}"
    echo ""
    echo "   또는 docker-compose를 사용:"
    echo "   docker-compose up -d"
else
    echo "❌ 이미지 빌드 실패"
    exit 1
fi

