#!/bin/bash

# 완전한 수정 및 재빌드 스크립트

echo "🔧 Starting complete fix and rebuild process..."

# 1. 템플릿 검증
echo "1️⃣ Validating template..."
./check-template.sh
if [ $? -ne 0 ]; then
    echo "❌ Template validation failed"
    exit 1
fi

# 2. 모든 컨테이너 및 이미지 정리
echo "2️⃣ Cleaning up Docker..."
docker-compose down --volumes --remove-orphans 2>/dev/null || true
docker rmi $(docker images "busan*" -q) 2>/dev/null || true
docker builder prune -f

# 3. Python 캐시 정리
echo "3️⃣ Cleaning Python cache..."
find . -name "*.pyc" -delete
find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

# 4. 완전 재빌드
echo "4️⃣ Complete rebuild..."
docker-compose build --no-cache --pull

# 5. 재시작
echo "5️⃣ Starting containers..."
docker-compose up -d

# 6. 상태 확인
echo "6️⃣ Checking status..."
sleep 5
docker-compose ps

echo ""
echo "✅ Fix and rebuild complete!"
echo "🌐 App: http://localhost:8000"
echo "🔍 Debug: http://localhost:8000/debug/template-check"
echo ""
echo "📋 Recent logs:"
docker-compose logs --tail=10
