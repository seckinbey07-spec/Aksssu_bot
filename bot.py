import os
import json
import time
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_IDS = os.getenv("CHAT_IDS", "")
CHAT_ID_LIST = [x.strip() for x in CHAT_IDS.split(",") if x.strip()]

DEBUG_ENABLED = os.getenv("DEBUG", "0") == "1"

ILAN_ENABLED = os.getenv("ILAN_ENABLED", "1") == "1"
ILAN_BASE_URL = "https://www.ilan.gov.tr"
ILAN_SEARCH_ENDPOINT = f"{ILAN_BASE_URL}/api/api/services/app/Ad/AdsByFilter"
ILAN_SEARCH_TEXT = os.getenv("ILAN_SEARCH_TEXT", "kiralama")
ILAN_PAGE_SIZE = int(os.getenv("ILAN_PAGE_SIZE", "20"))

STATE_PATH = "state.json"


def send_telegram(text: str) -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN eksik.")
    if not CHAT_ID_LIST:
        raise RuntimeError("CHAT_IDS eksik. Örn: 123456789")

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


def ilan_search() -> list[dict]:
    headers = {
        "content-type": "application/json-patch+json",
        "user-agent": "Mozilla/5.0",
    }
    payload = {
        "keys": {"q": [ILAN_SEARCH_TEXT]},
        "skipCount": 0,
        "maxResultCount": ILAN_PAGE_SIZE,
    }

    r = requests.post(
        ILAN_SEARCH_ENDPOINT,
        json=payload,
        headers=headers,
        timeout=45,
        verify=False,
    )
    r.raise_for_status()

    data = r.json()
    ads = (data.get("result") or {}).get("ads") or []

    out = []
    for ad in ads:
        out.append(
            {
                "id": str(ad.get("id")),
                "title": ad.get("title") or "",
                "url": ILAN_BASE_URL + (ad.get("urlStr") or ""),
            }
        )
    return out


def main():
    state = load_state()
    seen = set(state.get("ilan_seen_ids", []))

    items = ilan_search() if ILAN_ENABLED else []
    new_items = [x for x in items if x["id"] and x["id"] not in seen]

    if DEBUG_ENABLED:
        send_telegram(f"DEBUG: bulunan={len(items)} yeni={len(new_items)}")

    for it in new_items:
        send_telegram(f"🆕 ilan.gov.tr:\n{it['title']}\n{it['url']}")
        seen.add(it["id"])
        time.sleep(1)

    state["ilan_seen_ids"] = list(seen)[:2000]
    save_state(state)


if __name__ == "__main__":
    main()
