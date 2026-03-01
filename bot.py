import os
import json
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# =========================
# Aksu Belediyesi Kaynağı
# =========================
AKSU_URL = os.getenv("AKSU_URL", "https://www.aksu.bel.tr/ihaleler")

# =========================
# ilan.gov.tr Kaynağı (Antalya + kiralama)
# =========================
ILAN_ENABLED = os.getenv("ILAN_ENABLED", "1") == "1"
ILAN_BASE_URL = "https://www.ilan.gov.tr"
ILAN_SEARCH_ENDPOINT = f"{ILAN_BASE_URL}/api/api/services/app/Ad/AdsByFilter"
ILAN_AD_TYPE_ID = int(os.getenv("ILAN_AD_TYPE_ID", "3"))  # 3 = İhale
ILAN_CITY_PLATE = int(os.getenv("ILAN_CITY_PLATE", "7"))  # 07 = Antalya
ILAN_SEARCH_TEXT = os.getenv("ILAN_SEARCH_TEXT", "kiralama")
ILAN_PAGE_SIZE = int(os.getenv("ILAN_PAGE_SIZE", "20"))

# =========================
# Telegram
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# HEARTBEAT (default kapalı)
HEARTBEAT_ENABLED = os.getenv("HEARTBEAT", "0") == "1"

# DEBUG (default kapalı) -> açıkken 1 kez özet mesaj atar
DEBUG_ENABLED = os.getenv("DEBUG", "0") == "1"

# State
STATE_PATH = "state.json"


def send_telegram(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("BOT_TOKEN veya CHAT_ID eksik.")
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
    return requests.get(url, timeout=45, headers={"User-Agent": "Mozilla/5.0"})


# =========================
# 1) AKSU: HTML’den ihale linkleri
# =========================
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
# 2) ILAN.GOV.TR: API ile Antalya + kiralama ihaleleri
# =========================
def ilan_api_search_ads(
    search_text: str,
    city_plate: int,
    ad_type_id: int,
    page_size: int = 20,
    current_page: int = 1,
) -> tuple[list[dict], int]:
    headers = {
        "accept": "text/plain",
        "content-type": "application/json-patch+json",
        "origin": "https://www.ilan.gov.tr",
        "referer": "https://www.ilan.gov.tr/ilan/tum-ilanlar",
        "user-agent": "Mozilla/5.0",
        "x-requested-with": "XMLHttpRequest",
    }

    keys = {}
    if search_text:
        keys["q"] = [search_text]

    keys["aci"] = [city_plate]
    keys["ats"] = [ad_type_id]

    skip_count = (current_page - 1) * page_size
    payload = {"keys": keys, "skipCount": skip_count, "maxResultCount": page_size}

    r = requests.post(
        ILAN_SEARCH_ENDPOINT,
        json=payload,
        headers=headers,
        timeout=45,
        verify=False,
    )
    r.raise_for_status()
    data = r.json()

    result = data.get("result") or {}
    ads = result.get("ads") or []
    raw_count = len(ads)

    out = []
    for ad in ads:
        url_str = ad.get("urlStr") or ""
        out.append(
            {
                "id": str(ad.get("id")),
                "title": ad.get("title") or "",
                "full_url": f"{ILAN_BASE_URL}{url_str}" if url_str else ILAN_BASE_URL,
                "publish_date": ad.get("publishStartDate"),
                "advertiser": ad.get("advertiserName"),
                "city": ad.get("addressCityName"),
                "county": ad.get("addressCountyName"),
            }
        )

    # DİKKAT: Bu filtre çok sert olabilir; debug için sayım alıyoruz.
    kw = search_text.lower().strip()
    if kw:
        out = [x for x in out if kw in (x["title"] or "").lower()]

    return out, raw_count


def ilan_check_new(state: dict) -> tuple[list[dict], dict, int, int]:
    seen_ids = set(state.get("ilan_seen_ids", []))

    items, raw_count = ilan_api_search_ads(
        search_text=ILAN_SEARCH_TEXT,
        city_plate=ILAN_CITY_PLATE,
        ad_type_id=ILAN_AD_TYPE_ID,
        page_size=ILAN_PAGE_SIZE,
        current_page=1,
    )

    new_items = [it for it in items if it["id"] and it["id"] not in seen_ids]

    for it in items:
        if it["id"]:
            seen_ids.add(it["id"])

    state["ilan_seen_ids"] = list(seen_ids)[:1000]
    return new_items, state, raw_count, len(items)


def main() -> None:
    if HEARTBEAT_ENABLED:
        send_telegram("HEARTBEAT: Bot çalıştı ve tetiklendi.")

    state = load_state()

    aksu_new, state, aksu_total = aksu_check_new(state)

    ilan_new = []
    ilan_raw = 0
    ilan_after_kw = 0
    if ILAN_ENABLED:
        ilan_new, state, ilan_raw, ilan_after_kw = ilan_check_new(state)

    # DEBUG: tek seferlik özet (spam değil)
    if DEBUG_ENABLED:
        msg = (
            "DEBUG ÖZET\n"
            f"Aksu: listelenen={aksu_total}, yeni={len(aksu_new)}\n"
            f"ilan.gov.tr: raw={ilan_raw}, kw_sonrası={ilan_after_kw}, yeni={len(ilan_new)}\n"
            f"Arama='{ILAN_SEARCH_TEXT}', plaka={ILAN_CITY_PLATE}, type={ILAN_AD_TYPE_ID}"
        )
        send_telegram(msg)

    # Normal bildirimler
    if aksu_new:
        for it in aksu_new:
            send_telegram(f"🆕 Aksu Belediyesi ihale:\n{it['title']}\n{it['url']}")
            time.sleep(1)

    if ILAN_ENABLED and ilan_new:
        for it in ilan_new:
            extra = []
            if it.get("county"):
                extra.append(it["county"])
            if it.get("publish_date"):
                extra.append(f"Yayın: {it['publish_date']}")
            extra_line = ("\n" + " | ".join(extra)) if extra else ""
            send_telegram(
                f"🆕 Antalya (ilan.gov.tr) kiralama ihalesi:\n{it['title']}\n{it['full_url']}{extra_line}"
            )
            time.sleep(1)

    save_state(state)


if __name__ == "__main__":
    main()
