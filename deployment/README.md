# Optional deployment components

The root [Compose file](../compose.yaml) is the self-hosting entry point. The
[Self-Hosting & Operations Guide](../docs/SELF-HOSTING.md) applies to Linux and Raspberry Pi.

- `compose-pi.example.yaml` adds optional CPU/process limits and mounts the
  installer's protected `./secrets` folder read-only. Set the relevant
  `*_FILE` variables in the private configuration. Use both Compose files, e.g.
  `docker compose -f compose.yaml -f deployment/compose-pi.example.yaml config --quiet`.
  Supply any external Paperless or reverse-proxy network configuration locally.
- `update-guard.patch` and `scripts/pi_ops.py` are examples for an already
  installed, compatible update guard. A fresh installation does not use them.
  Configure the guard's own group, backup policy, Compose path and container
  names. `PROJEKTAKTE_GUARD_COMPOSE_FILE` is required for deploy. Set the
  archive-services variable when the guard group includes Paperless. The optional
  variables are `PROJEKTAKTE_GUARD_PROJECT`, `PROJEKTAKTE_GUARD_WEB`,
  `PROJEKTAKTE_GUARD_WORKER` and comma-separated
  `PROJEKTAKTE_GUARD_ARCHIVE_SERVICES`. The guard example expects its existing
  JSON-format Compose file and the guard's `server` module.
- Keep real paths, backup policies, schedules and mounts in operator-specific
  configuration outside version control. The application containers do not
  require access to the Docker socket.
