#!/bin/bash

# 템플릿 파일 검증 스크립트

echo "🔍 Checking template file..."

TEMPLATE_FILE="templates/admin/insurance.html"

if [ ! -f "$TEMPLATE_FILE" ]; then
    echo "❌ Template file not found: $TEMPLATE_FILE"
    exit 1
fi

echo "✅ Template file exists"

# Check for old syntax
OLD_SYNTAX=$(grep -n "tzlocal()" "$TEMPLATE_FILE" || true)
if [ -n "$OLD_SYNTAX" ]; then
    echo "❌ Found old tzlocal() syntax:"
    echo "$OLD_SYNTAX"
    exit 1
else
    echo "✅ No old tzlocal() syntax found"
fi

# Check for new filters
NEW_FILTER=$(grep -n "to_local_datetime\|safe_datetime" "$TEMPLATE_FILE" || true)
if [ -n "$NEW_FILTER" ]; then
    echo "✅ Found new filters:"
    echo "$NEW_FILTER"
else
    echo "❌ New filters not found"
    exit 1
fi

# Show line 86-90 area
echo ""
echo "📋 Lines 84-90:"
sed -n '84,90p' "$TEMPLATE_FILE"

echo ""
echo "✅ Template validation complete!"
