#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

# cloudscraper (Cloudflare/WAF bypass için)
try:
    import cloudscraper  # type: ignore
except Exception:
    cloudscraper = None


# -----------------------------
# ENV
# -----------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_IDS_RAW = os.getenv("CHAT_IDS", "").strip()
DEBUG = os.getenv("DEBUG", "0").strip() in ("1", "true", "TRUE", "yes", "YES", "on", "ON")

LIST_URL_TEMPLATE = os.getenv(
    "LIST_URL_TEMPLATE",
    "https://www.ilan.gov.tr/ilan/kategori/9/ihale-duyurulari?currentPage={page}&ats=3"
).strip()

MAX_PAGES = int(os.getenv("MAX_PAGES", "6"))
MAX_SEND_PER_RUN = int(os.getenv("MAX_SEND_PER_RUN", "6"))

CACHE_DIR = os.getenv("CACHE_DIR", ".cache")
SEEN_FILE = os.path.join(CACHE_DIR, "seen.json")

REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "45"))
SLEEP_BETWEEN = float(os.getenv("SLEEP_BETWEEN", "0.6"))

# -----------------------------
# Antalya + Kiralama filtreleri
# -----------------------------
ANTALYA_TOKENS = [
    "antalya",
    "aksu", "kepez", "muratpaşa", "muratpasa", "konyaaltı", "konyaalti",
    "döşemealtı", "dosemealti", "serik", "manavgat", "alanya", "kaş", "kas",
    "kemer", "kumluca", "finike", "demre", "elmalı", "elmali", "gazipaşa", "gazipasa",
    "akseki", "ibradı", "ibradi", "gündoğmuş", "gundogmus",
]

POSITIVE_HINTS = [
    "kiralama ihalesi",
    "kira ihalesi",
    "kiralanacaktır", "kiralanacakt",
    "kiraya verilece", "kiraya verilecek",
    "kiraya verilmesi",
    "işletme hakkı", "isletme hakki",
    "kullanım hakkı", "kullanim hakki",
    "ihale",
    "kiralama",
    "kira",
]

NEGATIVE_HINTS = [
    "konkordato",
    "iflas",
    "tasfiye",
    "icra",
    "satış", "satis",
    "arsa",
    "taşınmaz satışı", "tasinmaz satisi",
    "mahkeme",
    "dava",
    "kamulaştırma", "kamulastirma",
    "haciz",
    "ipotek",
]

ALLOW_KIRAYA_VERME = True  # istemiyorsan False yap

# -----------------------------
# Regex
# -----------------------------
NEXT_DATA_RE = re.compile(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL | re.IGNORECASE)
ADV_RE = re.compile(r"\badv=([A-Z]\d{6})\b", re.IGNORECASE)
HREF_ILAN_RE = re.compile(r'href\s*=\s*[\'"]?(/ilan/[^\'"\s>]+)', re.IGNORECASE)
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL | re.IGNORECASE)
OG_TITLE_RE = re.compile(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']', re.IGNORECASE)


# -----------------------------
# Helpers
# -----------------------------
def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def norm_text(s: Any) -> str:
    if s is None:
        return ""
    s = str(s).lower().strip()
    s = s.replace("ı", "i").replace("ğ", "g").replace("ü", "u").replace("ş", "s").replace("ö", "o").replace("ç", "c")
    s = re.sub(r"\s+", " ", s)
    return s


def contains_any(text: str, needles: List[str]) -> bool:
    t = norm_text(text)
    for n in needles:
        if norm_text(n) in t:
            return True
    return False


def antalya_score(text: str) -> int:
    t = norm_text(text)
    score = 0
    if "antalya" in t:
        score += 3
    for tok in set(ANTALYA_TOKENS):
        nt = norm_text(tok)
        if nt and nt != "antalya" and nt in t:
            score += 2
    return score


