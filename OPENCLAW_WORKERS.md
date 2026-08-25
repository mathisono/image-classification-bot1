# Image-processor deployment

The web application is a queue producer, not an image processor. Clicking
**Queue next images** writes durable jobs to SQLite and returns immediately.
Three long-lived Betty workers consume those jobs through `python -m app.worker`.

## Current service topology

Run the deployment through the repository control script:

```bash
cd ~/image_librarian
./control.sh start
./control.sh status
```

`control.sh` creates transient services in the user systemd manager:

- `image-librarian-web.service` runs Uvicorn on the host and port in
  `config.yaml`.
- `image-librarian-worker-1.service` through
  `image-librarian-worker-3.service` run
  `betty_image_worker_1` through `betty_image_worker_3`.
- `image-librarian-db-sync.service` is also started when
  `database_sync.enabled` is true.

The exact worker count comes from `workers.recommended_count`; the checked-in
deployment uses three. Each worker records `betty` as its agent name and a
unique `betty_image_worker_N` worker ID.

These are transient units with `Restart=no`, matching the current local
deployment. They are managed by systemd while running but are not installed as
boot-persistent unit files. Run `./control.sh start` again after the user
systemd manager or host restarts.

## Process-tree note

Each long-lived worker forks a child process while it classifies a job. The
child intentionally has the same command line as its parent so the worker can
enforce `workers.hard_timeout_seconds`. Seeing a parent and child with the same
worker ID during inference does not mean the queue has duplicate persistent
workers.

## Operations

```bash
./control.sh status
./control.sh logs
./control.sh restart
./control.sh stop
```

`status` reads the live user-systemd units. `logs` follows their user
journal. `restart` is required after changing worker or vision configuration
because each worker loads `config.yaml` once at startup.

## Scaling

`workers.recommended_count` controls the target pool size. More workers only
help when the local model server and available VRAM can handle simultaneous
requests. Otherwise they provide queue resilience but may reduce throughput.

## Reliability controls

- Atomic SQLite `BEGIN IMMEDIATE` claims prevent duplicate assignment.
- WAL mode and a 30-second busy timeout improve concurrent access.
- Every lease has a token, expiry, heartbeat, agent name, and worker ID.
- Expired leases are requeued automatically.
- Vision runs in a killable child process with
  `workers.hard_timeout_seconds`.
- The default legacy JSON fallback is off, avoiding a second full inference
  after a structured-output failure.
- The dashboard provides an assignment and completion audit.

## One-job validation

```bash
.venv/bin/python -m app.worker --config config.yaml \
  --agent-name betty --worker-id betty_image_worker_test --once
```

Then open the dashboard and verify the recent job shows
`betty_image_worker_test` in the Worker column and `betty` in the Agent
column.
