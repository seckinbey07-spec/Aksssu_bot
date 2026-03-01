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

STATE_PATH = "state.json"

ILAN_BASE_URL = "https://www.ilan.gov.tr"
ILAN_SEARCH_ENDPOINT = f"{ILAN_BASE_URL}/api/api/services/app/Ad/AdsByFilter"

# Sadece kiralama kelimesi ile çekiyoruz
ILAN_SEARCH_TEXT = "kiralama"
ILAN_PAGE_SIZE = 50


EXCLUDE_WORDS = [
    "iflas",
    "konkordato",
    "arsa",
    "mahkeme",
    "teblig",
    "geçici mühlet",
    "kesin mühlet",
]


def send_telegram(text: str):
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


def ilan_search():
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

    data = r.json()
    ads = (data.get("result") or {}).get("ads") or []

    results = []

    for ad in ads:
        title = (ad.get("title") or "").lower()
        url_str = ad.get("urlStr") or ""
        full_url = ILAN_BASE_URL + url_str

        # Antalya geçmeli
        if "antalya" not in title:
            continue

        # ihale kelimesi geçmeli
        if "ihale" not in title:
            continue

        # İstenmeyen kelimeleri ele
        if any(word in title for word in EXCLUDE_WORDS):
            continue

        results.append({
            "id": str(ad.get("id")),
            "title": ad.get("title"),
            "url": full_url
        })

    return results, len(ads)


def main():
    state = load_state()
    seen = set(state.get("seen_ids", []))

    items, raw_count = ilan_search()
    new_items = [x for x in items if x["id"] not in seen]

    if DEBUG_ENABLED:
        send_telegram(f"DEBUG\nraw={raw_count}\nfiltre_sonrasi={len(items)}\nyeni={len(new_items)}")

    for it in new_items:
        send_telegram(f"🆕 Antalya kiralama ihalesi:\n{it['title']}\n{it['url']}")
        seen.add(it["id"])
        time.sleep(1)

    state["seen_ids"] = list(seen)
    save_state(state)


if __name__ == "__main__":
    main()
