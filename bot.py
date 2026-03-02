#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests


# -----------------------------
# ENV
# -----------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_IDS_RAW = os.getenv("CHAT_IDS", "").strip()  # "id1,id2"
DEBUG = os.getenv("DEBUG", "0").strip() in ("1", "true", "TRUE", "yes", "YES", "on", "ON")

# ilan.gov.tr API (senin mevcut scriptinde çalışan endpoint'i buraya koyabilirsin)
# Eğer env ile override etmek istersen: ILAN_API_URL secret/env ekleyebilirsin.
ILAN_API_URL = os.getenv("ILAN_API_URL", "").strip() or "https://www.ilan.gov.tr/api/ilan/search"

# Arama parametresi (çok genişse bile filtreyi biz sertleştiriyoruz)
SEARCH_QUERY = os.getenv("SEARCH_QUERY", "kiralama").strip()

# Pagination / tarama limitleri
PAGE_SIZE = int(os.getenv("PAGE_SIZE", "20"))
MAX_PAGES = int(os.getenv("MAX_PAGES", "6"))  # 6 sayfa * 20 = 120 sonuç

# Çıktı/spam limitleri
MAX_SEND_PER_RUN = int(os.getenv("MAX_SEND_PER_RUN", "6"))

# Seen cache dosyası (Actions cache ile kalıcılaştıracağız)
CACHE_DIR = os.getenv("CACHE_DIR", ".cache")
SEEN_FILE = os.path.join(CACHE_DIR, "seen.json")


# -----------------------------
# Antalya hedefleri
# -----------------------------
ANTALYA_TOKENS = [
    # İl
    "antalya",
    # İlçeler (Aksu dahil) - gerekirse genişlet
    "aksu", "kepez", "muratpaşa", "konyaaltı", "döşemealtı", "dosemealti",
    "serik", "manavgat", "alanya", "kaş", "kas", "kalkan", "kemer",
    "kumluca", "finike", "demre", "elmalı", "elmali", "gazipaşa", "gazipasa",
    "gündoğmuş", "gundogmus", "ibradı", "ibradi", "akseki",
]

# Kiralama/ihale pozitif sinyaller (ilan metni farklı yazımlarla gelebilir)
POSITIVE_HINTS = [
    "kiralama ihalesi",
    "kira ihalesi",
    "kiraya ver",
    "kiraya verilece",
    "kiralanacaktır",
    "kiralanacakt",
    "kiralama ve hizmet alımı",
    "hizmet alımı",
    "işletme hakkı",
    "kullanim hakki",
    "kullanım hakkı",
    "işletme hakkının kiraya",
    "ihale",
]

# Kesin istemediklerin (spam ve alakasız içerik kırpma)
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
    "mirastan",
    "hisseli",
    "kamulaştırma", "kamulastirma",
    "ipotek",
    "haciz",
    "ihale ilanı satış", "ihale ilani satis",
]

# Eğer “kiraya verme” (belediye taşınmaz kiralama) istiyorsan bu kalsın.
# İstemiyorsan NEGATIVE_HINTS içine "kiraya verme" ekle.
ALLOW_KIRAYA_VERME = True


# -----------------------------
# Helpers
# -----------------------------
def die(msg: str, code: int = 1):
    raise SystemExit(msg)


def now_tr() -> str:
    # GitHub runner UTC; biz sadece log için ISO kullanıyoruz
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def norm_text(s: Any) -> str:
    if s is None:
        return ""
    if isinstance(s, (dict, list)):
        try:
            s = json.dumps(s, ensure_ascii=False)
        except Exception:
            s = str(s)
    s = str(s)
    s = s.lower().strip()
    # Turkish normalization (minimal)
    s = s.replace("ı", "i").replace("ğ", "g").replace("ü", "u").replace("ş", "s").replace("ö", "o").replace("ç", "c")
    s = re.sub(r"\s+", " ", s)
    return s


def contains_any(hay: str, needles: List[str]) -> bool:
    h = norm_text(hay)
    for n in needles:
        if norm_text(n) in h:
            return True
    return False


def score_antalyaness(fields_blob: str) -> int:
    """
    Antalya sinyal skoru:
    - antalya geçerse +3
    - ilçe geçerse +2 (her benzersiz ilçe için)
    """
    text = norm_text(fields_blob)
    score = 0
    if "antalya" in text:
        score += 3
    # ilçeler
    for t in set(ANTALYA_TOKENS):
        tt = norm_text(t)
        if tt and tt in text:
            if tt == "antalya":
                continue
            score += 2
    return score


