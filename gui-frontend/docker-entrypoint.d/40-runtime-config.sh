#!/bin/sh
# Writes /tmp/config.js, which the app loads before it starts (see index.html).
# Values are checked before being written into JavaScript, so a stray quote can't break out of it.
set -eu

safe() {
  case "$2" in
    *[!A-Za-z0-9:/._-]*) echo "$1 contains characters that aren't allowed: $2" >&2; exit 1 ;;
  esac
}

fields=""
if [ -n "${OIDC_AUTHORITY:-}" ]; then
  safe OIDC_AUTHORITY "$OIDC_AUTHORITY"
  fields="$fields oidcAuthority: \"$OIDC_AUTHORITY\","
fi
if [ -n "${OIDC_CLIENT_ID:-}" ]; then
  safe OIDC_CLIENT_ID "$OIDC_CLIENT_ID"
  fields="$fields oidcClientId: \"$OIDC_CLIENT_ID\","
fi

echo "window.__APP_CONFIG__ = {$fields };" > /tmp/config.js
