import os
import json
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_IDS = os.getenv("CHAT_IDS", "")
CHAT_ID_LIST = [x.strip() for x in CHAT_IDS.split(",") if x.strip()]

DEBUG_ENABLED = os.getenv("DEBUG", "0") == "1"
INIT_SILENT = os.getenv("INIT_SILENT", "0") == "1"

STATE_PATH = "state.json"

AKSU_URL = "https://www.aksu.bel.tr/ihaleler"


def send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for chat_id in CHAT_ID_LIST:
        requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=30)


def load_state():
    if not os.path.exists(STATE_PATH):
        return {}
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def http_get(url):
    return requests.get(url, timeout=45, headers={"User-Agent": "Mozilla/5.0"})


def aksu_fetch_items():
    r = http_get(AKSU_URL)
    soup = BeautifulSoup(r.text, "html.parser")

    items = []
    seen = set()

    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip()
        title = " ".join(a.get_text(" ", strip=True).split())

        if not href or not title:
            continue

        # Sadece gerçek ihale detay linkleri
        if "/ihale/" not in href:
            continue

        # Sadece kiralama içerenler (istersen kaldırabiliriz)
        if "kiralama" not in title.lower():
            continue

        full_url = urljoin(AKSU_URL, href)

        if full_url in seen:
            continue
        seen.add(full_url)

        items.append({"title": title, "url": full_url})

    return items


def main():
    state = load_state()
    seen_urls = set(state.get("aksu_seen", []))

    items = aksu_fetch_items()
    new_items = [x for x in items if x["url"] not in seen_urls]

    if INIT_SILENT:
        state["aksu_seen"] = [x["url"] for x in items]
        save_state(state)
        if DEBUG_ENABLED:
            send_telegram(f"INIT_SILENT: toplam={len(items)} yeni={len(new_items)}")
        return

    if DEBUG_ENABLED:
        send_telegram(f"DEBUG: toplam={len(items)} yeni={len(new_items)}")

    for it in new_items:
        send_telegram(f"🆕 Aksu Kiralama İhalesi:\n{it['title']}\n{it['url']}")
        time.sleep(1)

    state["aksu_seen"] = [x["url"] for x in items]
    save_state(state)


if __name__ == "__main__":
    main()
