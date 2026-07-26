# OpenClaw image-worker deployment

The web application is now a producer, not an image processor. Clicking **Queue next images** writes durable jobs to SQLite and returns immediately. OpenClaw sub-agents consume those jobs through `python -m app.worker`.

## Start four delegated workers

Create four OpenClaw sub-agent sessions and give each this command, changing both identity values for every worker:

```bash
cd ~/image_librarian
OPENCLAW_AGENT_NAME=image_worker_1 .venv/bin/python -m app.worker \
  --config config.yaml --agent-name image_worker_1 --worker-id image_worker_1
```

Repeat for `image_worker_2` through `image_worker_4`. The coordinator should spawn these as sub-agents and immediately return to normal conversation work.

## Scaling

`workers.recommended_count` controls the target pool size. Start at 2 workers on a single local vision model, then test 4. More workers only help when LM Studio/model-server parallelism and available VRAM can support simultaneous requests. Otherwise they provide queue resilience but may reduce tokens per second.

## Reliability controls

- Atomic SQLite `BEGIN IMMEDIATE` claims prevent duplicate assignment.
- WAL mode and a 30-second busy timeout improve concurrent access.
- Every lease has a token, expiry, heartbeat, agent name, and worker ID.
- Expired leases are requeued automatically.
- Vision runs in a killable child process with `workers.hard_timeout_seconds`.
- The default legacy JSON fallback is off, avoiding a second full inference after structured-output failure.
- The dashboard provides an assignment and completion audit.

## One-job validation

```bash
.venv/bin/python -m app.worker --config config.yaml \
  --agent-name image_worker_test --worker-id image_worker_test --once
```

Then open the dashboard and verify the recent job shows `image_worker_test` in the Agent column.
