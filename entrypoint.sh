#!/usr/bin/env bash
set -euo pipefail

UPLOADS_DIR="${UPLOADS_DIR:-/app/uploads}"

mkdir -p "$UPLOADS_DIR"
chown www-data:www-data "$UPLOADS_DIR"
chmod 775 "$UPLOADS_DIR"

escape_apache_env() {
    local val="$1"
    val="${val//\\/\\\\}"
    val="${val//\"/\\\"}"
    printf '"%s"' "$val"
}

cat <<EOF > /etc/apache2/conf-available/app-env.conf
PassEnv DB_HOST DB_PORT DB_NAME DB_USER DB_PASSWORD DB_CONNECT_TIMEOUT UPLOADS_DIR
SetEnv DB_HOST $(escape_apache_env "${DB_HOST:-db}")
SetEnv DB_PORT $(escape_apache_env "${DB_PORT:-5432}")
SetEnv DB_NAME $(escape_apache_env "${DB_NAME:-videos}")
SetEnv DB_USER $(escape_apache_env "${DB_USER:-video_user}")
SetEnv DB_PASSWORD $(escape_apache_env "${DB_PASSWORD:-video_pass}")
SetEnv DB_CONNECT_TIMEOUT $(escape_apache_env "${DB_CONNECT_TIMEOUT:-10}")
SetEnv UPLOADS_DIR $(escape_apache_env "$UPLOADS_DIR")
EOF

a2enconf app-env >/dev/null

exec "$@"
