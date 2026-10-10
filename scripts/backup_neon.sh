#!/bin/sh
set -eu

ENV_FILE=${1:-.env.production}
BACKUP_DIR=${2:-./backups}
POSTGRES_TOOL_IMAGE=${3:-postgres:17-alpine}

if [ ! -f "$ENV_FILE" ]; then
    echo "Environment file not found: $ENV_FILE" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"
BACKUP_DIR_ABS=$(cd "$BACKUP_DIR" && pwd)
BACKUP_NAME="finance-$(date -u +%Y%m%dT%H%M%SZ).dump"

docker run --rm \
    --env-file "$ENV_FILE" \
    --env "BACKUP_NAME=$BACKUP_NAME" \
    --mount "type=bind,src=$BACKUP_DIR_ABS,dst=/backup" \
    "$POSTGRES_TOOL_IMAGE" \
    sh -c '
        test -n "$DATABASE_URL"
        case "$DATABASE_URL" in
            postgresql+psycopg://*) PG_DUMP_URL="postgresql://${DATABASE_URL#postgresql+psycopg://}" ;;
            postgresql://*) PG_DUMP_URL="$DATABASE_URL" ;;
            *) echo "DATABASE_URL must use postgresql:// or postgresql+psycopg://" >&2; exit 1 ;;
        esac
        pg_dump --dbname="$PG_DUMP_URL" --format=custom --no-owner --no-acl --file="/backup/$BACKUP_NAME"
    '

docker run --rm \
    --mount "type=bind,src=$BACKUP_DIR_ABS,dst=/backup,readonly" \
    "$POSTGRES_TOOL_IMAGE" \
    pg_restore --list "/backup/$BACKUP_NAME" >/dev/null

echo "$BACKUP_DIR_ABS/$BACKUP_NAME"
