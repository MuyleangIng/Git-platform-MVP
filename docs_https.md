Below is a complete, minimal **HTTPS Git (“smart HTTP”)** implementation for your Mac setup. It is **no-security** (no auth, no permissions) and is intended only to validate the Git protocol works over HTTP/HTTPS. It uses `git http-backend` exactly the way real platforms do (behind a reverse proxy).

This will let you run:

```bash
git clone http://127.0.0.1:8000/gov/portal.git
git push  http://127.0.0.1:8000/gov/portal.git
```

---

## A) What you will build

* FastAPI app that:

  1. Creates bare repos under `repos/<org>/<name>.git`
  2. Enables HTTP push in that repo
  3. Exposes Git smart HTTP at:

     * `/<org>/<repo>.git/...` (the standard Git URL format)

It calls:

* `git http-backend` (Git’s official HTTP CGI backend)

---

## B) Install project in your folder

Use your folder:

```bash
cd /Users/ingmuyleang/git
mkdir -p national-git-https
cd national-git-https
mkdir -p app repos scripts
```

---

## C) Create `requirements.txt`

```bash
cat > requirements.txt << 'EOF'
fastapi==0.115.6
uvicorn[standard]==0.34.0
pydantic==2.10.4
EOF
```

---

## D) Create the full FastAPI code: `app/main.py`

```bash
cat > app/main.py << 'EOF'
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel
from pathlib import Path
import subprocess
import os

APP_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = APP_ROOT / "repos"
REPO_ROOT.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="National Git MVP - Smart HTTP (No Auth)")

class RepoCreate(BaseModel):
    org: str
    name: str

def safe_name(s: str) -> str:
    s = s.strip()
    if not s:
        raise ValueError("Empty name not allowed")
    if "/" in s or "\\" in s or ".." in s:
        raise ValueError("Invalid name")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
    if any(ch not in allowed for ch in s):
        raise ValueError("Only letters, numbers, '-', '_', '.' are allowed")
    return s

def init_bare_repo(repo_dir: Path) -> None:
    subprocess.run(["git", "init", "--bare", str(repo_dir)], check=True)

    # Enable pushes over smart HTTP for this bare repo
    # (Git disables receive-pack over HTTP by default in many setups.)
    subprocess.run(["git", "--git-dir", str(repo_dir), "config", "http.receivepack", "true"], check=True)
    subprocess.run(["git", "--git-dir", str(repo_dir), "config", "http.uploadpack", "true"], check=True)

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
    init_bare_repo(repo_dir)

    return {
        "org": org,
        "name": name,
        "path": str(repo_dir),
        "clone_url_http": f"http://127.0.0.1:8000/{org}/{name}.git",
    }

@app.get("/repos")
def list_repos():
    repos = []
    for p in REPO_ROOT.rglob("*.git"):
        org = p.parent.name
        name = p.stem
        repos.append({
            "org": org,
            "name": name,
            "path": str(p),
            "clone_url_http": f"http://127.0.0.1:8000/{org}/{name}.git",
        })
    return {"repos": repos}

def parse_cgi_response(raw: bytes) -> tuple[int, dict, bytes]:
    """
    git http-backend returns CGI-like headers then a blank line then body.
    It may include: Status: 200 OK
    """
    header_blob, _, body = raw.partition(b"\r\n\r\n")
    if not _:
        # Fallback if uses \n\n
        header_blob, _, body = raw.partition(b"\n\n")

    headers = {}
    status_code = 200

    for line in header_blob.splitlines():
        if not line.strip():
            continue
        k, sep, v = line.partition(b":")
        if not sep:
            continue
        key = k.decode("utf-8", "ignore").strip()
        val = v.decode("utf-8", "ignore").strip()
        if key.lower() == "status":
            # e.g. "200 OK" or "401 Authorization Required"
            try:
                status_code = int(val.split()[0])
            except Exception:
                status_code = 200
        else:
            headers[key] = val

    return status_code, headers, body

@app.api_route("/{org}/{repo}.git/{path:path}", methods=["GET", "POST"])
async def git_smart_http(org: str, repo: str, path: str, request: Request):
    """
    Smart HTTP endpoints like:
      GET  /org/repo.git/info/refs?service=git-upload-pack
      POST /org/repo.git/git-upload-pack
      POST /org/repo.git/git-receive-pack
    """
    try:
        org = safe_name(org)
        repo = safe_name(repo)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    repo_dir = REPO_ROOT / org / f"{repo}.git"
    if not repo_dir.exists():
        raise HTTPException(status_code=404, detail="Repo not found")

    # Build CGI environment expected by git http-backend
    # Reference: Git's http-backend uses CGI variables.
    query_string = request.url.query or ""
    method = request.method.upper()

    body = await request.body()
    content_type = request.headers.get("content-type", "")

    env = os.environ.copy()
    env.update({
        "GIT_PROJECT_ROOT": str(REPO_ROOT),  # root containing <org>/<repo>.git
        "GIT_HTTP_EXPORT_ALL": "1",          # allow serving repos without git-daemon-export-ok
        "REQUEST_METHOD": method,
        "QUERY_STRING": query_string,
        "CONTENT_TYPE": content_type,
        "CONTENT_LENGTH": str(len(body)),
        "PATH_INFO": f"/{org}/{repo}.git/{path}",
        "REMOTE_ADDR": request.client.host if request.client else "127.0.0.1",
    })

    # Call git http-backend
    try:
        proc = subprocess.run(
            ["git", "http-backend"],
            input=body,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="git binary not found on server")

    if proc.returncode != 0 and not proc.stdout:
        # If git fails badly, return stderr for debugging (still minimal MVP)
        err = proc.stderr.decode("utf-8", "ignore")
        raise HTTPException(status_code=500, detail=f"git http-backend error: {err}")

    status_code, headers, resp_body = parse_cgi_response(proc.stdout)

    # FastAPI/Starlette Response
    return Response(
        content=resp_body,
        status_code=status_code,
        headers=headers,
    )
EOF
```

