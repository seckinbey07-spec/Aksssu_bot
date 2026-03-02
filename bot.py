#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# -----------------------------
# ENV
# -----------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_IDS_RAW = os.getenv("CHAT_IDS", "").strip()
DEBUG = os.getenv("DEBUG", "0").strip() in ("1", "true", "TRUE", "yes", "YES", "on", "ON")

# ilan.gov.tr liste sayfası (ihale duyuruları)
# İstersen kategori değiştir: IHale=9; icra=... vs.
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
# Helpers
# -----------------------------
def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def norm_text(s: Any) -> str:
    if s is None:
        return ""
    s = str(s).lower().strip()
    # TR normalize
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

    pos = contains_any(t, POSITIVE_HINTS)
    if not pos:
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
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    r = requests.post(url, json=payload, timeout=30)
    # Telegram hata verirse workflow patlamasın diye raise etmiyoruz
    if r.status_code != 200 and DEBUG:
        print(f"Telegram error: {r.status_code} {r.text[:400]}")


def tg_broadcast(chat_ids: List[str], text: str) -> None:
    for cid in chat_ids:
        tg_send(cid, text)
        time.sleep(0.3)


# -----------------------------
# HTTP fetch (SSL fallback)
# -----------------------------
def http_get(url: str, params: Optional[Dict[str, Any]] = None) -> requests.Response:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; antalya_ihale_bot/2.0)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        r = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
        return r
    except requests.exceptions.SSLError:
        # yalnızca sertifika doğrulama patlarsa verify kapat
        r = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT, verify=False)
        return r


# -----------------------------
# ilan.gov.tr parsing
# -----------------------------
NEXT_DATA_RE = re.compile(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL | re.IGNORECASE)

def extract_next_data(html: str) -> Optional[Dict[str, Any]]:
    m = NEXT_DATA_RE.search(html)
    if not m:
        return None
    raw = m.group(1).strip()
    try:
        return json.loads(raw)
    except Exception:
        return None


def walk_json(obj: Any) -> Iterable[Any]:
    """Generic DFS over JSON structures."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        yield cur
        if isinstance(cur, dict):
            for v in cur.values():
                stack.append(v)
        elif isinstance(cur, list):
            for v in cur:
                stack.append(v)


def pick_candidates(next_data: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Next.js state içinden ilan adaylarını toplar.
    Aday kriteri:
      - url/link/path benzeri alan içeren dict
      - title/name benzeri alan içeren dict
    """
    out: List[Dict[str, str]] = []
    seen = set()

    for node in walk_json(next_data):
        if not isinstance(node, dict):
            continue

        # olası url alanları
        url = node.get("url") or node.get("link") or node.get("path") or node.get("href")
        title = node.get("title") or node.get("name") or node.get("baslik")

        if not url or not title:
            continue

        url_s = str(url).strip()
        title_s = str(title).strip()

        # ilan detay linki genelde /ilan/<id>/... şeklinde
        if "/ilan/" not in url_s:
            continue

        if url_s.startswith("/"):
            url_s = "https://www.ilan.gov.tr" + url_s

        key = f"{title_s}::{url_s}"
        if key in seen:
            continue
        seen.add(key)

        out.append({"title": title_s, "url": url_s})

    return out


def fetch_list_page(page: int) -> Tuple[List[Dict[str, str]], str]:
    url = LIST_URL_TEMPLATE.format(page=page)
    r = http_get(url)
    if r.status_code != 200:
        return [], f"LIST {page} HTTP {r.status_code}"

    html = r.text
    nd = extract_next_data(html)
    if not nd:
        # fallback: html içinden ilan linklerini kaba regexle topla
        links = re.findall(r'href="(/ilan/\d+/[^"]+)"', html, flags=re.IGNORECASE)
        out = []
        for lk in list(dict.fromkeys(links))[:300]:
            out.append({"title": "İlan", "url": "https://www.ilan.gov.tr" + lk})
        return out, f"LIST {page} OK (fallback links={len(out)})"

    cands = pick_candidates(nd)
    return cands, f"LIST {page} OK (next_data candidates={len(cands)})"


