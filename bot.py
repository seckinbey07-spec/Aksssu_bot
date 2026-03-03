import os
import json
import time
import requests

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
ILAN_MAX_PAGES = int(os.getenv("ILAN_MAX_PAGES", "10"))  # 10*30=300 ilan tarar

# Alakasızları elemek için
EXCLUDE_WORDS = [
    "iflas",
    "konkordato",
    "teblig",
    "tebliğ",
    "mahkeme",
    "icra",
    "mühlet",
    "kordato",
]

# urlStr'de ihale sinyali (en iyi pratik filtre)
REQUIRE_IHALE_IN_URL = os.getenv("REQUIRE_IHALE_IN_URL", "1") == "1"


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


def ilan_fetch_page(skip_count: int) -> list[dict]:
    headers = {
        "content-type": "application/json-patch+json",
        "user-agent": "Mozilla/5.0",
        "accept": "text/plain",
    }

    # ilan.gov UI: ?aci=7 (plaka). API tarafında da keys içine aci gönderiyoruz.
    payload = {
        "keys": {
            "q": [ILAN_SEARCH_TEXT] if ILAN_SEARCH_TEXT else [],
            "aci": [ILAN_CITY_PLATE],
        },
        "skipCount": skip_count,
        "maxResultCount": ILAN_PAGE_SIZE,
    }

    r = requests.post(ILAN_ENDPOINT, json=payload, headers=headers, timeout=45)
    r.raise_for_status()

    data = r.json()
    ads = (data.get("result") or {}).get("ads") or []

    out = []
    for ad in ads:
        ad_id = str(ad.get("id"))
        title = (ad.get("title") or "").strip()
        url_str = (ad.get("urlStr") or "").strip()
        out.append(
            {
                "id": ad_id,
                "title": title,
                "url_str": url_str,
                "url": ILAN_BASE_URL + url_str if url_str else ILAN_BASE_URL,
            }
        )
    return out


def ilan_collect() -> tuple[list[dict], int]:
    all_items: list[dict] = []
    raw_total = 0

    for page_idx in range(ILAN_MAX_PAGES):
        skip = page_idx * ILAN_PAGE_SIZE
        items = ilan_fetch_page(skip)
        raw_total += len(items)
        all_items.extend(items)

        if not items:
            break

        time.sleep(0.35)

    filtered = []
    for it in all_items:
        t = (it.get("title") or "").lower()
        u = (it.get("url_str") or "").lower()

        if not it.get("id"):
            continue

        # kiralama kelimesi mutlaka başlıkta olsun
        if "kiralama" not in t:
            continue

        # ihale sinyali: urlStr içinde ihale
        if REQUIRE_IHALE_IN_URL and "ihale" not in u:
            continue

        # alakasız kelimeleri ele
        if any(w in t for w in EXCLUDE_WORDS) or any(w in u for w in EXCLUDE_WORDS):
            continue

        filtered.append(it)

    # id bazlı tekilleştir
    uniq = {}
    for it in filtered:
        uniq[it["id"]] = it

    return list(uniq.values()), raw_total


def main() -> None:
    if not ILAN_ENABLED:
        if DEBUG_ENABLED:
            send_telegram("DEBUG: ILAN_ENABLED=0 (kapalı)")
        return

    state = load_state()
    seen = set(state.get("ilan_seen_ids", []))

    items, raw_total = ilan_collect()
    new_items = [x for x in items if x["id"] not in seen]

    if INIT_SILENT:
        for x in items:
            seen.add(x["id"])
        state["ilan_seen_ids"] = list(seen)[:5000]
        save_state(state)
        if DEBUG_ENABLED:
            send_telegram(
                "DEBUG (INIT_SILENT)\n"
                f"raw_total={raw_total}\nfiltre_sonrasi={len(items)}\nsetlenen={len(items)}\n"
                f"aci={ILAN_CITY_PLATE} q='{ILAN_SEARCH_TEXT}' pages={ILAN_MAX_PAGES} size={ILAN_PAGE_SIZE}"
            )
        return

    if DEBUG_ENABLED:
        send_telegram(
            "DEBUG\n"
            f"raw_total={raw_total}\nfiltre_sonrasi={len(items)}\nyeni={len(new_items)}\n"
            f"aci={ILAN_CITY_PLATE} q='{ILAN_SEARCH_TEXT}' pages={ILAN_MAX_PAGES} size={ILAN_PAGE_SIZE}"
        )

    for it in new_items:
        send_telegram(f"🆕 Antalya kiralama ihalesi (ilan.gov.tr):\n{it['title']}\n{it['url']}")
        seen.add(it["id"])
        time.sleep(1)

    state["ilan_seen_ids"] = list(seen)[:5000]
    save_state(state)


if __name__ == "__main__":
    main()
