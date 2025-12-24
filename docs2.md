Below is a complete, Mac-ready, copy/paste walkthrough that will create a minimal “Git platform MVP” inside:

`/Users/ingmuyleang/git`

It includes:

* A **FastAPI server** that creates bare repos under `repos/`
* A **simple post-receive hook** that logs pushes to `push.log`
* End-to-end testing: **API → create repo → clone → commit → push → verify push log**

---

## 1) Create the project folders

Run these commands exactly:

```bash
cd /Users/ingmuyleang/git
mkdir -p national-git-mvp
cd national-git-mvp
mkdir -p app repos scripts
```

---

## 2) Create `app/main.py`

Create the file:

```bash
cat > app/main.py << 'EOF'
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pathlib import Path
import subprocess
import os

APP_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = APP_ROOT / "repos"
REPO_ROOT.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="National Git MVP (No Auth)")

class RepoCreate(BaseModel):
    org: str
    name: str

def safe_name(s: str) -> str:
    s = s.strip()
    if not s:
        raise ValueError("Empty name not allowed")
    # Block path traversal and separators
    if "/" in s or "\\" in s or ".." in s:
        raise ValueError("Invalid name")
    # Keep it simple: allow letters/numbers/dash/underscore/dot
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
    if any(ch not in allowed for ch in s):
        raise ValueError("Only letters, numbers, '-', '_', '.' are allowed")
    return s

def write_post_receive_hook(repo_dir: Path) -> None:
    hooks_dir = repo_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    hook_path = hooks_dir / "post-receive"
    # This hook logs pushes to push.log inside the bare repo
    # It records: timestamp, pusher (from env if set), repo path, and ref updates.
    hook_content = """#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(pwd)"
LOG_FILE="$REPO_DIR/push.log"
PUSHER="${PUSHER_NAME:-unknown}"

TS="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

{
  echo "=== PUSH ==="
  echo "time_utc=$TS"
  echo "pusher=$PUSHER"
  echo "repo=$REPO_DIR"
  echo "updates:"
  while read -r oldrev newrev refname; do
    echo "  $refname $oldrev -> $newrev"
  done
  echo
} >> "$LOG_FILE"
"""
    hook_path.write_text(hook_content, encoding="utf-8")
    os.chmod(hook_path, 0o755)

@app.post("/repos")
def create_repo(body: RepoCreate):
    try:
        org = safe_name(body.org)
        name = safe_name(body.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    repo_dir = REPO_ROOT / org / f"{name}.git"
    if repo_dir.exists():
        raise HTTPException(status_code=409, detail="Repo already exists")

    repo_dir.parent.mkdir(parents=True, exist_ok=True)

    # Create a bare repository
    subprocess.run(["git", "init", "--bare", str(repo_dir)], check=True)

    # Add a post-receive hook to log pushes
    write_post_receive_hook(repo_dir)

    return {
        "org": org,
        "name": name,
        "path": str(repo_dir),
        "clone_url_file": f"file://{repo_dir}",
    }

@app.get("/repos")
def list_repos():
    repos = []
    for p in REPO_ROOT.rglob("*.git"):
        # repos/<org>/<name>.git
        org = p.parent.name
        name = p.stem
        repos.append({
            "org": org,
            "name": name,
            "path": str(p),
            "clone_url_file": f"file://{p}",
        })
    return {"repos": repos}

@app.get("/repos/{org}/{name}/pushlog")
def get_push_log(org: str, name: str):
    try:
        org = safe_name(org)
        name = safe_name(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    repo_dir = REPO_ROOT / org / f"{name}.git"
    if not repo_dir.exists():
        raise HTTPException(status_code=404, detail="Repo not found")

    log_path = repo_dir / "push.log"
    if not log_path.exists():
        return {"org": org, "name": name, "pushes": "", "note": "No pushes logged yet"}

    return {"org": org, "name": name, "pushes": log_path.read_text(encoding="utf-8")}
EOF
```

---

## 3) Create `requirements.txt`

```bash
cat > requirements.txt << 'EOF'
fastapi==0.115.6
uvicorn[standard]==0.34.0
pydantic==2.10.4
EOF
```

---

## 4) Create the E2E test script `scripts/e2e.sh`

This script will:

* create a repo via API
* clone it
* commit + push
* read the push log via API

```bash
cat > scripts/e2e.sh << 'EOF'
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
EOF

chmod +x scripts/e2e.sh
```

---

## 5) Create and activate virtual environment, install deps

```bash
cd /Users/ingmuyleang/git/national-git-mvp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 6) Run the server

In Terminal #1:

```bash
cd /Users/ingmuyleang/git/national-git-mvp
source .venv/bin/activate
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Leave it running.

---

## 7) Run the end-to-end test

In Terminal #2:

```bash
cd /Users/ingmuyleang/git/national-git-mvp
source .venv/bin/activate
./scripts/e2e.sh
```

You should see JSON output containing the `pushes` field with a `=== PUSH ===` entry.

---

## 8) Manual test (optional)

Create repo:

```bash
curl -X POST http://127.0.0.1:8000/repos \
  -H "Content-Type: application/json" \
  -d '{"org":"gov","name":"portal"}'
```

Clone and push:

```bash
cd /tmp
git clone file:///Users/ingmuyleang/git/national-git-mvp/repos/gov/portal.git
cd portal
echo "hello" > README.md
git add README.md
git -c user.email="a@b.com" -c user.name="Me" commit -m "test"
PUSHER_NAME="Me" git push origin main 2>/dev/null || PUSHER_NAME="Me" git push origin master
```

View push log via API:

```bash
curl http://127.0.0.1:8000/repos/gov/portal/pushlog
```

---

## Notes (so you do not get stuck)

* This is intentionally **no security**: anyone who can reach the API can create repos.
* We used `file://` cloning to validate the Git lifecycle first.
* Next step (when you are ready): replace `file://` with SSH/HTTPS gateways; keep the same repo storage.

If you run into an error, paste the exact terminal output (the last ~30 lines), and I will pinpoint the fix.