---

## E) Create a simple HTTPS E2E test script: `scripts/e2e_http.sh`

```bash
cat > scripts/e2e_http.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail

API="http://127.0.0.1:8000"
ORG="gov"
NAME="portal-http-$(date +%s)"

echo "1) Create repo via API..."
JSON=$(curl -s -X POST "$API/repos" -H "Content-Type: application/json" -d "{\"org\":\"$ORG\",\"name\":\"$NAME\"}")
CLONE_URL=$(python3 - <<PY
import json,sys
print(json.loads(sys.stdin.read())["clone_url_http"])
PY
<<< "$JSON")

echo "   Clone URL: $CLONE_URL"

WORKDIR="/tmp/git-http-$NAME"
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"
cd "$WORKDIR"

echo "2) Clone via HTTP..."
git clone "$CLONE_URL" "$NAME"
cd "$NAME"

echo "3) Commit + push via HTTP..."
echo "# $NAME" > README.md
git add README.md
git -c user.email="test@example.com" -c user.name="Test User" commit -m "init"

# Push main then master
git push origin main 2>/dev/null || git push origin master

echo
echo "E2E HTTP OK: $ORG/$NAME"
EOF

chmod +x scripts/e2e_http.sh
```

---

## F) Install and run

```bash
cd /Users/ingmuyleang/git/national-git-https
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Leave that running.

---

## G) Run the HTTP Git test

In a new terminal:

```bash
cd /Users/ingmuyleang/git/national-git-https
source .venv/bin/activate
./scripts/e2e_http.sh
```

If it succeeds, you have proven: **repo create + clone + commit + push over HTTP works**.

---

# Important notes (so you do not get stuck)

1. This is **HTTP**, not HTTPS.
   For HTTPS locally you can add a reverse proxy (Nginx) with TLS, but Git protocol behavior is the same.

2. If push fails with permission-like behavior, the most common fix is already included:

   * `http.receivepack=true` in the bare repo

3. If your Mac Git default branch is `master`, the script already falls back.

---

## If you want “real HTTPS” locally (optional)

Tell me whether you prefer:

* **Option A:** Nginx + self-signed cert (fast)
* **Option B:** mkcert (trusted local cert, better dev experience)

I will give you the exact config for your Mac.

---

If you run into an error, paste:

* the `git push` output
* and the uvicorn terminal log (last ~30 lines)
  and I will pinpoint the fix quickly.
