# ScriptMan — Quick Start

## Files
- `scriptman.py`          — the entire application (single file)
- `scripts.json`          — your script definitions + auth config
- `scriptman.nginx.conf`  — nginx reverse proxy config
- `scriptman.service`     — optional systemd unit to run as a service

---

## 1. Install dependency

```bash
pip install flask
# or: pip3 install flask
```

---

## 2. Configure your scripts

Edit `scripts.json`. Each script entry:

```json
{
  "id": "unique-slug",          // used in URLs
  "name": "Human Name",
  "description": "optional",
  "command": "bash /path/to/script.sh",
  "cwd": "/working/directory",
  "env": { "KEY": "value" },    // extra env vars
  "auto_restart": false          // restart on exit?
}
```

---

## 3. Change the default password

Default is `admin` / `changeme`. Generate a new hash:

```bash
python3 -c "import hashlib; print(hashlib.sha256(b'yourpassword').hexdigest())"
```

Paste the result into `scripts.json` → `auth.password_hash`.

---

## 4. Run

```bash
python3 scriptman.py
```

Environment variable overrides:
```
SCRIPTMAN_CONFIG=scripts.json   # config file path
SCRIPTMAN_HOST=127.0.0.1        # bind host (keep 127.0.0.1 behind nginx)
SCRIPTMAN_PORT=7000             # port
SCRIPTMAN_SECRET=<hex>          # Flask secret key (auto-generated if unset)
```

---

## 5. Nginx

```bash
sudo cp scriptman.nginx.conf /etc/nginx/sites-available/scriptman
sudo ln -s /etc/nginx/sites-available/scriptman /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Edit the `server_name` line first.

For HTTPS (recommended):
```bash
sudo certbot --nginx -d your-domain.com
```

---

## 6. Run as a systemd service (optional)

```bash
sudo cp scriptman.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now scriptman
sudo journalctl -fu scriptman
```

---

## Features

- **Start / Stop / Restart** any configured script
- **Live log streaming** via Server-Sent Events (no page refresh needed)
- **Log history** ring buffer (last 1000 lines per script)
- **Auto-restart** on exit (per-script flag)
- **Session-based auth** with hashed password
- **Zero JS build step** — plain HTML+JS embedded in Python
- **Single file** — `scriptman.py` is everything
