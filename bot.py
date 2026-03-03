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
# ilan.gov.tr API
# =========================
ILAN_ENABLED = os.getenv("ILAN_ENABLED", "1") == "1"
ILAN_BASE_URL = "https://www.ilan.gov.tr"
ILAN_ENDPOINT = f"{ILAN_BASE_URL}/api/api/services/app/Ad/AdsByFilter"

ILAN_SEARCH_TEXT = os.getenv("ILAN_SEARCH_TEXT", "kiralama").strip()
ILAN_CITY_PLATE = int(os.getenv("ILAN_CITY_PLATE", "7"))  # Antalya=7
ILAN_PAGE_SIZE = int(os.getenv("ILAN_PAGE_SIZE", "30"))
ILAN_MAX_PAGES = int(os.getenv("ILAN_MAX_PAGES", "10"))

TARGET_CITY_TEXT = os.getenv("ILAN_CITY_TEXT", "antalya").strip().lower()

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


def _post_ads_by_filter(payload: dict) -> dict:
    headers = {
        "content-type": "application/json-patch+json",
        "user-agent": "Mozilla/5.0",
        "accept": "text/plain",
    }
    r = requests.post(
        ILAN_ENDPOINT,
        json=payload,
        headers=headers,
        timeout=45,
        verify=False,  # GitHub Actions SSL fix
    )
    r.raise_for_status()
    return r.json()


def _parse_ads(data: dict) -> list[dict]:
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


def ilan_fetch_page_best_variant(skip_count: int, forced_variant: str | None) -> tuple[list[dict], str, dict]:
    """
    - forced_variant verildiyse sadece onu dener.
    - yoksa 3 varyantı dener, en çok sonuç döndüreni seçer.
    Dönen: items, chosen_variant, counts_debug
    """
    base = {
        "skipCount": skip_count,
        "maxResultCount": ILAN_PAGE_SIZE,
    }

    variants = [
        ("v1_aci_list", {"keys": {"q": [ILAN_SEARCH_TEXT], "aci": [ILAN_CITY_PLATE]}}),
        ("v2_aci_int", {"keys": {"q": [ILAN_SEARCH_TEXT], "aci": ILAN_CITY_PLATE}}),
        ("v3_no_aci", {"keys": {"q": [ILAN_SEARCH_TEXT]}}),
    ]

    if forced_variant:
        variants = [v for v in variants if v[0] == forced_variant]

    best_name = "none"
    best_items: list[dict] = []
    counts = {}

    for name, extra in variants:
        payload = {**base, **extra}
        try:
            data = _post_ads_by_filter(payload)
            items = _parse_ads(data)
            counts[name] = len(items)

            if len(items) > len(best_items):
                best_items = items
                best_name = name
        except Exception as e:
            counts[name] = f"ERR:{type(e).__name__}"
            continue

    return best_items, best_name, counts


def is_relevant(it: dict) -> bool:
    t = (it.get("title") or "").lower()
    u = (it.get("url_str") or "").lower()
    combined = f"{t} {u}"

    # Antalya (aci çalışmasa bile)
    if TARGET_CITY_TEXT and TARGET_CITY_TEXT not in combined:
        return False

    # kiralama
    if "kiralama" not in combined:
        return False

    # ihale sinyali: urlStr veya title içinde ihale olsun
    if REQUIRE_IHALE_IN_URL:
        if "ihale" not in u and "ihale" not in t:
            return False

    # alakasızlar
    if any(w in combined for w in EXCLUDE_WORDS):
        return False

    return True


def ilan_collect() -> tuple[list[dict], int, str, dict]:
    """
    İlk sayfada en iyi varyantı seçer, sonra aynı varyantla sayfalar.
    """
    raw_total = 0
    chosen_variant = None
    first_page_counts = {}

    all_items: list[dict] = []

    for page_idx in range(ILAN_MAX_PAGES):
        skip = page_idx * ILAN_PAGE_SIZE

        if page_idx == 0:
            items, chosen_variant, counts = ilan_fetch_page_best_variant(skip, forced_variant=None)
            first_page_counts = counts
        else:
            items, _, _ = ilan_fetch_page_best_variant(skip, forced_variant=chosen_variant)

        raw_total += len(items)
        all_items.extend(items)

        if not items:
            break

        time.sleep(0.3)

    # filtre + tekilleştir
    filtered = [x for x in all_items if x.get("id") and is_relevant(x)]
    uniq = {}
    for x in filtered:
        uniq[x["id"]] = x

    return list(uniq.values()), raw_total, (chosen_variant or "none"), first_page_counts


def main() -> None:
    if not ILAN_ENABLED:
        if DEBUG_ENABLED:
            send_telegram("DEBUG: ILAN_ENABLED=0")
        return

    state = load_state()
    seen = set(state.get("ilan_seen_ids", []))

    items, raw_total, variant, counts = ilan_collect()
    new_items = [x for x in items if x["id"] not in seen]

    if INIT_SILENT:
        for x in items:
            seen.add(x["id"])
        state["ilan_seen_ids"] = list(seen)[:5000]
        save_state(state)
        if DEBUG_ENABLED:
            send_telegram(
                "DEBUG (INIT_SILENT)\n"
                f"first_page_counts={counts}\n"
                f"chosen_variant={variant}\n"
                f"raw_total={raw_total}\nfiltre_sonrasi={len(items)}\nsetlenen={len(items)}\n"
                f"q='{ILAN_SEARCH_TEXT}' target='{TARGET_CITY_TEXT}' pages={ILAN_MAX_PAGES} size={ILAN_PAGE_SIZE}"
            )
        return

    if DEBUG_ENABLED:
        send_telegram(
            "DEBUG\n"
            f"first_page_counts={counts}\n"
            f"chosen_variant={variant}\n"
            f"raw_total={raw_total}\nfiltre_sonrasi={len(items)}\nyeni={len(new_items)}\n"
            f"q='{ILAN_SEARCH_TEXT}' target='{TARGET_CITY_TEXT}' pages={ILAN_MAX_PAGES} size={ILAN_PAGE_SIZE}"
        )

    for it in new_items:
        send_telegram(f"🆕 Antalya kiralama ihalesi (ilan.gov.tr):\n{it['title']}\n{it['url']}")
        seen.add(it["id"])
        time.sleep(1)

    state["ilan_seen_ids"] = list(seen)[:5000]
    save_state(state)


if __name__ == "__main__":
    main()
