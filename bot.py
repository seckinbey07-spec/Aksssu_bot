import os
import json
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# =========================
# Telegram
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_IDS = os.getenv("CHAT_IDS", "")
CHAT_ID_LIST = [x.strip() for x in CHAT_IDS.split(",") if x.strip()]

HEARTBEAT_ENABLED = os.getenv("HEARTBEAT", "0") == "1"
DEBUG_ENABLED = os.getenv("DEBUG", "0") == "1"
INIT_SILENT = os.getenv("INIT_SILENT", "0") == "1"

STATE_PATH = "state.json"


def send_telegram(text: str) -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN eksik.")
    if not CHAT_ID_LIST:
        raise RuntimeError("CHAT_IDS eksik. Örn: 8714272187 veya 111,222,-100xxx")

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
# ilan.gov.tr (Antalya + kiralama)
# =========================
ILAN_ENABLED = os.getenv("ILAN_ENABLED", "1") == "1"
ILAN_BASE_URL = "https://www.ilan.gov.tr"
ILAN_SEARCH_ENDPOINT = f"{ILAN_BASE_URL}/api/api/services/app/Ad/AdsByFilter"
ILAN_SEARCH_TEXT = os.getenv("ILAN_SEARCH_TEXT", "kiralama").strip()
ILAN_CITY_NAME = os.getenv("ILAN_CITY_NAME", "Antalya").strip().lower()
ILAN_PAGE_SIZE = int(os.getenv("ILAN_PAGE_SIZE", "30"))


def ilan_search() -> tuple[list[dict], int]:
    headers = {
        "content-type": "application/json-patch+json",
        "user-agent": "Mozilla/5.0",
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
        verify=False,  # GitHub Actions SSL hatası için pratik çözüm
    )
    r.raise_for_status()

    data = r.json()
    ads = (data.get("result") or {}).get("ads") or []
    raw_count = len(ads)

    out = []
    for ad in ads:
        city = (ad.get("addressCityName") or "").strip()
        title = (ad.get("title") or "").strip()
        url_str = (ad.get("urlStr") or "").strip()

        out.append(
            {
                "id": str(ad.get("id")),
                "title": title,
                "city": city,
                "url": ILAN_BASE_URL + url_str if url_str else ILAN_BASE_URL,
            }
        )

    # Başlıkta arama kelimesi geçsin (spam azaltır)
    if ILAN_SEARCH_TEXT:
        kw = ILAN_SEARCH_TEXT.lower()
        out = [x for x in out if kw in (x.get("title") or "").lower()]

    # Antalya filtresi (şehir alanı boş olabiliyor; başlık + şehir metninde arıyoruz)
    if ILAN_CITY_NAME:
        city_kw = ILAN_CITY_NAME.lower()
        filtered = []
        for x in out:
            combined = f"{x.get('title','')} {x.get('city','')}".lower()
            if city_kw in combined:
                filtered.append(x)
        out = filtered

    return out, raw_count


def ilan_check_new(state: dict) -> tuple[list[dict], dict, int, int]:
    seen_ids = set(state.get("ilan_seen_ids", []))

    items, raw_count = ilan_search()
    new_items = [x for x in items if x["id"] and x["id"] not in seen_ids]

    for x in items:
        if x["id"]:
            seen_ids.add(x["id"])

    state["ilan_seen_ids"] = list(seen_ids)[:2000]
    return new_items, state, raw_count, len(items)


# =========================
# EKAP (Stub - sonraki adımda dolduracağız)
# =========================
EKAP_ENABLED = os.getenv("EKAP_ENABLED", "0") == "1"


def ekap_check_new(state: dict) -> tuple[list[dict], dict]:
    return [], state


# =========================
# Main
# =========================
def main():
    if HEARTBEAT_ENABLED:
        send_telegram("HEARTBEAT: Bot tetiklendi.")

    state = load_state()

    aksu_new = []
    aksu_total = 0
    if AKSU_ENABLED:
        aksu_new, state, aksu_total = aksu_check_new(state)

    ilan_new = []
    ilan_raw = 0
    ilan_after = 0
    if ILAN_ENABLED:
        ilan_new, state, ilan_raw, ilan_after = ilan_check_new(state)

    ekap_new = []
    if EKAP_ENABLED:
        ekap_new, state = ekap_check_new(state)

    # İlk kurulum sessiz mod: state doldur, mesaj atma
    if INIT_SILENT:
        save_state(state)
        if DEBUG_ENABLED:
            send_telegram(
                "DEBUG (INIT_SILENT)\n"
                f"Aksu toplam={aksu_total} yeni={len(aksu_new)}\n"
                f"ilan raw={ilan_raw} antalya+kw={ilan_after} yeni={len(ilan_new)}\n"
                f"EKAP yeni={len(ekap_new)}"
            )
        return

    if DEBUG_ENABLED:
        send_telegram(
            "DEBUG ÖZET\n"
            f"Aksu toplam={aksu_total}, yeni={len(aksu_new)}\n"
            f"ilan raw={ilan_raw}, antalya+kw={ilan_after}, yeni={len(ilan_new)}\n"
            f"EKAP yeni={len(ekap_new)}"
        )

    for it in aksu_new:
        send_telegram(f"🆕 Aksu Belediyesi ihale:\n{it['title']}\n{it['url']}")
        time.sleep(1)

    for it in ilan_new:
        send_telegram(f"🆕 Antalya kiralama (ilan.gov.tr):\n{it['title']}\n{it['url']}")
        time.sleep(1)

    save_state(state)


if __name__ == "__main__":
    main()
