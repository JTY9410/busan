#!/bin/bash

# 강제 재빌드 스크립트 - 모든 캐시 제거

echo "🧹 Force rebuilding with cache clearing..."

# 모든 컨테이너 중지 및 제거
echo "⏹️  Stopping and removing containers..."
docker-compose down --volumes --remove-orphans

# 이미지 제거
echo "🗑️  Removing old images..."
docker rmi $(docker images "busan*" -q) 2>/dev/null || true

# Docker 빌드 캐시 정리
echo "🧽 Cleaning build cache..."
docker builder prune -f

# 완전 재빌드
echo "🔨 Complete rebuild..."
docker-compose build --no-cache --pull

# 재시작
echo "🚀 Starting fresh containers..."
docker-compose up -d

echo "✅ Force rebuild complete!"
echo "📱 App available at: http://localhost:8000"

# 로그 확인
echo "📋 Checking logs..."
docker-compose logs --tail=30
