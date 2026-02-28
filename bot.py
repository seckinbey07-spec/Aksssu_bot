import os
import json
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# Kaynak (Aksu Belediyesi)
AKSU_URL = os.getenv("AKSU_URL", "https://www.aksu.bel.tr/ihaleler")

# Telegram
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# State
STATE_PATH = "state.json"


def send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=30)
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
    return requests.get(
        url,
        timeout=45,
        headers={"User-Agent": "Mozilla/5.0"},
    )


def aksu_fetch_items(limit: int = 80) -> list[dict]:
    """
    Aksu Belediyesi ihaleler sayfasından ihale linklerini çeker.
    Dönen: [{"title": "...", "url": "..."}]
    """
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

        # Başlıkta "ihale" geçenleri öncelikle al
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


def aksu_check_new(state: dict) -> tuple[list[dict], dict]:
    seen_urls = set(state.get("aksu_seen_urls", []))

    items = aksu_fetch_items(limit=80)

    new_items = [it for it in items if it["url"] not in seen_urls]

    # state güncelle (en fazla 500 link sakla)
    for it in items:
        seen_urls.add(it["url"])
    state["aksu_seen_urls"] = list(seen_urls)[:500]

    return new_items, state


def main() -> None:
    if not BOT_TOKEN or not CHAT_ID:
        raise SystemExit("BOT_TOKEN veya CHAT_ID eksik. GitHub Secrets ayarlarını kontrol edin.")

    state = load_state()

    new_items, state = aksu_check_new(state)

    # Yeni ihaleleri tek tek gönder
    if new_items:
        for it in new_items:
            send_telegram(f"🆕 Aksu Belediyesi ihale:\n{it['title']}\n{it['url']}")
            time.sleep(1)  # Telegram rate limit için ufak bekleme

    save_state(state)


if __name__ == "__main__":
    main()
