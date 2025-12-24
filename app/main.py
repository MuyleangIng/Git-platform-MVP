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
