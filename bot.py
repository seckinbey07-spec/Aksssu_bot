import os
import json
import time
import requests
import urllib3

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
# ilan.gov.tr
# =========================
ILAN_ENABLED = os.getenv("ILAN_ENABLED", "1") == "1"
ILAN_BASE_URL = "https://www.ilan.gov.tr"
ILAN_SEARCH_ENDPOINT = f"{ILAN_BASE_URL}/api/api/services/app/Ad/AdsByFilter"
ILAN_SEARCH_TEXT = os.getenv("ILAN_SEARCH_TEXT", "kiralama").strip()
ILAN_PAGE_SIZE = int(os.getenv("ILAN_PAGE_SIZE", "50"))

# Antalya hedef
TARGET_CITY = (os.getenv("ILAN_CITY_NAME", "Antalya") or "Antalya").strip().lower()

# İhale dışı / alakasızları elemek için
EXCLUDE_WORDS = [
    "iflas",
    "konkordato",
    "mahkeme",
    "teblig",
    "tebliğ",
    "arsa",
    "icra",
]

# URL’de ihale sinyali
IHale_URL_KEYWORDS = [
    "ihale",
    "ihale-duyurulari",
]


def send_telegram(text: str) -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN eksik.")
    if not CHAT_ID_LIST:
        raise RuntimeError("CHAT_IDS eksik. Örn: 8714272187")

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


def ilan_api_fetch() -> tuple[list[dict], int]:
    headers = {
        "content-type": "application/json-patch+json",
        "user-agent": "Mozilla/5.0",
        "accept": "text/plain",
    }

    payload = {
        "keys": {"q": [ILAN_SEARCH_TEXT]} if ILAN_SEARCH_TEXT else {},
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
    raw_count = len(ads)

    items = []
    for ad in ads:
        url_str = (ad.get("urlStr") or "").strip()
        title = (ad.get("title") or "").strip()
        city = (ad.get("addressCityName") or "").strip()

        items.append(
            {
                "id": str(ad.get("id")),
                "title": title,
                "city": city,
                "url_str": url_str,
                "url": ILAN_BASE_URL + url_str if url_str else ILAN_BASE_URL,
            }
        )

    return items, raw_count


def is_relevant(item: dict) -> bool:
    title_l = (item.get("title") or "").lower()
    city_l = (item.get("city") or "").lower()
    url_l = (item.get("url_str") or "").lower()

    combined = f"{title_l} {city_l} {url_l}"

    # Antalya sinyali: title/city/url içinde antalya geçsin
    if TARGET_CITY and TARGET_CITY not in combined:
        return False

    # Kiralama mutlaka geçsin (başlıkta tercih)
    if "kiralama" not in title_l and "kiralama" not in combined:
        return False

    # İhale sayfası olmalı: url’de ihale sinyali arıyoruz
    if not any(k in url_l for k in IHale_URL_KEYWORDS):
        return False

    # Alakasızları ele
    if any(w in combined for w in EXCLUDE_WORDS):
        return False

    return True


def main() -> None:
    state = load_state()
    seen = set(state.get("ilan_seen_ids", []))

    items = []
    raw = 0
    if ILAN_ENABLED:
        items, raw = ilan_api_fetch()

    filtered = [x for x in items if x.get("id") and is_relevant(x)]
    new_items = [x for x in filtered if x["id"] not in seen]

    # İlk kurulum sessiz: yeni sayma, sadece state doldur
    if INIT_SILENT:
        for x in filtered:
            seen.add(x["id"])
        state["ilan_seen_ids"] = list(seen)[:3000]
        save_state(state)
        if DEBUG_ENABLED:
            send_telegram(
                "DEBUG (INIT_SILENT)\n"
                f"raw={raw}\nfiltre_sonrasi={len(filtered)}\nsetlenen={len(filtered)}\n"
                f"Arama='{ILAN_SEARCH_TEXT}' sehir='{TARGET_CITY}'"
            )
        return

    if DEBUG_ENABLED:
        send_telegram(
            "DEBUG\n"
            f"raw={raw}\nfiltre_sonrasi={len(filtered)}\nyeni={len(new_items)}\n"
            f"Arama='{ILAN_SEARCH_TEXT}' sehir='{TARGET_CITY}'"
        )

    for it in new_items:
        send_telegram(f"🆕 Antalya kiralama ihalesi (ilan.gov.tr):\n{it['title']}\n{it['url']}")
        seen.add(it["id"])
        time.sleep(1)

    state["ilan_seen_ids"] = list(seen)[:3000]
    save_state(state)


if __name__ == "__main__":
    main()
