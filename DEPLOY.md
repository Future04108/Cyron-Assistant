# Deploying and Restarting (Without Losing Data)

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

3. **If the API still does not come up**
   - Check API logs: `docker compose logs api`
   - If you see Postgres connection errors, confirm that `POSTGRES_PASSWORD` in `.env` is the same value that was used when the Postgres volume was first created. If you changed it at some point, the only way to “fix” without touching the DB is to set `POSTGRES_PASSWORD` back to that original value and restart the API.

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
