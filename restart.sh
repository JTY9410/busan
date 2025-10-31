#!/bin/bash

# 부산 보험 앱 재시작 스크립트 (템플릿 캐시 클리어)

echo "🔄 Restarting Busan Insurance App..."

# Docker Compose 중지
echo "⏹️  Stopping containers..."
docker-compose down

# 이미지 재빌드 (템플릿 변경사항 반영)
echo "🔨 Rebuilding image..."
docker-compose build --no-cache

# 컨테이너 재시작
echo "🚀 Starting containers..."
docker-compose up -d

echo "✅ Restart complete!"
echo "📱 App available at: http://localhost:8000"

# 로그 확인
echo "📋 Checking logs..."
docker-compose logs --tail=20
