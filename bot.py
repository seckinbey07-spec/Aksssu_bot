import os
import json
import time
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# EKAP bulletin PDF parsing
from pypdf import PdfReader


AKSU_URL = os.getenv("AKSU_URL", "https://www.aksu.bel.tr/ihaleler")
EKAP_BULTEN_URL = os.getenv("EKAP_BULTEN_URL", "https://ekap.kik.gov.tr/ekap/ilan/bultenindirme.aspx")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

STATE_PATH = "state.json"


def send_telegram(text: str) -> None:
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
    return requests.get(
        url,
        timeout=45,
        headers={"User-Agent": "Mozilla/5.0"},
    )


# ----------------------------
# 1) AKSU Belediyesi: yeni ihale linklerini yakala
# ----------------------------
def aksu_fetch_items(limit: int = 50) -> list[dict]:
    """
    Returns list of {"title": str, "url": str}
    """
    r = http_get(AKSU_URL)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    items = []
    seen = set()

    # Sayfada "ihale" içeren başlık/linkler
    for a in soup.select("a"):
        title = " ".join(a.get_text(" ", strip=True).split())
        href = (a.get("href") or "").strip()
        if not title or len(title) < 8 or not href:
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


def aksu_check_new(state: dict) -> tuple[list[dict], dict]:
    seen_urls = set(state.get("aksu_seen_urls", []))

    items = aksu_fetch_items(limit=60)
    new_items = [it for it in items if it["url"] not in seen_urls]

    # state güncelle (en fazla 300 adet sakla)
    for it in items:
        seen_urls.add(it["url"])
    state["aksu_seen_urls"] = list(seen_urls)[:300]

    return new_items, state


# ----------------------------
# 2) EKAP: bülten PDF bul → indir → metin çıkar → Antalya + Aksu filtrele
# ----------------------------
def ekap_find_latest_pdf_url() -> str | None:
    r = http_get(EKAP_BULTEN_URL)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # Sayfadaki PDF linklerini ara
    pdf_links = []
    for a in soup.select("a"):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        if ".pdf" in href.lower():
            pdf_links.append(urljoin(EKAP_BULTEN_URL, href))

    if not pdf_links:
        return None

    # Genelde en güncel ilklerde olur; yine de ilkini alıyoruz
    return pdf_links[0]


def ekap_pdf_text(pdf_bytes: bytes) -> str:
    # pypdf bytes'tan okuyalım
    import io
    reader = PdfReader(io.BytesIO(pdf_bytes))
    texts = []
    for page in reader.pages:
        t = page.extract_text() or ""
        if t:
            texts.append(t)
    return "\n".join(texts)


def ekap_extract_relevant_lines(text: str) -> list[str]:
    """
    Basit yaklaşım: satır satır gez, ANTALYA ve AKSU birlikte geçen satırları al.
    (Bülten formatı değişken olduğu için 'line' bazlı yakalama en güvenlisi.)
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    out = []
    for ln in lines:
        up = ln.upper()
        if "ANTALYA" in up and "AKSU" in up:
            # çok uzun satırları kırp
            if len(ln) > 220:
                ln = ln[:220] + "…"
            out.append(ln)
    return out


def ekap_check_new(state: dict) -> tuple[list[str], dict]:
    latest_pdf_url = ekap_find_latest_pdf_url()
    if not latest_pdf_url:
        return [], state

    # PDF değişti mi?
    last_pdf_url = state.get("ekap_last_pdf_url")
    seen_lines = set(state.get("ekap_seen_lines", []))

    # PDF indir
    r = http_get(latest_pdf_url)
    r.raise_for_status()
    text = ekap_pdf_text(r.content)

    relevant = ekap_extract_relevant_lines(text)

    # yeni satırlar
    new_lines = [ln for ln in relevant if ln not in seen_lines]

    # state güncelle
    for ln in relevant:
        seen_lines.add(ln)
    state["ekap_seen_lines"] = list(seen_lines)[:2000]
    state["ekap_last_pdf_url"] = latest_pdf_url

    # PDF yeni olmasa bile (aynı PDF), satırlar içinde yeni bir şey çıkmaz zaten.
    # PDF tamamen yenilendiğinde de satırlar yeniden değerlendirilecek.

    return new_lines, state


def main() -> None:
    if not BOT_TOKEN or not CHAT_ID:
        raise SystemExit("BOT_TOKEN veya CHAT_ID eksik. GitHub Secrets ayarlarını kontrol edin.")

    state = load_state()

    # AKSU Belediyesi
    aksu_new, state = aksu_check_new(state)

    # EKAP (Antalya + Aksu)
    ekap_new, state = ekap_check_new(state)

    # Bildirim gönder
    if aksu_new:
        for it in aksu_new:
            send_telegram(f"🆕 Aksu Belediyesi ihale:\n{it['title']}\n{it['url']}")
            time.sleep(1)  # Telegram rate limit için küçük bekleme

    if ekap_new:
        # çok satır çıkarsa tek mesajda gruplayalım
        chunk = ekap_new[:20]
        msg = "🆕 EKAP Bülteninde ANTALYA + AKSU geçen yeni kayıt(lar):\n\n" + "\n".join(f"- {ln}" for ln in chunk)
        send_telegram(msg)

    save_state(state)


if __name__ == "__main__":
    main()
