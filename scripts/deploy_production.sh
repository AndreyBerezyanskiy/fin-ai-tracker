#!/bin/sh
set -eu

deploy() {
    script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
    app_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)

    env_file=${APP_ENV_FILE:-/opt/family-finance/.env.production}
    project_name=${COMPOSE_PROJECT_NAME:-family-finance}
    deploy_user=${DEPLOY_USER:-financebot}
    backup_dir=${BACKUP_DIR:-/var/backups/family-finance}
    postgres_tool_image=${POSTGRES_TOOL_IMAGE:-postgres:18-alpine}
    compose_file="$app_dir/compose.production.yml"
    bot_stopped=0

    on_exit() {
        exit_code=$?
        if [ "$exit_code" -ne 0 ]; then
            echo "Deployment failed (exit $exit_code)." >&2
            if [ "$bot_stopped" -eq 1 ]; then
                echo "The bot remains stopped. Review the error before starting it." >&2
            fi
        fi
        exit "$exit_code"
    }
    trap on_exit EXIT

    compose() {
        sudo env \
            "APP_ENV_FILE=$env_file" \
            "COMPOSE_PROJECT_NAME=$project_name" \
            docker compose --file "$compose_file" "$@"
    }

    echo "[1/9] Checking prerequisites"
    command -v git >/dev/null
    command -v docker >/dev/null
    command -v sudo >/dev/null
    sudo -v
    sudo test -r "$env_file"
    sudo -u "$deploy_user" git -C "$app_dir" rev-parse --is-inside-work-tree >/dev/null

    dirty_files=$(sudo -u "$deploy_user" git -C "$app_dir" status --porcelain)
    if [ -n "$dirty_files" ]; then
        echo "Refusing to deploy: the server working tree has local changes:" >&2
        printf '%s\n' "$dirty_files" >&2
        exit 1
    fi

    echo "[2/9] Pulling the latest commit"
    sudo -u "$deploy_user" git -C "$app_dir" pull --ff-only
    deployed_commit=$(sudo -u "$deploy_user" git -C "$app_dir" rev-parse --short HEAD)

    echo "[3/9] Validating Docker Compose configuration"
    compose config --quiet

    echo "[4/9] Creating and validating a PostgreSQL backup"
    sudo "$app_dir/scripts/backup_neon.sh" \
        "$env_file" \
        "$backup_dir" \
        "$postgres_tool_image"

    echo "[5/9] Building the application image"
    compose build

    echo "[6/9] Stopping the previous worker"
    compose stop bot
    bot_stopped=1

    echo "[7/9] Applying database migrations"
    compose run --rm migrate

    echo "[8/9] Starting the updated worker"
    compose up -d --no-deps bot
    bot_stopped=0

    echo "[9/9] Waiting for the worker healthcheck"
    container_id=$(compose ps -q bot)
    if [ -z "$container_id" ]; then
        echo "The bot container was not created." >&2
        exit 1
    fi

    attempt=0
    while [ "$attempt" -lt 18 ]; do
        health=$(sudo docker inspect \
            --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
            "$container_id")
        case "$health" in
            healthy)
                echo "Deployment complete: commit $deployed_commit is healthy."
                compose ps bot
                trap - EXIT
                exit 0
                ;;
            unhealthy | exited | dead)
                echo "Worker state is $health." >&2
                compose logs --tail=100 bot >&2
                exit 1
                ;;
        esac
        attempt=$((attempt + 1))
        sleep 5
    done

    echo "Timed out waiting for a healthy worker." >&2
    compose ps bot >&2
    compose logs --tail=100 bot >&2
    exit 1
}

deploy "$@"
