# NutakuClaimer

Automatically claims your Nutaku daily rewards and posts a Discord notification on success. Handles login, session persistence, and automatic re-authentication when the session expires.

---

## Features

- Auto-login with email and password — no manual cookie copying
- Session cookies persisted to `cookies.json` and reused across runs
- Automatic re-authentication when the session expires
- Discord embed notification on successful claim, including reward image
- Optional Discord notification when the reward has already been claimed
- Two run modes: **cron** (run once and exit) or **continuous** (stay alive and claim at each daily reset)

---

## Requirements

- Python 3.10+
- `requests` library

---

## Setup

**1. Clone and create a virtual environment**

```bash
git clone https://github.com/youruser/NutakuClaimer.git
cd NutakuClaimer
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

**2. Create your config**

```bash
cp config.json.example config.json
```

Edit `config.json`:

```json
{
  "email": "your@email.com",
  "password": "your_password",
  "discord_webhook": "https://discord.com/api/webhooks/YOUR_WEBHOOK_ID/YOUR_WEBHOOK_TOKEN",
  "notify_already_claimed": true,
  "cron": false
}
```

**3. Test it**

```bash
.venv/bin/python claimer.py
```

On first run it will log in, save `cookies.json`, and attempt to claim today's reward.

---

## Configuration

| Key | Type | Description |
|---|---|---|
| `email` | string | Your Nutaku account email |
| `password` | string | Your Nutaku account password |
| `discord_webhook` | string | Discord webhook URL. Leave empty (`""`) to disable notifications |
| `notify_already_claimed` | bool | Send a Discord notification when today's reward is already claimed |
| `cron` | bool | `true` = run once and exit. `false` = stay running and claim each day automatically |

---

## Run modes

### Continuous mode (`"cron": false`)

The script stays alive and sleeps until 00:05 UTC after each claim attempt (5 minutes after Nutaku's midnight UTC reset). This is the recommended mode for a server.

Deploy as a systemd service:

```bash
# Edit the service file and set YOUR_LINUX_USER
nano nutaku-claimer.service

sudo cp nutaku-claimer.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nutaku-claimer.service

# Check logs
journalctl -u nutaku-claimer.service -f
```

### Cron mode (`"cron": true`)

The script runs once and exits. Schedule it yourself via cron or any external scheduler:

```bash
# Example: run at 00:05 UTC daily
5 0 * * * /opt/nutaku-claimer/.venv/bin/python /opt/nutaku-claimer/claimer.py
```

---

## Discord notifications

**Successful claim** — green embed with reward name, type, item description, gold balance, and reward thumbnail.

**Already claimed** (when `notify_already_claimed: true`) — pink embed showing what was claimed earlier that day.

---

## Files

| File | Description |
|---|---|
| `claimer.py` | Main script |
| `config.json` | Your configuration (gitignored) |
| `config.json.example` | Config template |
| `cookies.json` | Auto-managed session cookies (gitignored) |
| `requirements.txt` | Python dependencies |
| `nutaku-claimer.service` | systemd service for continuous mode |
