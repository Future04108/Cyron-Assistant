# Auth & Scheduler Fix – VPS Deployment

## What was fixed (in this repo)

1. **Scheduler crash** – `main.py` now imports `run_daily_reset` and `run_monthly_reset` from `backend.services.reset_service`, so the daily/monthly jobs no longer raise `NameError`.
2. **Auth routes** – `GET /auth/discord`, `GET /auth/callback`, `GET /auth/me` are implemented. A 404 on the VPS means the running container was built from code that does **not** include these files.

## Files required for auth (must exist on VPS before build)

- `backend/api/auth.py`
- `backend/services/auth_service.py`
- `backend/config.py` (with auth-related config: `discord_client_id`, `discord_client_secret`, `frontend_allowed_origins`, `backend_public_url`, `auth_jwt_*`)
- `backend/main.py` (with `from backend.api import ... auth`, `app.include_router(auth.router)`, and `from backend.services.reset_service import run_daily_reset, run_monthly_reset`)
- `requirements-backend.txt` (must include `httpx`, `PyJWT`)

## VPS commands (run from project root, e.g. `~/Cyron-Assistant`)

```bash
# 1. Ensure you have the latest code (auth + scheduler fix)
#    If your repo is a clone of this project, pull. If you copy files manually, ensure all auth files and main.py are present.
git pull

# 2. Confirm auth files exist
test -f backend/api/auth.py && test -f backend/services/auth_service.py && echo "Auth files OK" || echo "Missing auth files!"

# 3. Rebuild API image (no cache so new code is used) and restart
docker compose build --no-cache api
docker compose up -d api

# 4. Check API logs (should see no NameError)
docker compose logs -f api
# Ctrl+C after a few lines

# 5. Test auth route from VPS
curl -sI "http://127.0.0.1:8000/auth/discord?redirect_uri=http://localhost:5173/auth/callback"
# Expect: HTTP/1.1 307 (redirect to Discord) or 400 if redirect_uri invalid. Not 404.

# 6. Test from your PC (replace with your VPS IP if different)
curl -sI "http://161.97.87.172:8000/auth/discord?redirect_uri=http://localhost:5173/auth/callback"
```

## If you still get 404

- List files inside the running API container:
  `docker compose exec api ls -la /app/backend/api/`
  You should see `auth.py`. If not, the image was built from code without auth; fix the source and rebuild with `docker compose build --no-cache api && docker compose up -d api`.
- Ensure `.env` on the VPS has at least:
  `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `BACKEND_PUBLIC_URL=http://161.97.87.172:8000`, `FRONTEND_ALLOWED_ORIGINS=http://localhost:5173`, `AUTH_JWT_SECRET`.
