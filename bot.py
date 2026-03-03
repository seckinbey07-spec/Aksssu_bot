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
        raise RuntimeError("CHAT_IDS eksik.")
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


def aksu_is_candidate(title_l: str, href_l: str) -> bool:
    """
    Aksu sayfasında ihale linklerini kaçırmamak için toleranslı filtre:
    - title'da ihale/tender benzeri
    - veya href içinde ihale/ihaleler
    """
    if "ihale" in title_l or "tender" in title_l:
        return True
    if "/ihale" in href_l or "ihal" in href_l:
        return True
    return False


def aksu_fetch_items(limit: int = 120) -> list[dict]:
    r = http_get(AKSU_URL)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    items = []
    seen = set()

    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip()
        if not href:
            continue

        title = " ".join(a.get_text(" ", strip=True).split())
        title_l = title.lower()
        href_l = href.lower()

        if not aksu_is_candidate(title_l, href_l):
            continue

        full_url = urljoin(AKSU_URL, href)

        # başlık boşsa URL'den üret
        if not title:
            title = full_url

        key = full_url
        if key in seen:
            continue
        seen.add(key)

        items.append({"title": title, "url": full_url})

        if len(items) >= limit:
            break

    return items


def aksu_check_new(state: dict) -> tuple[list[dict], dict, int, list[dict]]:
    seen_urls = set(state.get("aksu_seen_urls", []))

    items = aksu_fetch_items(limit=120)
    new_items = [it for it in items if it["url"] not in seen_urls]

    for it in items:
        seen_urls.add(it["url"])
    state["aksu_seen_urls"] = list(seen_urls)[:800]

    sample = items[:5]
    return new_items, state, len(items), sample


# =========================
# Main
# =========================
def main() -> None:
    if HEARTBEAT_ENABLED:
        send_telegram("HEARTBEAT: Bot tetiklendi.")

    state = load_state()

    aksu_new = []
    aksu_total = 0
    aksu_sample = []

    if AKSU_ENABLED:
        aksu_new, state, aksu_total, aksu_sample = aksu_check_new(state)

    if INIT_SILENT:
        save_state(state)
        if DEBUG_ENABLED:
            send_telegram(
                "DEBUG (INIT_SILENT)\n"
                f"Aksu toplam={aksu_total}, yeni={len(aksu_new)}\n"
                f"Örnekler:\n" + "\n".join([f"- {x['title']} | {x['url']}" for x in aksu_sample])
            )
        return

    if DEBUG_ENABLED:
        send_telegram(
            "DEBUG\n"
            f"Aksu toplam={aksu_total}, yeni={len(aksu_new)}\n"
            f"Örnekler:\n" + "\n".join([f"- {x['title']} | {x['url']}" for x in aksu_sample])
        )

    for it in aksu_new:
        send_telegram(f"🆕 Aksu link:\n{it['title']}\n{it['url']}")
        time.sleep(1)

    save_state(state)


if __name__ == "__main__":
    main()
