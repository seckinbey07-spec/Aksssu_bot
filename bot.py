import os
import json
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

AKSU_URL = os.getenv("AKSU_URL", "https://www.aksu.bel.tr/ihaleler")

ILAN_ENABLED = os.getenv("ILAN_ENABLED", "1") == "1"
ILAN_BASE_URL = "https://www.ilan.gov.tr"
ILAN_SEARCH_ENDPOINT = f"{ILAN_BASE_URL}/api/api/services/app/Ad/AdsByFilter"
ILAN_AD_TYPE_ID = int(os.getenv("ILAN_AD_TYPE_ID", "3"))
ILAN_SEARCH_TEXT = os.getenv("ILAN_SEARCH_TEXT", "kiralama")
ILAN_PAGE_SIZE = int(os.getenv("ILAN_PAGE_SIZE", "20"))

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

HEARTBEAT_ENABLED = os.getenv("HEARTBEAT", "0") == "1"
DEBUG_ENABLED = os.getenv("DEBUG", "0") == "1"

STATE_PATH = "state.json"


def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=30)


def load_state():
    if not os.path.exists(STATE_PATH):
        return {}
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def ilan_api_search():
    headers = {
        "content-type": "application/json-patch+json",
        "user-agent": "Mozilla/5.0",
    }

    payload = {
        "keys": {
            "q": [ILAN_SEARCH_TEXT],
            "ats": [ILAN_AD_TYPE_ID],
        },
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

    if r.status_code != 200:
        return [], 0

    data = r.json()
    ads = (data.get("result") or {}).get("ads") or []

    results = []
    for ad in ads:
        results.append({
            "id": str(ad.get("id")),
            "title": ad.get("title"),
            "url": ILAN_BASE_URL + (ad.get("urlStr") or "")
        })

    return results, len(ads)


def ilan_check_new(state):
    seen = set(state.get("ilan_seen_ids", []))

    items, raw_count = ilan_api_search()
    new_items = [x for x in items if x["id"] not in seen]

    for x in items:
        seen.add(x["id"])

    state["ilan_seen_ids"] = list(seen)[:1000]

    return new_items, state, raw_count


def main():
    state = load_state()

    ilan_new = []
    raw = 0

    if ILAN_ENABLED:
        ilan_new, state, raw = ilan_check_new(state)

    if DEBUG_ENABLED:
        send_telegram(
            f"DEBUG\nilan raw={raw}\nyeni={len(ilan_new)}\nArama='{ILAN_SEARCH_TEXT}'"
        )

    for it in ilan_new:
        send_telegram(
            f"🆕 ilan.gov.tr kiralama ihalesi:\n{it['title']}\n{it['url']}"
        )
        time.sleep(1)

    save_state(state)


if __name__ == "__main__":
    main()
