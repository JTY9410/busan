#!/bin/bash

# 부산 보험 앱 자동 배포 스크립트
# 사용법: ./deploy.sh [commit-message]

set -e

# 설정
DOCKER_USERNAME="YOUR_DOCKERHUB_USERNAME"  # 수정 필요
IMAGE_NAME="busan-insurance"
COMMIT_MSG="${1:-Auto deploy $(date '+%Y-%m-%d %H:%M:%S')}"

echo "🚀 Starting deployment process..."

# Git 상태 확인
if [ -n "$(git status --porcelain)" ]; then
    echo "📝 Committing changes..."
    git add -A
    git commit -m "$COMMIT_MSG"
else
    echo "✅ No changes to commit"
fi

# Git 푸시
echo "📤 Pushing to GitHub..."
git push origin main

# Docker 빌드
COMMIT_HASH=$(git rev-parse --short HEAD)
TAG_LATEST="${DOCKER_USERNAME}/${IMAGE_NAME}:latest"
TAG_COMMIT="${DOCKER_USERNAME}/${IMAGE_NAME}:${COMMIT_HASH}"

echo "🔨 Building Docker image..."
docker build -t "$TAG_LATEST" -t "$TAG_COMMIT" .

echo "📤 Pushing Docker image..."
docker push "$TAG_LATEST"
docker push "$TAG_COMMIT"

echo "🎉 Deployment complete!"
echo "📦 GitHub: https://github.com/$(git config --get remote.origin.url | sed 's/.*github.com[:/]\([^.]*\).*/\1/')"
echo "🐳 Docker: $TAG_LATEST"
