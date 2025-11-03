#!/bin/bash
# Vercel 프로젝트 대시보드 열기 스크립트

# 프로젝트 정보 읽기
if [ -f ".vercel/project.json" ]; then
    PROJECT_ID=$(cat .vercel/project.json | grep -o '"projectId":"[^"]*"' | cut -d'"' -f4)
    ORG_ID=$(cat .vercel/project.json | grep -o '"orgId":"[^"]*"' | cut -d'"' -f4)
    
    if [ -n "$PROJECT_ID" ] && [ -n "$ORG_ID" ]; then
        # Vercel 대시보드 URL 형식: https://vercel.com/{team}/busan
        DASHBOARD_URL="https://vercel.com/${ORG_ID}/busan"
        echo "✓ Opening Vercel dashboard: $DASHBOARD_URL"
        
        # macOS
        if [[ "$OSTYPE" == "darwin"* ]]; then
            open "$DASHBOARD_URL"
        # Linux
        elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
            xdg-open "$DASHBOARD_URL" 2>/dev/null || sensible-browser "$DASHBOARD_URL" 2>/dev/null
        # Windows (Git Bash)
        elif [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]]; then
            start "$DASHBOARD_URL"
        else
            echo "Please open this URL in your browser: $DASHBOARD_URL"
        fi
        exit 0
    fi
fi

# Fallback: 프로젝트 이름으로 대시보드 열기 시도
echo "Could not find project information. Opening Vercel dashboard..."
DASHBOARD_URL="https://vercel.com/dashboard"

if [[ "$OSTYPE" == "darwin"* ]]; then
    open "$DASHBOARD_URL"
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    xdg-open "$DASHBOARD_URL" 2>/dev/null || sensible-browser "$DASHBOARD_URL" 2>/dev/null
elif [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]]; then
    start "$DASHBOARD_URL"
else
    echo "Please open this URL in your browser: $DASHBOARD_URL"
fi

