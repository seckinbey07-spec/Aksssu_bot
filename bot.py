import os
import json
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# =========================
# Telegram
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_IDS = os.getenv("CHAT_IDS", "")
CHAT_ID_LIST = [x.strip() for x in CHAT_IDS.split(",") if x.strip()]

DEBUG_ENABLED = os.getenv("DEBUG", "0") == "1"
INIT_SILENT = os.getenv("INIT_SILENT", "0") == "1"
HEARTBEAT_ENABLED = os.getenv("HEARTBEAT", "0") == "1"

STATE_PATH = "state.json"


def send_telegram(text: str) -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN eksik.")
    if not CHAT_ID_LIST:
        raise RuntimeError("CHAT_IDS eksik. Örn: 8714272187 veya -100(grup_id)")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for chat_id in CHAT_ID_LIST:
        r = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=30)
        r.raise_for_status()


def load_state() -> dict:
    if not os.path.exists(STATE_PATH):
        return {}
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def http_get(url: str) -> requests.Response:
    return requests.get(url, timeout=45, headers={"User-Agent": "Mozilla/5.0"})


# =========================
# Aksu Belediyesi
# =========================
AKSU_ENABLED = os.getenv("AKSU_ENABLED", "1") == "1"
AKSU_URL = os.getenv("AKSU_URL", "https://www.aksu.bel.tr/ihaleler")


def aksu_fetch_items(limit: int = 80) -> list[dict]:
    r = http_get(AKSU_URL)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    items = []
    seen = set()

    for a in soup.select("a"):
        title = " ".join(a.get_text(" ", strip=True).split())
        href = (a.get("href") or "").strip()
        if not title or not href:
            continue
        if len(title) < 8:
            continue

        # "ihale" geçenleri al
        if "ihale" not in title.lower():
            continue

        full_url = urljoin(AKSU_URL, href)
        key = (title, full_url)
        if key in seen:
            continue
        seen.add(key)

        items.append({"title": title, "url": full_url})
        if len(items) >= limit:
            break

    return items


def aksu_check_new(state: dict) -> tuple[list[dict], dict, int]:
    seen_urls = set(state.get("aksu_seen_urls", []))
    items = aksu_fetch_items(limit=80)
    new_items = [it for it in items if it["url"] not in seen_urls]

    for it in items:
        seen_urls.add(it["url"])
    state["aksu_seen_urls"] = list(seen_urls)[:500]

    return new_items, state, len(items)


# =========================
# ilan.gov.tr (KAPALI)
# =========================
ILAN_ENABLED = os.getenv("ILAN_ENABLED", "0") == "1"


# =========================
# EKAP (şimdilik kapalı)
# =========================
EKAP_ENABLED = os.getenv("EKAP_ENABLED", "0") == "1"


def main() -> None:
    if HEARTBEAT_ENABLED:
        send_telegram("HEARTBEAT: Bot tetiklendi.")

    state = load_state()

    aksu_new = []
    aksu_total = 0

    if AKSU_ENABLED:
        aksu_new, state, aksu_total = aksu_check_new(state)

    # INIT_SILENT: ilk kurulumda eski ilanları yeni sayma
    if INIT_SILENT:
        save_state(state)
        if DEBUG_ENABLED:
            send_telegram(f"DEBUG (INIT_SILENT)\nAksu toplam={aksu_total}, yeni={len(aksu_new)}")
        return

    if DEBUG_ENABLED:
        send_telegram(f"DEBUG\nAksu toplam={aksu_total}, yeni={len(aksu_new)}\nilan.gov=KAPALI\nEKAP={'ACIK' if EKAP_ENABLED else 'KAPALI'}")

    for it in aksu_new:
        send_telegram(f"🆕 Aksu Belediyesi ihale:\n{it['title']}\n{it['url']}")
        time.sleep(1)

    save_state(state)


if __name__ == "__main__":
    main()