def looks_like_kiralama(text_blob: str) -> bool:
    t = norm_text(text_blob)
    if not t:
        return False

    # “kiraya verme” bazen istenen bir şey (taşınmaz kiralama).
    # ALLOW_KIRAYA_VERME False ise bunu negatif yap.
    if not ALLOW_KIRAYA_VERME and "kiraya verme" in t:
        return False

    pos = contains_any(t, POSITIVE_HINTS) or ("kirala" in t) or ("kira" in t)
    if not pos:
        return False

    neg = contains_any(t, NEGATIVE_HINTS)
    if neg:
        return False

    return True


def safe_get(d: Dict[str, Any], *keys: str) -> Any:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def extract_items(payload: Any) -> List[Dict[str, Any]]:
    """
    ilan.gov.tr endpointi farklı şemalar döndürebiliyor.
    O yüzden birkaç olası formatı destekliyoruz.
    """
    if payload is None:
        return []

    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]

    if not isinstance(payload, dict):
        return []

    # Olası alanlar:
    # { data: [...] } veya { content: [...] } veya { results: [...] } vs.
    for key in ("data", "content", "results", "items", "ilanlar"):
        v = payload.get(key)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]

    # Bazı API'lerde: { data: { content: [...] } }
    v = payload.get("data")
    if isinstance(v, dict):
        for key in ("content", "results", "items"):
            vv = v.get(key)
            if isinstance(vv, list):
                return [x for x in vv if isinstance(x, dict)]

    return []


def build_item_blob(item: Dict[str, Any]) -> str:
    """
    Antalya ve kiralama filtresi için mümkün olan tüm alanları tek blob'a topluyoruz.
    addressCityName güvenilmezse bile title/description/address vs yakalansın.
    """
    candidates = []

    # Yaygın alan adları
    for k in (
        "title", "baslik",
        "description", "aciklama", "summary", "ozet",
        "institutionName", "kurumAdi", "publisher", "yayimlayan",
        "categoryName", "kategoriAdi",
        "advertTypeName", "ilanTuru", "typeName",
        "addressCityName", "addressTownName", "addressDistrictName",
        "city", "town", "district",
        "address", "adres", "location",
        "text", "body", "detail", "details",
        "url", "link",
    ):
        v = item.get(k)
        if v:
            candidates.append(v)

    # Nested olası alanlar
    for path in (
        ("address", "cityName"),
        ("address", "townName"),
        ("address", "districtName"),
        ("address", "fullAddress"),
        ("location", "city"),
        ("location", "town"),
        ("location", "district"),
    ):
        v = safe_get(item, *path)
        if v:
            candidates.append(v)

    return " | ".join([str(x) for x in candidates if x is not None])


def item_id(item: Dict[str, Any]) -> str:
    """
    Dedupe için ilan kimliği: id / ilanNo / ilanNumber / sourceId vs.
    Bulamazsak url veya title hash benzeri string döndürür.
    """
    for k in ("id", "ilanId", "ilanNo", "ilanNumber", "advertId", "sourceId", "ilnNo", "ilan_numarasi"):
        v = item.get(k)
        if v:
            return str(v).strip()

    url = item.get("url") or item.get("link")
    if url:
        return str(url).strip()

    # son çare
    return f"fallback::{norm_text(item.get('title',''))[:80]}"


def item_url(item: Dict[str, Any]) -> str:
    u = item.get("url") or item.get("link") or ""
    u = str(u).strip()
    # Bazı API'ler path döndürür, domain eklemek gerekebilir
    if u and u.startswith("/"):
        u = "https://www.ilan.gov.tr" + u
    return u


def format_message(item: Dict[str, Any]) -> str:
    title = (item.get("title") or item.get("baslik") or "İlan").strip() if isinstance(item.get("title") or item.get("baslik") or "İlan", str) else "İlan"
    url = item_url(item)
    iln = None
    for k in ("ilanNo", "ilanNumber", "ilnNo", "ilan_numarasi"):
        if item.get(k):
            iln = str(item.get(k)).strip()
            break

    city = item.get("addressCityName") or safe_get(item, "address", "cityName") or item.get("city") or ""
    town = item.get("addressTownName") or safe_get(item, "address", "townName") or item.get("town") or ""
    dist = item.get("addressDistrictName") or safe_get(item, "address", "districtName") or item.get("district") or ""

    parts = []
    parts.append(f"📌 {title}")
    if iln:
        parts.append(f"🆔 {iln}")
    loc = " / ".join([p for p in [str(city).strip(), str(town).strip(), str(dist).strip()] if p and p.strip()])
    if loc:
        parts.append(f"📍 {loc}")
    if url:
        parts.append(f"🔗 {url}")

    return "\n".join(parts)


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
    if r.status_code != 200:
        raise RuntimeError(f"Telegram sendMessage failed: {r.status_code} {r.text}")


