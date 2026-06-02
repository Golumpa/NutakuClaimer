#!/usr/bin/env python3
"""
Nutaku daily gold claimer.
Auto-logs in using credentials from config.json, persists session cookies
to cookies.json, and re-authenticates whenever the session has expired.
Posts a Discord notification on successful claim.
"""

import json
import logging
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

CONFIG_PATH = Path(__file__).parent / "config.json"
COOKIES_PATH = Path(__file__).parent / "cookies.json"
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger("nutaku-claimer")

BASE_URL = "https://www.nutaku.net"
HOME_URL = f"{BASE_URL}/home/"
LOGIN_URL = f"{BASE_URL}/execute-login/"
CALENDAR_DETAILS_URL = f"{BASE_URL}/rewards-calendar-details/"
REDEEM_URL = f"{BASE_URL}/rewards-calendar/rewards-calendar/redeem/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
}


# ---------------------------------------------------------------------------
# Config / cookie persistence
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        log.error("config.json not found — copy config.json.example and fill in your credentials")
        sys.exit(1)
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    if not cfg.get("email") or not cfg.get("password"):
        log.error("config.json must contain 'email' and 'password'")
        sys.exit(1)
    return cfg


def load_saved_cookies() -> dict:
    if COOKIES_PATH.exists():
        with open(COOKIES_PATH) as f:
            return json.load(f)
    return {}


def save_cookies(session: requests.Session):
    data = {c.name: c.value for c in session.cookies}
    with open(COOKIES_PATH, "w") as f:
        json.dump(data, f, indent=2)
    log.info("Cookies saved to %s", COOKIES_PATH)


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def build_session(cookies: dict | None = None) -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    if cookies:
        for name, value in cookies.items():
            session.cookies.set(name, value, domain="www.nutaku.net")
    return session


def fetch_csrf_token(session: requests.Session) -> tuple[str, str]:
    """GET the home page; return (csrf_token, page_html).
    Raises RuntimeError if the CSRF token is absent (indicates logged-out page)."""
    resp = session.get(HOME_URL, timeout=30)
    resp.raise_for_status()
    html = resp.text
    match = re.search(r'<meta[^>]+name=["\']csrf-token["\'][^>]+content=["\']([^"\']+)', html)
    if not match:
        match = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']csrf-token["\']', html)
    if not match:
        raise RuntimeError("csrf-token meta tag not found — session may be invalid")
    return match.group(1), html


def is_logged_in(session: requests.Session) -> bool:
    return session.cookies.get("Nutaku_userLoggedIn") == "1"


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def login(session: requests.Session, email: str, password: str):
    """Perform a full login: fetch CSRF from the login page, then POST credentials."""
    log.info("Logging in as %s…", email)

    # Need a fresh CSRF token from the unauthenticated home page
    resp = session.get(HOME_URL, timeout=30)
    resp.raise_for_status()
    html = resp.text

    match = re.search(r'<meta[^>]+name=["\']csrf-token["\'][^>]+content=["\']([^"\']+)', html)
    if not match:
        match = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']csrf-token["\']', html)
    if not match:
        raise RuntimeError("Could not find CSRF token on home page for login")
    csrf = match.group(1)

    resp = session.post(
        LOGIN_URL,
        data={
            "email": email,
            "password": password,
            "isGI": "0",
            "pre_register_title_id": "",
        },
        headers={
            "x-csrf-token": csrf,
            "x-requested-with": "XMLHttpRequest",
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "accept": "application/json, text/javascript, */*; q=0.01",
            "origin": BASE_URL,
            "referer": HOME_URL,
        },
        timeout=30,
    )
    resp.raise_for_status()

    if not is_logged_in(session):
        body = resp.text[:300]
        raise RuntimeError(f"Login POST succeeded but session cookie not set. Response: {body}")

    log.info("Login successful")


def ensure_logged_in(session: requests.Session, config: dict):
    """Check the current session and re-login if needed. Saves cookies on success."""
    if is_logged_in(session):
        # Confirm the session is actually valid by fetching the page
        try:
            csrf, html = fetch_csrf_token(session)
            # If the page contains a logged-in indicator we're good
            if is_logged_in(session):
                return csrf
        except Exception:
            pass

    log.info("Session is invalid or expired — re-authenticating…")
    session.cookies.clear()
    login(session, config["email"], config["password"])
    save_cookies(session)
    csrf, _ = fetch_csrf_token(session)
    return csrf


# ---------------------------------------------------------------------------
# Nutaku API
# ---------------------------------------------------------------------------

