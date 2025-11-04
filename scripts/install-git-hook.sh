#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
HOOKS_DIR="$REPO_ROOT/.git/hooks"
SCRIPT_SRC="$REPO_ROOT/scripts/post-commit-docker.sh"
HOOK_DEST="$HOOKS_DIR/post-commit"

if [ ! -d "$HOOKS_DIR" ]; then
  echo "[install] .git/hooks not found. Initialize git repo first: git init"
  exit 1
fi

if [ ! -f "$SCRIPT_SRC" ]; then
  echo "[install] post-commit source script not found: $SCRIPT_SRC"
  exit 1
fi

cp "$SCRIPT_SRC" "$HOOK_DEST"
chmod +x "$HOOK_DEST"
echo "[install] Installed git post-commit hook -> $HOOK_DEST"

echo "[install] Test: make a commit and Docker will build/push automatically."