def looks_like_kiralama(text: str) -> bool:
    t = norm_text(text)
    if not t:
        return False
    if not ALLOW_KIRAYA_VERME and "kiraya verme" in t:
        return False
    if not contains_any(t, POSITIVE_HINTS):
        return False
    if contains_any(t, NEGATIVE_HINTS):
        return False
    return True


def ensure_cache_dir():
    os.makedirs(CACHE_DIR, exist_ok=True)


def load_seen() -> Dict[str, str]:
    ensure_cache_dir()
    if not os.path.exists(SEEN_FILE):
        return {}
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            obj = json.load(f)
        if isinstance(obj, dict):
            return {str(k): str(v) for k, v in obj.items()}
        return {}
    except Exception:
        return {}


def save_seen(seen: Dict[str, str]) -> None:
    ensure_cache_dir()
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)


def walk_json(obj: Any) -> Iterable[Any]:
    stack = [obj]
    while stack:
        cur = stack.pop()
        yield cur
        if isinstance(cur, dict):
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)


# -----------------------------
# Telegram
# -----------------------------
def tg_send(chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    try:
        requests.post(url, json=payload, timeout=30)
    except Exception:
        pass


def tg_broadcast(chat_ids: List[str], text: str) -> None:
    for cid in chat_ids:
        tg_send(cid, text)
        time.sleep(0.25)


# -----------------------------
# HTTP client (cloudscraper + session)
# -----------------------------
def make_session():
    if cloudscraper is not None:
        s = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "desktop": True}
        )
        return s
    return requests.Session()


SESSION = make_session()


def http_get(url: str) -> requests.Response:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.7,en;q=0.6",
        "Connection": "keep-alive",
    }
    return SESSION.get(url, headers=headers, timeout=REQUEST_TIMEOUT)


def http_get_json(url: str) -> Any:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.7,en;q=0.6",
        "Connection": "keep-alive",
    }
    r = SESSION.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


# -----------------------------
# ilan.gov.tr parsing
# -----------------------------
def extract_next_data(html: str) -> Optional[Dict[str, Any]]:
    m = NEXT_DATA_RE.search(html)
    if not m:
        return None
    raw = m.group(1).strip()
    try:
        return json.loads(raw)
    except Exception:
        return None


def detect_block_page(html: str) -> bool:
    t = norm_text(html[:5000])
    # Cloudflare / bot protection / access denied sinyalleri
    bad_markers = [
        "cloudflare",
        "attention required",
        "access denied",
        "captcha",
        "bot detection",
        "verify you are human",
    ]
    return any(m in t for m in bad_markers)


def next_data_build_id(next_data: Dict[str, Any]) -> Optional[str]:
    bid = next_data.get("buildId")
    if isinstance(bid, str) and bid.strip():
        return bid.strip()
    return None


def build_next_data_url(build_id: str, page: int) -> str:
    # Next.js data route: /_next/data/<buildId>/ilan/kategori/9/ihale-duyurulari.json?currentPage=0&ats=3
    return f"https://www.ilan.gov.tr/_next/data/{build_id}/ilan/kategori/9/ihale-duyurulari.json?currentPage={page}&ats=3"


