import os
import json
import time
import requests
import urllib3

# SSL uyarılarını kapat
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# =========================
# Telegram
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_IDS = os.getenv("CHAT_IDS", "")
CHAT_ID_LIST = [x.strip() for x in CHAT_IDS.split(",") if x.strip()]

DEBUG_ENABLED = os.getenv("DEBUG", "0") == "1"
INIT_SILENT = os.getenv("INIT_SILENT", "0") == "1"

STATE_PATH = "state.json"

# =========================
# ilan.gov.tr API
# =========================
ILAN_ENABLED = os.getenv("ILAN_ENABLED", "1") == "1"
ILAN_BASE_URL = "https://www.ilan.gov.tr"
ILAN_ENDPOINT = f"{ILAN_BASE_URL}/api/api/services/app/Ad/AdsByFilter"

ILAN_SEARCH_TEXT = os.getenv("ILAN_SEARCH_TEXT", "kiralama").strip()
ILAN_CITY_PLATE = int(os.getenv("ILAN_CITY_PLATE", "7"))  # Antalya=7
ILAN_PAGE_SIZE = int(os.getenv("ILAN_PAGE_SIZE", "30"))
ILAN_MAX_PAGES = int(os.getenv("ILAN_MAX_PAGES", "10"))

EXCLUDE_WORDS = [
    "iflas",
    "konkordato",
    "teblig",
    "tebliğ",
    "mahkeme",
    "icra",
    "mühlet",
]

REQUIRE_IHALE_IN_URL = os.getenv("REQUIRE_IHALE_IN_URL", "1") == "1"


def send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for chat_id in CHAT_ID_LIST:
        requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=30)


def load_state() -> dict:
    if not os.path.exists(STATE_PATH):
        return {}
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def ilan_fetch_page(skip_count: int) -> list[dict]:
    headers = {
        "content-type": "application/json-patch+json",
        "user-agent": "Mozilla/5.0",
        "accept": "text/plain",
    }

    payload = {
        "keys": {
            "q": [ILAN_SEARCH_TEXT] if ILAN_SEARCH_TEXT else [],
            "aci": [ILAN_CITY_PLATE],
        },
        "skipCount": skip_count,
        "maxResultCount": ILAN_PAGE_SIZE,
    }

    r = requests.post(
        ILAN_ENDPOINT,
        json=payload,
        headers=headers,
        timeout=45,
        verify=False,   # 🔥 SSL FIX
    )

    data = r.json()
    ads = (data.get("result") or {}).get("ads") or []

    out = []
    for ad in ads:
        out.append(
            {
                "id": str(ad.get("id")),
                "title": (ad.get("title") or "").strip(),
                "url_str": (ad.get("urlStr") or "").strip(),
                "url": ILAN_BASE_URL + (ad.get("urlStr") or ""),
            }
        )
    return out


def ilan_collect():
    all_items = []
    raw_total = 0

    for page_idx in range(ILAN_MAX_PAGES):
        skip = page_idx * ILAN_PAGE_SIZE
        items = ilan_fetch_page(skip)
        raw_total += len(items)
        all_items.extend(items)

        if not items:
            break

        time.sleep(0.3)

    filtered = []

    for it in all_items:
        t = it["title"].lower()
        u = it["url_str"].lower()

        if "kiralama" not in t:
            continue

        if REQUIRE_IHALE_IN_URL and "ihale" not in u:
            continue

        if any(w in t for w in EXCLUDE_WORDS):
            continue

        filtered.append(it)

    uniq = {}
    for it in filtered:
        uniq[it["id"]] = it

    return list(uniq.values()), raw_total


def main():
    if not ILAN_ENABLED:
        return

    state = load_state()
    seen = set(state.get("ilan_seen_ids", []))

    items, raw_total = ilan_collect()
    new_items = [x for x in items if x["id"] not in seen]

    if DEBUG_ENABLED:
        send_telegram(
            f"DEBUG\nraw_total={raw_total}\nfiltre_sonrasi={len(items)}\nyeni={len(new_items)}"
        )

    if INIT_SILENT:
        for x in items:
            seen.add(x["id"])
        state["ilan_seen_ids"] = list(seen)
        save_state(state)
        return

    for it in new_items:
        send_telegram(
            f"🆕 Antalya kiralama ihalesi:\n{it['title']}\n{it['url']}"
        )
        seen.add(it["id"])
        time.sleep(1)

    state["ilan_seen_ids"] = list(seen)
    save_state(state)


if __name__ == "__main__":
    main()
