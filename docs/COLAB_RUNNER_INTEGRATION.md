# Colab runner integration

Open **Workers → Colab runner connection** after updating Docling. Supply the
runner control API root (for example `http://192.168.68.63:8000`, without `/v1`)
and its `RUNNER_TOKEN`. This token differs from the Kobold inference keys.
Requests run on the Docling backend, so the browser needs no runner CORS access.

Save the connection, fetch accounts, then import the accounts you want to use.
Choose **Link existing** when replacing manual configuration for an existing
physical worker; its ID and review assignments are preserved. Link only while
idle. Otherwise create a new worker. Do not register the same physical runtime
as both a manual and imported worker. Manual workers remain independently
editable and are never refreshed by the runner integration.

Monitoring polls authenticated `GET /accounts` and each linked, ready account's
`GET /accounts/{id}/endpoint` every ten seconds. It requires `running`, `healthy`,
`routable`, and an unexpired configured budget before allowing new jobs. The
backend saves the fetched URL and key and refreshes them after runtime changes.
Credential rotation waits until active requests finish. Missing, stale (45s),
invalid, or inaccessible status blocks both verification and review dispatch;
it does not consume a queued review attempt. Imported workers require monitoring
to be enabled, including after Docling restarts.

The account list shows the configured session budget countdown, a warning within
ten minutes of expiry, and Colab's separate upper-bound estimate when published
in account resources. Reconnect can retain an existing runtime and its budget.
The runner handles its own drain deadline and cooldown.

Optional automatic restart applies to enabled, unpaused accounts in `error`,
`idle`, or unhealthy `running`. It waits at least 60 seconds, waits for active
Docling reservations, and makes at most three attempts with increasing backoff
(four and eight minutes before later attempts). A verified healthy endpoint or
an explicit Start/Restart resets the recovery counter. Counters reset when
Docling restarts. It never bypasses cooldown, draining, quota exhaustion,
starting/reconnecting, or Google login. Resolve those conditions in the runner.
Inference content/validation errors alone do not trigger runtime restarts.

Imported cards provide Start, Stop, Restart, and Reconnect using the account
control routes. These reject busy workers. Stop pauses participation before the
remote command so automatic recovery cannot undo an intentional stop. A failed
control request leaves the worker paused; verify the runner state before retrying.
The ordinary **Stop after current job** button still pauses only Docling job
participation, without deleting the Colab runtime.

Runner tokens and inference keys are stored in separate files next to the worker
registry with mode 0600 on Linux. Public API responses expose key presence only;
upstream error bodies and arbitrary account configuration are not relayed.
This integration does not create/delete runner accounts or automate Google login.
