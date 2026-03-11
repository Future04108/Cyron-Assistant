# Deploying and Restarting (Without Losing Data)

## Fix: "password authentication failed for user postgres"

If API logs show `InvalidPasswordError` or `password authentication failed for user "postgres"`, the password in `.env` does not match the one the Postgres volume was created with.

**Option A — Keep existing data:** Set `POSTGRES_PASSWORD` in `.env` to the value that was used when the volume was first created. If you never set it, that value is usually **`postgres`**. Then restart the API:

```bash
docker compose up -d api
```

**Option B — Reset DB (data loss):** If you don’t need the data, re-create the volume so Postgres uses your current password:

```bash
docker compose down
docker volume rm cyron-assistant_postgres_data
docker compose up -d --build
```

After this, do not change `POSTGRES_PASSWORD` again, or you’ll hit the same error until you reset the volume or set the password back.

---

## You should never have to delete the database for a code update

Removing the Postgres volume (`docker volume rm ...`) is **not** required when you change application code (e.g. `backend/schemas/guild.py`). The database only stores your data; code changes do not require re-initializing it.

## Why did the API not respond until the volume was removed?

Usually one of these:

1. **Postgres password mismatch**  
   The API could not connect to Postgres because:
   - The Postgres container was first started with one `POSTGRES_PASSWORD` (or the default).
   - Later you changed `.env` (or the env passed to Compose) to a different `POSTGRES_PASSWORD`.
   - Postgres keeps the password it was **first** initialized with; the API then used the new password and failed.  
   After removing the volume, Postgres re-initialized with the **current** password, so the API could connect. So the “fix” was aligning credentials, not fixing code.

2. **Transient startup failure**  
   Sometimes the API starts before Postgres/Redis is fully ready and exits. The project now adds **startup retries** for DB and Redis so a simple restart usually works without touching the database.

## Keep the database and run smoothly

1. **Use a single, stable Postgres password**
   - Set `POSTGRES_PASSWORD` in `.env` once and keep it the same.
   - Do not change it later unless you are prepared to re-initialize the volume (or change the password inside Postgres manually).

2. **Code-only deploy (preserves data)**

   On the server (e.g. VPS), from the project root:

   ```bash
   cd ~/Cyron-Assistant   # or your project root
   git pull               # or copy your updated code
   docker compose up -d --build api
   ```

   Do **not** run `docker compose down` or `docker volume rm ...` for normal code updates. That keeps Postgres and Redis (and their data) running; only the API is rebuilt and restarted.

3. **If the API still does not come up** — see [Troubleshooting](#troubleshooting-api-not-responding) below.

## Troubleshooting: API not responding

When `curl http://YOUR_SERVER_IP:8000/health` fails:

1. **Check API logs (always do this first)**  
   ```bash
   docker compose logs api --tail 100
   ```  
   Look for:
   - `"startup_failed"` or `"startup_retry"` — the API is failing to connect to Postgres or Redis.
   - If you see `password authentication failed` or `connection refused` for Postgres: **`POSTGRES_PASSWORD` in `.env` must match the password used when the Postgres volume was first created.** If you changed it later, set it back to that original value (or remove the volume and lose data to re-initialize).

2. **Test from the server itself**  
   On the VPS run:
   ```bash
   curl http://127.0.0.1:8000/health
   ```  
   - If this returns `200` but curl to `http://YOUR_SERVER_IP:8000/health` fails: the API is up; the problem is firewall or routing. Open port 8000 (e.g. `ufw allow 8000 && ufw reload`) or use a reverse proxy.
   - If `127.0.0.1` also fails: the API process is likely crashing. Rely on step 1 (logs) to see the error.

3. **Wait for startup**  
   The API retries DB/Redis for up to ~20 seconds. After `docker compose up -d`, wait 30–60 seconds then try `curl http://127.0.0.1:8000/health` again.

4. **Run without host volume mounts (production)**  
   If the host’s `./backend` or `./shared` might be broken or different, run using the image’s code only (no mounts):
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
   ```  
   This keeps the database; only the way the API/bot get their code changes (from the image, not from the host).

## Optional: full stack restart (still keeps data)

If you want to restart everything but **keep** the database and Redis data:

```bash
docker compose down
docker compose up -d --build
```

Do **not** run `docker volume rm ...`. Your data stays in the named volumes (`postgres_data`, `redis_data`).

## Summary

| Goal                         | Commands                                      | Database preserved? |
|-----------------------------|-----------------------------------------------|----------------------|
| Deploy code change          | `git pull` then `docker compose up -d --build api` | Yes                  |
| Restart full stack          | `docker compose down` then `docker compose up -d --build` | Yes (no volume rm)   |
| Nuclear reset (lose data)   | `docker compose down` then `docker volume rm cyron-assistant_postgres_data` then `up -d --build` | No                   |

Use the first or second row for normal operation so the project runs smoothly and the original database is preserved after code changes and restarts.
