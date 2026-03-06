# Fix: "password authentication failed for user postgres" on VPS

The API fails to start because the database password used by the API does not match the password that PostgreSQL was initialized with. This usually happens when:

- You changed `.env` (e.g. `DATABASE_URL` or `POSTGRES_PASSWORD`) after Postgres was first created.
- An old Postgres data volume still exists with a different password.

Follow these steps **on your VPS** in order.

---

## Step 1: Use a single password everywhere

On the VPS, open your project directory and edit `.env`:

```bash
cd /root/Cyron-Assistant
nano .env
```

Set the Postgres password **once** and leave it unchanged. Either:

- **Option A (recommended):** Use the default so you don’t need to remember it:
  - Add or set: `POSTGRES_PASSWORD=postgres`
  - Do **not** set `DATABASE_URL` (Compose will build it from `POSTGRES_PASSWORD`).

- **Option B:** Use your own password:
  - Set: `POSTGRES_PASSWORD=mynewpass123` (or any password you choose).
  - Do **not** set `DATABASE_URL` (Compose will use `POSTGRES_PASSWORD` for the API as well).

Save and exit (`Ctrl+O`, `Enter`, `Ctrl+X`).

---

## Step 2: Remove old Postgres data (required after a password mismatch)

This deletes the existing Postgres volume so Postgres can re-initialize with the password from Step 1.

```bash
cd /root/Cyron-Assistant
docker compose down
docker volume rm cyron-assistant_postgres_data
```

If you get "No such volume", the volume name might be different. List volumes:

```bash
docker volume ls | grep -E "postgres|cyron"
```

Then remove the one that matches your project (e.g. `cyron-assistant_postgres_data`):

```bash
docker volume rm <volume_name>
```

---

## Step 3: Start the stack again

```bash
cd /root/Cyron-Assistant
docker compose up -d --build
```

Wait ~15 seconds for Postgres and Redis to become healthy, then check:

```bash
docker compose ps
docker compose logs api --tail=30
```

You should see the API start without "password authentication failed". Then:

```bash
curl http://localhost:8000/health
# or
curl http://161.97.87.172:8000/health
```

You should get a JSON response like `{"status":"healthy",...}`.

---

## Step 4: If it still fails

1. **Confirm `.env` is correct**
   - If you use a custom password, you must have **only** `POSTGRES_PASSWORD=yourpass` in `.env`.
   - Do **not** set `DATABASE_URL` in `.env` when using Docker Compose (the compose file sets it for the API using `POSTGRES_PASSWORD`).

2. **Confirm the volume was removed**
   - Run again: `docker compose down` then `docker volume rm cyron-assistant_postgres_data` (or the name from `docker volume ls`).
   - Then `docker compose up -d`.

3. **Check for typos**
   - In `.env`, the line must be exactly `POSTGRES_PASSWORD=postgres` (or your chosen password), no spaces around `=`.

4. **Re-pull the latest compose file**
   - Ensure `docker-compose.yml` uses `${POSTGRES_PASSWORD:-postgres}` for both the `postgres` service and the `api` service’s `DATABASE_URL`. If your repo was updated, pull and repeat from Step 2.

---

## Summary

| Step | Command / action |
|------|-------------------|
| 1 | Set `POSTGRES_PASSWORD=postgres` (or your password) in `.env`; do not set `DATABASE_URL`. |
| 2 | `docker compose down` then `docker volume rm cyron-assistant_postgres_data` |
| 3 | `docker compose up -d --build` |
| 4 | `curl http://localhost:8000/health` to verify |

After this, the API and Postgres use the same password and the "password authentication failed" error should be resolved.