def fetch_detail_text(url: str) -> Tuple[str, str]:
    r = http_get(url)
    if r.status_code != 200:
        return "", f"DETAIL HTTP {r.status_code}"

    html = r.text
    # sayfadaki görünen texti kabaca çıkar
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text, "DETAIL OK"


def make_id(url: str) -> str:
    # /ilan/<id>/... yakala
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

    total_list_candidates = 0
    total_detail_checked = 0
    filtered = 0
    new_count = 0
    sent = 0

    debug_lines: List[str] = []
    new_hits: List[Tuple[str, str]] = []  # (title, url)

    for page in range(MAX_PAGES):
        cands, status = fetch_list_page(page)
        debug_lines.append(status)
        total_list_candidates += len(cands)

        # adayları sırala: önce antalya kelimesi geçen title/url üstte
        def rank(c: Dict[str, str]) -> int:
            blob = norm_text(c.get("title", "") + " " + c.get("url", ""))
            return 1 if ("antalya" in blob or any(norm_text(t) in blob for t in ANTALYA_TOKENS)) else 0
        cands.sort(key=rank, reverse=True)

        for c in cands:
            if len(new_hits) >= MAX_SEND_PER_RUN:
                break

            title = c.get("title", "İlan")
            url = c.get("url", "")
            if not url:
                continue

            iid = make_id(url)
            if iid in seen:
                continue

            # detay indir, filtreyi detay metninde yap
            text, dstatus = fetch_detail_text(url)
            total_detail_checked += 1

            if DEBUG and total_detail_checked <= 20:
                debug_lines.append(f"{iid} {dstatus} url={url[:90]}")

            if not text:
                continue

            # Antalya filtresi (detaydan)
            a_score = antalya_score(text + " " + title)
            if "antalya" not in norm_text(text + " " + title):
                # antalya kelimesi yoksa en az 2 ilçe yakalasın
                if a_score < 4:
                    continue
            else:
                # antalya varsa yeter
                if a_score < 3:
                    continue

            # kiralama filtresi
            if not looks_like_kiralama(text + " " + title):
                continue

            filtered += 1

            # yeni
            seen[iid] = now_iso()
            new_hits.append((title, url))
            new_count += 1

            time.sleep(SLEEP_BETWEEN)

        if len(new_hits) >= MAX_SEND_PER_RUN:
            break

        time.sleep(SLEEP_BETWEEN)

    save_seen(seen)

    # gönder
    for title, url in new_hits[:MAX_SEND_PER_RUN]:
        msg = format_msg(title, url)
        tg_broadcast(chat_ids, msg)
        sent += 1
        time.sleep(0.5)

    # debug özet
    if DEBUG:
        summary = [
            "🧪 DEBUG antalya_ihale_bot",
            f"time={now_iso()}",
            f"list_pages={MAX_PAGES}",
            f"list_candidates_total={total_list_candidates}",
            f"detail_checked={total_detail_checked}",
            f"filtre_sonrasi={filtered}",
            f"yeni={new_count}",
            f"sent={sent}",
            "—",
        ]
        # çok uzamasın: son 12 satır
        tail = debug_lines[-12:] if len(debug_lines) > 12 else debug_lines
        summary.extend([str(x)[:350] for x in tail])
        tg_broadcast(chat_ids, "\n".join(summary))

    # workflow log
    print(json.dumps({
        "time": now_iso(),
        "list_candidates_total": total_list_candidates,
        "detail_checked": total_detail_checked,
        "filtered": filtered,
        "new": new_count,
        "sent": sent,
    }, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # workflow patlamasın diye (ama debug açıksa telegrama düşsün)
        msg = f"❌ Bot error\n{type(e).__name__}: {str(e)[:300]}"
        print(msg)
        if BOT_TOKEN and CHAT_IDS_RAW:
            chat_ids = [c.strip() for c in CHAT_IDS_RAW.split(",") if c.strip()]
            if chat_ids:
                tg_broadcast(chat_ids, msg)
        raise
