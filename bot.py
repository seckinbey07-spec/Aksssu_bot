#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

import requests  # sadece Telegram için
import urllib3

try:
    import certifi  # type: ignore
    CA_BUNDLE = certifi.where()
except Exception:
    CA_BUNDLE = None

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# -----------------------------
# ENV
# -----------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_IDS_RAW = os.getenv("CHAT_IDS", "").strip()
DEBUG = os.getenv("DEBUG", "0").strip() in ("1", "true", "TRUE", "yes", "YES", "on", "ON")

MAX_SEND_PER_RUN = int(os.getenv("MAX_SEND_PER_RUN", "6"))
MAX_SITEMAP_URLS = int(os.getenv("MAX_SITEMAP_URLS", "250"))  # sitemap’ten en fazla bu kadar url incele
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "45"))
SLEEP_BETWEEN = float(os.getenv("SLEEP_BETWEEN", "0.5"))

CACHE_DIR = os.getenv("CACHE_DIR", ".cache")
SEEN_FILE = os.path.join(CACHE_DIR, "seen.json")

# Sitemap kaynakları (sırayla dener)
SITEMAP_URLS = [
    "https://www.ilan.gov.tr/sitemap/ads.xml",
    "https://www.ilan.gov.tr/sitemap/daily-categories.xml",
    "https://www.ilan.gov.tr/sitemap/categories.xml",
]

# -----------------------------
# Filtreler
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

ALLOW_KIRAYA_VERME = True

# -----------------------------
# Regex / parse
# -----------------------------
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
# HTTP (ilan.gov.tr) - urllib3
# -----------------------------
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

HTTP_OK = urllib3.PoolManager(
    cert_reqs="CERT_REQUIRED",
    ca_certs=CA_BUNDLE,
    timeout=urllib3.Timeout(total=REQUEST_TIMEOUT),
    retries=False,
)

HTTP_INSECURE = urllib3.PoolManager(
    cert_reqs="CERT_NONE",
    assert_hostname=False,
    timeout=urllib3.Timeout(total=REQUEST_TIMEOUT),
    retries=False,
)


def u3_get(url: str, accept: str) -> Tuple[int, bytes]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": accept,
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.7,en;q=0.6",
        "Connection": "keep-alive",
    }
    try:
        r = HTTP_OK.request("GET", url, headers=headers)
        return int(r.status), (r.data or b"")
    except Exception:
        r = HTTP_INSECURE.request("GET", url, headers=headers)
        return int(r.status), (r.data or b"")


def detect_block_page(html: str) -> bool:
    t = norm_text(html[:8000])
    bad = ["cloudflare", "attention required", "access denied", "captcha", "verify you are human", "bot"]
    return any(x in t for x in bad)


# -----------------------------
# Sitemap parsing
# -----------------------------
def parse_sitemap_xml(xml_bytes: bytes) -> Tuple[List[str], str]:
    """
    Returns (loc_list, kind)
    kind: "urlset" or "sitemapindex" or "unknown"
    """
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return [], "parse_error"

    tag = root.tag.lower()
    if "urlset" in tag:
        locs = []
        for url_el in root.findall(".//{*}url"):
            loc = url_el.find("{*}loc")
            if loc is not None and loc.text:
                locs.append(loc.text.strip())
        return locs, "urlset"

    if "sitemapindex" in tag:
        locs = []
        for sm_el in root.findall(".//{*}sitemap"):
            loc = sm_el.find("{*}loc")
            if loc is not None and loc.text:
                locs.append(loc.text.strip())
        return locs, "sitemapindex"

    return [], "unknown"