def get_calendar_details(session: requests.Session, csrf: str) -> dict:
    resp = session.get(
        CALENDAR_DETAILS_URL,
        headers={"x-csrf-token": csrf, "x-requested-with": "XMLHttpRequest"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def find_claimable_reward(calendar: dict) -> dict | None:
    for reward in calendar.get("rewards", []):
        if reward.get("status") == "current":
            return reward
    return None


def redeem(session: requests.Session, csrf: str, calendar_id: int) -> dict:
    resp = session.post(
        REDEEM_URL,
        data={"calendarId": calendar_id},
        headers={
            "x-csrf-token": csrf,
            "x-requested-with": "XMLHttpRequest",
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "accept": "application/json, text/javascript, */*; q=0.01",
            "referer": HOME_URL,
        },
        timeout=30,
    )
    resp.raise_for_status()
    try:
        return resp.json()
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------

def post_discord_already_claimed(webhook_url: str, reward: dict, calendar: dict):
    calendar_name = calendar.get("name", "Daily Rewards")
    day = reward.get("day", "?")
    slot_title = reward.get("slotTitle", "Unknown reward")
    benefit_type = reward.get("benefitType", "")
    title_name = reward.get("titleName", "")
    badge = reward.get("badge", "")

    lines = [f"**Day {day}** — {slot_title}"]
    if benefit_type == "gold":
        lines.append("Type: Nutaku Gold")
    elif benefit_type == "in-game-reward":
        lines.append(f"Type: In-Game Reward" + (f" ({title_name})" if title_name else ""))

    embed = {
        "title": f"⚠️ {calendar_name} — Already Claimed",
        "description": "\n".join(lines),
        "color": 0xf25881,
    }
    if badge:
        embed["thumbnail"] = {"url": reward_image_url(badge)}

    resp = requests.post(webhook_url, json={"embeds": [embed]}, timeout=10)
    if resp.status_code not in (200, 204):
        log.warning("Discord webhook returned %s: %s", resp.status_code, resp.text[:200])


def reward_image_url(badge: str) -> str:
    return f"{BASE_URL}/images/reward-calendar/{badge}.png"


def post_discord(webhook_url: str, reward: dict, calendar: dict, redeem_response: dict):
    calendar_name = calendar.get("name", "Daily Rewards")
    slot_title = reward.get("slotTitle", "Unknown reward")
    day = reward.get("day", "?")
    benefit_type = reward.get("benefitType", "")
    title_name = reward.get("titleName", "")
    badge = reward.get("badge", "")

    lines = [f"**Day {day}** — {slot_title}"]
    if benefit_type == "gold":
        lines.append("Type: Nutaku Gold")
    elif benefit_type == "in-game-reward":
        lines.append(f"Type: In-Game Reward" + (f" ({title_name})" if title_name else ""))
        desc = reward.get("itemDescription", "")
        if desc:
            lines.append(f"> {desc[:120]}")

    user_gold = redeem_response.get("userGold")
    if user_gold is not None:
        lines.append(f"Gold balance: **{user_gold}**")

    coupon = redeem_response.get("coupon")
    if coupon:
        lines.append(f"Coupon: `{coupon.get('code', '')}` — {coupon.get('title', '')}")
        if coupon.get("expiration"):
            lines.append(f"Expires: {coupon['expiration']}")

    embed = {
        "title": f"✅ {calendar_name} — Claimed!",
        "description": "\n".join(lines),
        "color": 0x58f286,
    }
    if badge:
        embed["thumbnail"] = {"url": reward_image_url(badge)}

    resp = requests.post(webhook_url, json={"embeds": [embed]}, timeout=10)
    if resp.status_code not in (200, 204):
        log.warning("Discord webhook returned %s: %s", resp.status_code, resp.text[:200])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def seconds_until_next_reset(offset_minutes: int = 5) -> float:
    """Seconds until midnight UTC plus a small offset to let Nutaku's reset settle."""
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    next_reset = tomorrow + timedelta(minutes=offset_minutes)
    return (next_reset - now).total_seconds()


def run_once(config: dict, session: requests.Session):
    discord_webhook: str = config.get("discord_webhook", "")

    csrf = ensure_logged_in(session, config)

    log.info("Fetching calendar details…")
    calendar = get_calendar_details(session, csrf)
    calendar_id = calendar.get("id")
    log.info("Calendar: %s (id=%s)", calendar.get("name"), calendar_id)

    if not calendar.get("isAnyCurrentRewardAvailable", False):
        log.info("No reward available right now (already claimed today or calendar not active)")
        if discord_webhook and config.get("notify_already_claimed", False):
            already_claimed = next(
                (r for r in calendar.get("rewards", []) if r.get("status") == "current-claimed"),
                None,
            )
            if already_claimed:
                post_discord_already_claimed(discord_webhook, already_claimed, calendar)
        return

    reward = find_claimable_reward(calendar)
    if not reward:
        log.warning("isAnyCurrentRewardAvailable=true but no 'current' reward found")
        return

    log.info("Claiming day %s: %s", reward.get("day"), reward.get("slotTitle"))
    result = redeem(session, csrf, calendar_id)
    log.info("Redeem response: %s", result)

    save_cookies(session)

    if discord_webhook:
        post_discord(discord_webhook, reward, calendar, result)
        log.info("Discord notification sent")


def main():
    config = load_config()
    cron_mode: bool = config.get("cron", True)

    saved_cookies = load_saved_cookies()
    session = build_session(saved_cookies)

    if cron_mode:
        log.info("Running in cron mode — single execution")
        run_once(config, session)
        log.info("Done")
        return

    log.info("Running in continuous mode — waiting for daily reset each midnight UTC")
    while True:
        try:
            run_once(config, session)
        except Exception as e:
            log.error("Error during claim attempt: %s", e)

        wait = seconds_until_next_reset()
        wake = datetime.now(timezone.utc) + timedelta(seconds=wait)
        h, rem = divmod(int(wait), 3600)
        m, s = divmod(rem, 60)
        log.info("Next attempt at %s UTC (%dh %dm %ds away)", wake.strftime("%Y-%m-%d %H:%M:%S"), h, m, s)
        time.sleep(wait)


if __name__ == "__main__":
    main()