def pick_candidates_from_any_json(j: Any, page: int) -> List[Dict[str, str]]:
    """
    JSON içinde:
      - /ilan/ linkleri
      - adv=Mxxxxxx kodları
    arar ve URL üretir.
    """
    out: List[Dict[str, str]] = []
    seen = set()

    # JSON dump üzerinden adv yakalama (en sağlam)
    dumped = ""
    try:
        dumped = json.dumps(j, ensure_ascii=False)
    except Exception:
        dumped = str(j)

    # adv kodları
    advs = [a.upper() for a in ADV_RE.findall(dumped)]
    for adv in list(dict.fromkeys(advs)):
        key = f"ADV::{adv}"
        if key in seen:
            continue
        seen.add(key)
        url_adv = f"https://www.ilan.gov.tr/ilan/kategori/9/ihale-duyurulari?adv={adv}&currentPage={page}"
        out.append({"title": f"İlan ({adv})", "url": url_adv})

    # /ilan/ linkleri (varsa)
    for node in walk_json(j):
        if not isinstance(node, (dict, str)):
            continue
        if isinstance(node, dict):
            url = node.get("url") or node.get("link") or node.get("path") or node.get("href")
            title = node.get("title") or node.get("name") or node.get("baslik")
            if not url:
                continue
            url_s = str(url).strip()
            if "/ilan/" not in url_s:
                continue
            if url_s.startswith("/"):
                url_s = "https://www.ilan.gov.tr" + url_s
            if url_s in seen:
                continue
            seen.add(url_s)
            out.append({"title": str(title).strip() if title else "İlan", "url": url_s})
        else:
            # string node
            s = str(node)
            if "/ilan/" in s:
                # kaba yakalama: /ilan/..../
                pass

    return out


def pick_candidates_from_html(html: str, page: int) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    seen = set()

    # /ilan/ href
    for lk in HREF_ILAN_RE.findall(html):
        url = "https://www.ilan.gov.tr" + lk if lk.startswith("/") else lk
        if url in seen:
            continue
        seen.add(url)
        out.append({"title": "İlan", "url": url})

    # adv=
    advs = [a.upper() for a in ADV_RE.findall(html)]
    for adv in list(dict.fromkeys(advs)):
        key = f"ADV::{adv}"
        if key in seen:
            continue
        seen.add(key)
        url_adv = f"https://www.ilan.gov.tr/ilan/kategori/9/ihale-duyurulari?adv={adv}&currentPage={page}"
        out.append({"title": f"İlan ({adv})", "url": url_adv})

    return out


def extract_title_from_html(html: str) -> str:
    m = OG_TITLE_RE.search(html)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()[:140]
    m = TITLE_RE.search(html)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()[:140]
    return "İlan"


def html_to_text(html: str) -> str:
    html = re.sub(r"<script.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<[^>]+>", " ", html)
    html = re.sub(r"\s+", " ", html).strip()
    return html


def fetch_list_page(page: int) -> Tuple[List[Dict[str, str]], str]:
    url = LIST_URL_TEMPLATE.format(page=page)
    r = http_get(url)

    if r.status_code != 200:
        return [], f"LIST {page} HTTP {r.status_code}"

    html = r.text or ""

    # Koruma/boş sayfa tespiti
    if detect_block_page(html):
        return [], f"LIST {page} BLOCKED (protection page detected)"

    # 1) __NEXT_DATA__ -> buildId -> JSON data route
    nd = extract_next_data(html)
    if nd:
        bid = next_data_build_id(nd)
        if bid:
            data_url = build_next_data_url(bid, page)
            try:
                j = http_get_json(data_url)
                cands = pick_candidates_from_any_json(j, page)
                if cands:
                    return cands, f"LIST {page} OK (next_data_json candidates={len(cands)})"
                else:
                    return [], f"LIST {page} OK (next_data_json candidates=0)"
            except Exception as e:
                return [], f"LIST {page} next_data_json ERROR {type(e).__name__}"

    # 2) HTML fallback
    cands = pick_candidates_from_html(html, page)
    return cands, f"LIST {page} OK (html_fallback candidates={len(cands)})"


def fetch_detail(url: str) -> Tuple[str, str, str]:
    r = http_get(url)
    if r.status_code != 200:
        return "", "İlan", f"DETAIL HTTP {r.status_code}"
    html = r.text or ""
    if detect_block_page(html):
        return "", "İlan", "DETAIL BLOCKED"
    title = extract_title_from_html(html)
    text = html_to_text(html)
    return text, title, "DETAIL OK"


def make_id(url: str) -> str:
    m = ADV_RE.search(url)
    if m:
        return m.group(1).upper()
    m = re.search(r"/ilan/(\d+)", url)
    if m:
        return m.group(1)
    return url


