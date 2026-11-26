#!/bin/sh
# Read-only. Review output before sharing: host paths/addresses are not recorded.
set -eu
date -u '+%Y-%m-%dT%H:%M:%SZ'
uname -m
cat /etc/os-release
free -m
df -h .
docker compose version
docker version --format '{{.Server.Version}}'
printf '\nApp image architecture:\n'
if docker image inspect projektakte:0.5.0-local --format '{{.Architecture}}'; then
  :
else
  printf 'Projektakte test image not installed; inventory continues.\n'
fi
printf '\nKernel memory controller availability:\n'
cat /proc/cgroups
