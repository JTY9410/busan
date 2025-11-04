#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="wecarmobility/busan-insurance"
TAG_LATEST="latest"
GIT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "manual")
TAG_SHA="sha-${GIT_SHA}"

# Find repo root (this hook may run from .git/hooks directory)
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT"

echo "[post-commit] Building Docker image: ${IMAGE_NAME}:${TAG_LATEST} (${TAG_SHA})"
docker build -t ${IMAGE_NAME}:${TAG_LATEST} -t ${IMAGE_NAME}:${TAG_SHA} .

echo "[post-commit] Pushing Docker image tags: ${TAG_LATEST}, ${TAG_SHA}"
docker push ${IMAGE_NAME}:${TAG_LATEST}
docker push ${IMAGE_NAME}:${TAG_SHA}

echo "[post-commit] Done: ${IMAGE_NAME}@$(date '+%Y-%m-%d %H:%M:%S')"