def format_msg(title: str, url: str) -> str:
    title = title.strip() if title else "İlan"
    return f"📌 {title}\n🔗 {url}"


# -----------------------------
# MAIN
# -----------------------------
def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN missing")
    if not CHAT_IDS_RAW:
        raise SystemExit("CHAT_IDS missing")

    chat_ids = [c.strip() for c in CHAT_IDS_RAW.split(",") if c.strip()]
    if not chat_ids:
        raise SystemExit("CHAT_IDS parse empty")

    seen = load_seen()

    list_candidates_total = 0
    detail_checked = 0
    filtered = 0
    new_count = 0
    sent = 0

    debug_lines: List[str] = []
    hits: List[Tuple[str, str]] = []

    for page in range(MAX_PAGES):
        cands, status = fetch_list_page(page)
        debug_lines.append(status)
        list_candidates_total += len(cands)

        # aday yoksa sonraki sayfa
        if not cands:
            time.sleep(SLEEP_BETWEEN)
            continue

        # hızlı sıralama: Antalya kelimesi title/url’da geçenleri öne al
        def rank(c: Dict[str, str]) -> int:
            blob = norm_text(c.get("title", "") + " " + c.get("url", ""))
            return 1 if ("antalya" in blob or any(norm_text(t) in blob for t in ANTALYA_TOKENS)) else 0

        cands.sort(key=rank, reverse=True)

        for c in cands:
            if len(hits) >= MAX_SEND_PER_RUN:
                break

            url = c.get("url", "")
            if not url:
                continue

            iid = make_id(url)
            if iid in seen:
                continue

            text, title, dstatus = fetch_detail(url)
            detail_checked += 1

            if DEBUG and detail_checked <= 15:
                debug_lines.append(f"{iid} {dstatus} title={title[:70]}")

            if not text:
                continue

            blob = f"{title} {text}"

            # Antalya filtresi
            a_score = antalya_score(blob)
            if "antalya" not in norm_text(blob):
                if a_score < 4:  # antalya yoksa en az 2 ilçe
                    continue
            else:
                if a_score < 3:
                    continue

            # Kiralama + negatif
            if not looks_like_kiralama(blob):
                continue

            filtered += 1
            seen[iid] = now_iso()
            hits.append((title, url))
            new_count += 1

            time.sleep(SLEEP_BETWEEN)

        if len(hits) >= MAX_SEND_PER_RUN:
            break

        time.sleep(SLEEP_BETWEEN)

    save_seen(seen)

    for title, url in hits[:MAX_SEND_PER_RUN]:
        tg_broadcast(chat_ids, format_msg(title, url))
        sent += 1
        time.sleep(0.4)

    if DEBUG:
        summary = [
            "🧪 DEBUG antalya_ihale_bot",
            f"time={now_iso()}",
            f"list_pages={MAX_PAGES}",
            f"list_candidates_total={list_candidates_total}",
            f"detail_checked={detail_checked}",
            f"filtre_sonrasi={filtered}",
            f"yeni={new_count}",
            f"sent={sent}",
            "—",
        ]
        tail = debug_lines[-16:] if len(debug_lines) > 16 else debug_lines
        summary.extend([str(x)[:350] for x in tail])
        tg_broadcast(chat_ids, "\n".join(summary))

    print(json.dumps({
        "time": now_iso(),
        "list_candidates_total": list_candidates_total,
        "detail_checked": detail_checked,
        "filtered": filtered,
        "new": new_count,
        "sent": sent,
    }, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        msg = f"❌ Bot error\n{type(e).__name__}: {str(e)[:300]}"
        print(msg)
        if BOT_TOKEN and CHAT_IDS_RAW:
            chat_ids = [c.strip() for c in CHAT_IDS_RAW.split(",") if c.strip()]
            if chat_ids:
                tg_broadcast(chat_ids, msg)
        raise