# -----------------------------
# Cache (seen)
# -----------------------------
def load_seen() -> Dict[str, Any]:
    os.makedirs(CACHE_DIR, exist_ok=True)
    if not os.path.exists(SEEN_FILE):
        return {"seen": {}}
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"seen": {}}


def save_seen(db: Dict[str, Any]) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def mark_seen(db: Dict[str, Any], iid: str) -> None:
    if "seen" not in db or not isinstance(db["seen"], dict):
        db["seen"] = {}
    # Value: seen time (debug/trace)
    db["seen"][iid] = now_tr()


# -----------------------------
# ilan.gov.tr fetch
# -----------------------------
def fetch_page(page: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Endpoint şeması değişebildiği için:
    - params ile query/page/size yolluyoruz
    - 200 değilse hata döndürür
    """
    params = {
        "q": SEARCH_QUERY,
        "page": page,
        "size": PAGE_SIZE,
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; antalya_ihale_bot/1.0; +https://github.com/)",
        "Accept": "application/json,text/plain,*/*",
    }
    r = requests.get(ILAN_API_URL, params=params, headers=headers, timeout=40)
    if r.status_code != 200:
        raise RuntimeError(f"ilan.gov.tr API failed: {r.status_code} {r.text[:500]}")
    payload = r.json()
    items = extract_items(payload)
    meta = {"raw_payload_keys": list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__}
    return items, meta


def main():
    if not BOT_TOKEN:
        die("BOT_TOKEN missing")
    if not CHAT_IDS_RAW:
        die("CHAT_IDS missing")

    chat_ids = [c.strip() for c in CHAT_IDS_RAW.split(",") if c.strip()]
    if not chat_ids:
        die("CHAT_IDS empty after parse")

    seen_db = load_seen()
    seen_map = seen_db.get("seen", {}) if isinstance(seen_db.get("seen"), dict) else {}

    total_raw = 0
    total_filtered = 0
    total_new = 0

    new_items: List[Dict[str, Any]] = []
    debug_lines: List[str] = []

    for page in range(0, MAX_PAGES):
        items, meta = fetch_page(page)
        total_raw += len(items)

        if DEBUG:
            debug_lines.append(f"page={page} raw={len(items)} meta={meta}")

        # Filtre: Antalya + kiralama + negatif eleme
        for it in items:
            blob = build_item_blob(it)

            # Antalya skoru
            ant_score = score_antalyaness(blob)
            if ant_score < 3:
                # en azından “antalya” yakalasın veya 2+ ilçe gibi bir kombinasyon
                # (ör. şehir alanı boş gelip title'da "Aksu" geçiyorsa 2 puan olur; o zaman kaçmasın diye)
                # Kural: antalya yoksa 2 ilçe (4 puan) gerekir.
                if "antalya" not in norm_text(blob) and ant_score < 4:
                    continue

            # Kiralama/ihale kontrolü + negatif eleme
            if not looks_like_kiralama(blob):
                continue

            total_filtered += 1

            iid = item_id(it)
            if iid in seen_map:
                continue

            new_items.append(it)
            mark_seen(seen_db, iid)
            total_new += 1

        # Yeterli yeni bulduysak (spam limiti) daha fazla sayfa tarama
        if len(new_items) >= MAX_SEND_PER_RUN:
            break

        # Çok hızlı istek yapmayalım
        time.sleep(0.4)

    # Seen kaydet
    save_seen(seen_db)

    # Mesaj gönderimi
    if new_items:
        # Tek tek gönder (okunabilirlik + Telegram limitlerine takılmama)
        for it in new_items[:MAX_SEND_PER_RUN]:
            msg = format_message(it)
            for cid in chat_ids:
                tg_send(cid, msg)
            time.sleep(0.8)

    # DEBUG özet (tek mesaj)
    if DEBUG:
        summary = [
            "🧪 DEBUG (antalya_ihale_bot)",
            f"time={now_tr()}",
            f"query={SEARCH_QUERY}",
            f"raw_total={total_raw}",
            f"filtre_sonrasi={total_filtered}",
            f"yeni={total_new}",
            f"sent={min(len(new_items), MAX_SEND_PER_RUN)}",
            "—",
        ]
        # Debug satırlarını çok uzatmayalım
        tail = debug_lines[-8:] if len(debug_lines) > 8 else debug_lines
        summary.extend([str(x)[:350] for x in tail])
        text = "\n".join(summary)

        for cid in chat_ids:
            tg_send(cid, text)

    # Workflow log
    print(json.dumps({
        "time": now_tr(),
        "query": SEARCH_QUERY,
        "raw_total": total_raw,
        "filtered": total_filtered,
        "new": total_new,
        "sent": min(len(new_items), MAX_SEND_PER_RUN),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