def fetch_sitemap_urls(debug_lines: List[str]) -> List[str]:
    """
    1) SITEMAP_URLS içinden ilk erişilebilen sitemap’i alır
    2) sitemapindex ise içindeki ilk birkaç sitemap’i çekip URL’leri toplar
    """
    all_urls: List[str] = []

    for sm in SITEMAP_URLS:
        code, data = u3_get(sm, "application/xml,text/xml,*/*")
        if code != 200 or not data:
            debug_lines.append(f"SITEMAP {sm} HTTP {code}")
            continue

        locs, kind = parse_sitemap_xml(data)
        debug_lines.append(f"SITEMAP {sm} OK kind={kind} locs={len(locs)}")

        if kind == "urlset":
            all_urls.extend(locs)
            break

        if kind == "sitemapindex":
            # index içinden ilk 6 alt sitemap’i dene (çok büyüyebilir)
            submaps = locs[:6]
            for sub in submaps:
                c2, d2 = u3_get(sub, "application/xml,text/xml,*/*")
                if c2 != 200 or not d2:
                    debug_lines.append(f"SUBMAP {sub} HTTP {c2}")
                    continue
                u2, k2 = parse_sitemap_xml(d2)
                debug_lines.append(f"SUBMAP {sub} OK kind={k2} locs={len(u2)}")
                if k2 == "urlset":
                    all_urls.extend(u2)
                # limit
                if len(all_urls) >= MAX_SITEMAP_URLS:
                    break
            break

    # Deduplicate + limit
    dedup = list(dict.fromkeys(all_urls))
    return dedup[:MAX_SITEMAP_URLS]


# -----------------------------
# Detail fetch + filter
# -----------------------------
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


def make_id(url: str) -> str:
    # /ilan/2021740/ gibi
    m = re.search(r"/ilan/(\d+)", url)
    if m:
        return m.group(1)
    return url


def fetch_detail(url: str) -> Tuple[str, str, str]:
    code, data = u3_get(url, "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
    if code != 200:
        return "", "İlan", f"DETAIL HTTP {code}"
    html = data.decode("utf-8", errors="ignore")
    if detect_block_page(html):
        return "", "İlan", "DETAIL BLOCKED"
    title = extract_title_from_html(html)
    text = html_to_text(html)
    return text, title, "DETAIL OK"


def format_msg(title: str, url: str) -> str:
    return f"📌 {title.strip() if title else 'İlan'}\n🔗 {url}"


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

    debug_lines: List[str] = []
    urls = fetch_sitemap_urls(debug_lines)

    sitemap_total = len(urls)
    detail_checked = 0
    filtered = 0
    new_count = 0
    sent = 0

    hits: List[Tuple[str, str]] = []

    # Sadece ilan detaylarını hedefle (sitemap bazen kategori sayfalarını da döndürebilir)
    urls = [u for u in urls if "/ilan/" in u]

    for url in urls:
        if len(hits) >= MAX_SEND_PER_RUN:
            break

        iid = make_id(url)
        if iid in seen:
            continue

        text, title, st = fetch_detail(url)
        detail_checked += 1

        if DEBUG and detail_checked <= 12:
            debug_lines.append(f"{iid} {st} title={title[:70]}")

        if not text:
            continue

        blob = f"{title} {text}"

        a_score = antalya_score(blob)
        if "antalya" not in norm_text(blob):
            if a_score < 4:
                continue
        else:
            if a_score < 3:
                continue

        if not looks_like_kiralama(blob):
            continue

        filtered += 1
        seen[iid] = now_iso()
        hits.append((title, url))
        new_count += 1

        time.sleep(SLEEP_BETWEEN)

    save_seen(seen)

    for title, url in hits[:MAX_SEND_PER_RUN]:
        tg_broadcast(chat_ids, format_msg(title, url))
        sent += 1
        time.sleep(0.4)

    if DEBUG:
        summary = [
            "🧪 DEBUG antalya_ihale_bot (sitemap mode)",
            f"time={now_iso()}",
            f"sitemap_urls_total={sitemap_total}",
            f"sitemap_ilan_urls={len(urls)}",
            f"detail_checked={detail_checked}",
            f"filtre_sonrasi={filtered}",
            f"yeni={new_count}",
            f"sent={sent}",
            "—",
        ]
        tail = debug_lines[-18:] if len(debug_lines) > 18 else debug_lines
        summary.extend([str(x)[:350] for x in tail])
        tg_broadcast(chat_ids, "\n".join(summary))

    print(json.dumps({
        "time": now_iso(),
        "sitemap_urls_total": sitemap_total,
        "sitemap_ilan_urls": len(urls),
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
