# Ops Console Deploy

## Local

```text
cd D:\student-bot
python run_ops_console.py
```

Default bind:

```text
OPS_CONSOLE_HOST=127.0.0.1
OPS_CONSOLE_PORT=8091
```

Optional auth:

```text
OPS_CONSOLE_USERNAME=admin
OPS_CONSOLE_PASSWORD=change-me
```

## Droplet

Project path:

```text
/root/student-bot
```

Runner:

```text
python3 run_ops_console.py
```

PM2:

```text
pm2 start ecosystem.config.js --only ops-console
pm2 status ops-console
pm2 logs ops-console --lines 100
pm2 save
```

Health check:

```text
curl http://127.0.0.1:8091/api/ping
```

With optional auth enabled:

```text
curl -u "$OPS_CONSOLE_USERNAME:$OPS_CONSOLE_PASSWORD" http://127.0.0.1:8091/api/ping
```

Public nginx path:

```text
http://68.183.181.237/ops-console/
```

Current live shape:

- nginx proxies `/ops-console/` to `127.0.0.1:8091`
- nginx requires HTTP Basic Auth before the app is reached
- app is prefix-aware through `OPS_CONSOLE_URL_PREFIX=/ops-console`
- nginx also adds `Cache-Control: no-store`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, and `Referrer-Policy: no-referrer`
- nginx rate-limits the path at `10r/m` with a small burst allowance
- nginx denies methods other than `GET`, `POST`, and `HEAD`

## Notes

- Keep the console on loopback unless you intentionally put it behind a safer access layer.
- If you expose it beyond loopback, set `OPS_CONSOLE_USERNAME` and `OPS_CONSOLE_PASSWORD`.
- The console depends on the same repo-local SQLite files as the rest of Student Bot.
- On Ubuntu 24.04, system Python may require `python3 -m pip install --break-system-packages -r requirements.txt` if dependencies need to be refreshed outside a venv.
- HTTPS on the droplet is currently blocked by DNS: `arifaqyl.me` resolves to GitHub Pages right now, and the droplet has no certificate installed.
- A reusable nginx example for this path lives at `docs/ops-console-nginx.conf.example`.
