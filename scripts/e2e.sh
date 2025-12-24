#!/usr/bin/env bash
set -euo pipefail

API="http://127.0.0.1:8000"
ORG="gov"
NAME="portal-e2e-$(date +%s)"

echo "1) Create repo via API: $ORG/$NAME"
JSON=$(curl -s -X POST "$API/repos" \
  -H "Content-Type: application/json" \
  -d "{\"org\":\"$ORG\",\"name\":\"$NAME\"}")

REPO_PATH=$(python3 - <<PY
import json,sys
print(json.loads(sys.stdin.read())["path"])
PY
<<< "$JSON")

CLONE_URL="file://$REPO_PATH"
echo "   Repo path: $REPO_PATH"
echo "   Clone URL: $CLONE_URL"

WORKDIR="/tmp/git-e2e-$NAME"
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"
cd "$WORKDIR"

echo "2) Clone"
git clone "$CLONE_URL" "$NAME"
cd "$NAME"

echo "3) Commit + push"
echo "# $NAME" > README.md
git add README.md
git -c user.email="test@example.com" -c user.name="Test User" commit -m "init"

# Try pushing main, then master (depending on local default branch)
# Also set PUSHER_NAME env so hook logs it
PUSHER_NAME="TestUser" git push origin main 2>/dev/null || PUSHER_NAME="TestUser" git push origin master

echo "4) Fetch push log via API"
curl -s "$API/repos/$ORG/$NAME/pushlog" | python3 -m json.tool

echo
echo "E2E OK: $ORG/$NAME"