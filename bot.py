#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_IDS_RAW = os.getenv("CHAT_IDS", "").strip()
DEBUG = os.getenv("DEBUG", "0") in ("1", "true", "TRUE")

ILAN_API_URL = os.getenv("ILAN_API_URL", "").strip() or "https://www.ilan.gov.tr/api/ilan/search"
SEARCH_QUERY = os.getenv("SEARCH_QUERY", "kiralama").strip()

PAGE_SIZE = int(os.getenv("PAGE_SIZE", "20"))
MAX_PAGES = int(os.getenv("MAX_PAGES", "6"))
MAX_SEND_PER_RUN = int(os.getenv("MAX_SEND_PER_RUN", "6"))

CACHE_DIR = ".cache"
SEEN_FILE = os.path.join(CACHE_DIR, "seen.json")

ANTALYA_TOKENS = [
    "antalya","aksu","kepez","muratpasa","konyaalti","dosemealti",
    "serik","manavgat","alanya","kas","kemer","kumluca",
    "finike","demre","elmali","gazipasa","akseki"
]

NEGATIVE_HINTS = [
    "konkordato","iflas","tasfiye","icra",
    "satis","arsa","mahkeme","dava","haciz"
]


def norm(s):
    if not s:
        return ""
    s = str(s).lower()
    s = s.replace("ı","i").replace("ğ","g").replace("ü","u").replace("ş","s").replace("ö","o").replace("ç","c")
    return re.sub(r"\s+"," ",s)


def contains_any(text, arr):
    t = norm(text)
    return any(norm(a) in t for a in arr)


def looks_valid(text):
    t = norm(text)
    if "kira" not in t:
        return False
    if contains_any(t, NEGATIVE_HINTS):
        return False
    return True


def antalya_match(text):
    t = norm(text)
    if "antalya" in t:
        return True
    count = sum(1 for x in ANTALYA_TOKENS if x in t)
    return count >= 2


def tg_send(chat_id, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True
    }, timeout=30)


def load_seen():
    os.makedirs(CACHE_DIR, exist_ok=True)
    if not os.path.exists(SEEN_FILE):
        return {}
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_seen(data):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def extract_items(payload):
    if isinstance(payload, dict):
        for key in ["data","content","results","items"]:
            if key in payload and isinstance(payload[key], list):
                return payload[key]
    if isinstance(payload, list):
        return payload
    return []


def fetch_page(page):
    params = {"q": SEARCH_QUERY, "page": page, "size": PAGE_SIZE}
    headers = {"User-Agent":"Mozilla/5.0"}

    try:
        r = requests.get(ILAN_API_URL, params=params, headers=headers, timeout=40)
    except requests.exceptions.SSLError:
        # SSL fallback
        r = requests.get(ILAN_API_URL, params=params, headers=headers, timeout=40, verify=False)

    r.raise_for_status()
    return extract_items(r.json())


def build_blob(item):
    fields = []
    for k in ["title","description","city","address","institutionName","url"]:
        if k in item:
            fields.append(str(item[k]))
    return " | ".join(fields)


def main():
    if not BOT_TOKEN or not CHAT_IDS_RAW:
        raise SystemExit("Secrets missing")

    chat_ids = [x.strip() for x in CHAT_IDS_RAW.split(",") if x.strip()]
    seen = load_seen()

    total_raw = 0
    total_filtered = 0
    total_new = 0
    sent = 0

    new_items = []

    for page in range(MAX_PAGES):
        items = fetch_page(page)
        total_raw += len(items)

        for it in items:
            blob = build_blob(it)

            if not antalya_match(blob):
                continue

            if not looks_valid(blob):
                continue

            total_filtered += 1

            iid = str(it.get("id") or it.get("ilanNo") or it.get("url"))
            if iid in seen:
                continue

            seen[iid] = datetime.now().isoformat()
            new_items.append(it)
            total_new += 1

        if len(new_items) >= MAX_SEND_PER_RUN:
            break

    save_seen(seen)

    for it in new_items[:MAX_SEND_PER_RUN]:
        title = it.get("title","İlan")
        url = it.get("url","")
        msg = f"📌 {title}\n🔗 {url}"
        for cid in chat_ids:
            tg_send(cid, msg)
        sent += 1
        time.sleep(0.8)

    if DEBUG:
        debug_msg = (
            f"DEBUG\n"
            f"raw={total_raw}\n"
            f"filtre_sonrasi={total_filtered}\n"
            f"yeni={total_new}\n"
            f"sent={sent}"
        )
        for cid in chat_ids:
            tg_send(cid, debug_msg)


if __name__ == "__main__":
    main()
