#!/usr/bin/env bash
# =============================================================================
# backup.sh — Production PostgreSQL Backup Script
#
# Generates a compressed daily pg_dump, encrypts it using openssl, and
# uploads it to local storage or an S3-compatible bucket (MinIO, Backblaze B2,
# Wasabi, Cloudflare R2, etc.).
# =============================================================================

set -euo pipefail

# Load environment variables if run manually from project root
if [[ -f .env ]]; then
    export $(grep -v '^#' .env | xargs)
fi

BACKUP_ENABLED="${BACKUP_ENABLED:-false}"
if [[ "$BACKUP_ENABLED" != "true" ]]; then
    echo "[INFO] Backups are disabled (BACKUP_ENABLED != true). Exiting."
    exit 0
fi

# Variables
BACKUP_TARGET="${BACKUP_TARGET:-local}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/dana}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
BACKUP_ENCRYPTION_KEY="${BACKUP_ENCRYPTION_KEY:-}"

# Database variables (read from DATABASE_ADMIN_URL or DATABASE_URL)
DB_URL="${DATABASE_ADMIN_URL:-${DATABASE_URL:-}}"
if [[ -z "$DB_URL" ]]; then
    echo "[ERROR] Neither DATABASE_ADMIN_URL nor DATABASE_URL is defined."
    exit 1
fi

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"' EXIT

DUMP_FILE="${TEMP_DIR}/dana_backup_${TIMESTAMP}.sql.gz"
ENC_FILE="${DUMP_FILE}.enc"

# 1. Run database dump
echo "[INFO] Starting pg_dump..."
if [[ "$DB_URL" =~ postgresql://([^:]+):([^@]+)@([^:/]+):?([0-9]*)/([^?]+) ]]; then
    DB_USER="${BASH_REMATCH[1]}"
    DB_PASS="${BASH_REMATCH[2]}"
    DB_HOST="${BASH_REMATCH[3]}"
    DB_PORT="${BASH_REMATCH[4]:-5432}"
    DB_NAME="${BASH_REMATCH[5]}"
    
    export PGPASSWORD="$DB_PASS"
    pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -F p | gzip > "$DUMP_FILE"
else
    # Fallback to direct pg_dump using connection string if pg_dump supports it
    pg_dump "$DB_URL" -F p | gzip > "$DUMP_FILE"
fi

if [[ ! -s "$DUMP_FILE" ]]; then
    echo "[ERROR] pg_dump failed or produced an empty file."
    exit 1
fi
echo "[OK] pg_dump completed successfully."

# 2. Encrypt backup file
if [[ -n "$BACKUP_ENCRYPTION_KEY" ]]; then
    echo "[INFO] Encrypting backup file..."
    openssl enc -aes-256-cbc -salt -pbkdf2 -pass pass:"$BACKUP_ENCRYPTION_KEY" -in "$DUMP_FILE" -out "$ENC_FILE"
    TARGET_FILE="$ENC_FILE"
    echo "[OK] Backup encrypted."
else
    echo "[WARNING] BACKUP_ENCRYPTION_KEY not set. Storing backup UNENCRYPTED."
    TARGET_FILE="$DUMP_FILE"
fi

# 3. Store backup according to target
FILE_NAME=$(basename "$TARGET_FILE")

if [[ "$BACKUP_TARGET" == "local" ]]; then
    echo "[INFO] Storing backup locally to ${BACKUP_DIR}..."
    mkdir -p "$BACKUP_DIR"
    cp "$TARGET_FILE" "${BACKUP_DIR}/${FILE_NAME}"
    echo "[OK] Backup stored locally at ${BACKUP_DIR}/${FILE_NAME}"

    # Local cleanup of old backups
    echo "[INFO] Cleaning up local backups older than ${BACKUP_RETENTION_DAYS} days..."
    find "$BACKUP_DIR" -type f -name "dana_backup_*" -mtime +"$BACKUP_RETENTION_DAYS" -delete
    echo "[OK] Local cleanup done."

elif [[ "$BACKUP_TARGET" == "s3_compatible" || "$BACKUP_TARGET" == "minio" ]]; then
    S3_ENDPOINT="${BACKUP_S3_ENDPOINT:-}"
    S3_BUCKET="${BACKUP_S3_BUCKET:-}"
    S3_ACCESS_KEY="${BACKUP_S3_ACCESS_KEY:-}"
    S3_SECRET_KEY="${BACKUP_S3_SECRET_KEY:-}"

    if [[ -z "$S3_ENDPOINT" || -z "$S3_BUCKET" || -z "$S3_ACCESS_KEY" || -z "$S3_SECRET_KEY" ]]; then
        echo "[ERROR] Missing S3 credentials or configuration (endpoint, bucket, access key, secret key)."
        exit 1
    fi

    echo "[INFO] Uploading backup to S3-compatible storage (${S3_ENDPOINT}/${S3_BUCKET})..."
    
    # Run inline python script to perform boto3 S3-compatible upload
    python3 -c "
import os, sys, boto3
from botocore.client import Config

endpoint = '$S3_ENDPOINT'
bucket = '$S3_BUCKET'
access = '$S3_ACCESS_KEY'
secret = '$S3_SECRET_KEY'
local_path = '$TARGET_FILE'
key = '$FILE_NAME'

try:
    s3 = boto3.client(
        's3',
        endpoint_url=endpoint,
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        config=Config(signature_version='s3v4')
    )
    s3.upload_file(local_path, bucket, key)
    print('[OK] S3 upload completed successfully.')
    
    # Retention cleanup in S3
    retention_days = int('$BACKUP_RETENTION_DAYS')
    import datetime
    from dateutil.tz import tzutc
    cutoff = datetime.datetime.now(tzutc()) - datetime.timedelta(days=retention_days)
    
    paginator = s3.get_paginator('list_objects_v2')
    pages = paginator.paginate(Bucket=bucket, Prefix='dana_backup_')
    
    deleted_count = 0
    for page in pages:
        if 'Contents' in page:
            for obj in page['Contents']:
                if obj['LastModified'] < cutoff:
                    s3.delete_object(Bucket=bucket, Key=obj['Key'])
                    deleted_count += 1
    if deleted_count > 0:
        print(f'[INFO] Cleaned up {deleted_count} stale backups from S3 bucket.')
except Exception as e:
    print(f'[ERROR] S3 backup operation failed: {e}', file=sys.stderr)
    sys.exit(1)
"
else
    echo "[ERROR] Unsupported BACKUP_TARGET: ${BACKUP_TARGET}"
    exit 1
fi

# 4. Backup health check (Report size and status)
FILE_SIZE=$(wc -c < "$TARGET_FILE")
echo "[OK] Backup process complete. File: ${FILE_NAME}, Size: ${FILE_SIZE} bytes."
