#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
docker compose up -d "$@"

if command -v iptables-legacy >/dev/null 2>&1 &&
   command -v sudo >/dev/null 2>&1 &&
   sudo -n iptables -S DOCKER-FORWARD >/dev/null 2>&1; then
    legacy_policy=$(sudo -n iptables-legacy -S FORWARD)
    if [[ "$legacy_policy" == *"-P FORWARD DROP"* ]]; then
        bridge_name=$(docker network inspect nexova-net --format '{{if index .Options "com.docker.network.bridge.name"}}{{index .Options "com.docker.network.bridge.name"}}{{else}}br-{{slice .Id 0 12}}{{end}}')
        repaired=false
        if ! sudo -n iptables-legacy -C DOCKER-USER -i "$bridge_name" -j ACCEPT 2>/dev/null; then
            sudo -n iptables-legacy -I DOCKER-USER 1 -i "$bridge_name" -j ACCEPT
            repaired=true
        fi
        if ! sudo -n iptables-legacy -C DOCKER-USER -o "$bridge_name" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null; then
            sudo -n iptables-legacy -I DOCKER-USER 1 -o "$bridge_name" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
            repaired=true
        fi
        if [[ "$repaired" == true ]]; then
            printf '%s\n' "Reparado el bloqueo de iptables-legacy para $bridge_name."
            docker compose restart api
        fi
    fi
fi