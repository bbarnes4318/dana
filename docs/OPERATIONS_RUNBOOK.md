# Operations Runbook

This runbook describes standard administrative procedures for starting, stopping, backing up, restoring, and troubleshooting the voice agent production infrastructure.

---

## 1. System Startup and Validation

All services run inside a Docker bridge network on the dedicated server host.

### Step 1a: Validate Compose File Config
Verify syntax and environment variable interpolation:
```bash
docker compose config
```
*(Ensure no errors are returned and all volumes/networks are mapped correctly).*

### Step 1b: Start Core Database & Caching Services
```bash
docker compose up -d postgres pgbouncer redis
```

Verify that all three services are healthy:
```bash
docker compose ps
# Ensure 'postgres', 'pgbouncer', and 'redis' report '(healthy)'
```

### Step 1c: Apply Migrations (Direct Mode)
Ensure the database schema is up-to-date. This must connect directly to PostgreSQL, bypassing PgBouncer connection pooling:
```bash
# Set DATABASE_ADMIN_URL to point directly to postgres
DATABASE_ADMIN_URL=postgresql://dana_user:dana_secure_pass@localhost:5432/dana \
  python -m storage.migrations
```

### Step 1d: Start Inference and Orchestration Services
Start vLLM and the voice agent:
```bash
docker compose up -d vllm-server voice-agent
```

Verify full system logs:
```bash
docker compose logs -f --tail=50 voice-agent
```

---

## 2. Daily Database Backups

We perform daily encrypted database dumps using `infra/backup/backup.sh`. Backups are compressed and encrypted with AES-256 (via openssl) to support generic S3 object storage (MinIO, R2, B2) or local filesystem retention.

### Cron Setup
Run the backup script daily at 2:00 AM. Add the following to your system `crontab -e`:
```cron
0 2 * * * /bin/bash /opt/dana/infra/backup/backup.sh >> /var/log/dana/backup.log 2>&1
```

### Manual Backup Check
To force a backup run immediately and inspect output size/encryption:
```bash
bash /opt/dana/infra/backup/backup.sh
```

---

## 3. Database Restoration

To restore from a backup, follow the decrypted import procedure.

> [!WARNING]
> Do **NOT** restore database dumps through PgBouncer on port `6432`.
> Always connect directly to PostgreSQL on port `5432`.

For full step-by-step restoration commands and decryption procedures, read:
[restore.md](file:///c:/Users/jimbo/OneDrive/Desktop/ultimate-voice/infra/backup/restore.md)

---

## 4. Troubleshooting and Recovery

### Issue: Redis is Down
*   **Symptom**: The voice agent logs show `Failed to ping Redis server. Falling back to InMemoryHotStateStore (DEGRADED mode).`
*   **Behavior**: The system degrades gracefully to run on a single container using an in-memory pace checker. It will **not** allow distributed dialing across multiple voice agent containers to prevent pacing violations.
*   **Recovery**: Restart the Redis service:
    ```bash
    docker compose restart redis
    ```
    Once Redis is back online, the voice agent will automatically reconnect on its next pacing loop.

### Issue: PgBouncer is Down
*   **Symptom**: Voice agent healthcheck fails, or connections fail with connection refused on port `6432`.
*   **Behavior**: System halts dialing completely to prevent lead-handling failures.
*   **Recovery**: Check PgBouncer logs and restart the container:
    ```bash
    docker compose logs pgbouncer
    docker compose restart pgbouncer
    ```

### Issue: Transaction Pool Exhaustion
*   **Symptom**: Database queries fail with `pgbouncer: pool size exceeded` or log slow query durations (>250ms).
*   **Behavior**: Background queue flushes slow down; conversation turn times increase.
*   **Recovery**:
    1.  Inspect active clients in PgBouncer console:
        ```bash
        psql "postgresql://dana_user:dana_secure_pass@localhost:6432/pgbouncer" -c "SHOW CLIENTS;"
        ```
    2.  If default pool size is too small, increase `PGBOUNCER_DEFAULT_POOL_SIZE` in `.env` and restart pgbouncer.
