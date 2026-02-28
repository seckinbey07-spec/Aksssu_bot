import os
import json
import hashlib
import requests
from bs4 import BeautifulSoup

CHECK_URL = os.getenv("CHECK_URL", "https://www.aksu.bel.tr/ihaleler")
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
STATE_PATH = "state.json"


def send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=20)
    r.raise_for_status()


def load_state() -> dict:
    if not os.path.exists(STATE_PATH):
        return {}
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fetch_top_items() -> list[tuple[str, str]]:
    r = requests.get(CHECK_URL, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    items: list[tuple[str, str]] = []
    seen = set()

    # Öncelik: içinde "ihale" geçen link başlıkları
    for a in soup.select("a"):
        title = " ".join(a.get_text(" ", strip=True).split())
        href = a.get("href") or ""
        if not title or len(title) < 10:
            continue

        key = (title, href)
        if key in seen:
            continue
        seen.add(key)

        if "ihale" in title.lower():
            items.append((title, href))

    # Yedek: hiç yakalayamazsa ilk 10 anlamlı linki al
    if not items:
        for a in soup.select("a")[:80]:
            title = " ".join(a.get_text(" ", strip=True).split())
            href = a.get("href") or ""
            if title and len(title) >= 12:
                items.append((title, href))
            if len(items) >= 10:
                break

    return items[:10]


def main() -> None:
    if not BOT_TOKEN or not CHAT_ID:
        raise SystemExit("BOT_TOKEN veya CHAT_ID eksik. GitHub Secrets ayarlarını kontrol edin.")

    state = load_state()
    last_hash = state.get("last_hash")

    items = fetch_top_items()
    snapshot = "\n".join([f"- {t}" for t, _ in items])
    current_hash = sha(snapshot)

    # İlk çalıştırma: state kaydet + test mesajı gönder (1 kere)
    if not last_hash:
        save_state({"last_hash": current_hash})
        return

    # Değişiklik varsa bildir
    if current_hash != last_hash:
        msg = (
            "🆕 Aksu ihaleler sayfasında güncelleme tespit edildi.\n"
            f"{CHECK_URL}\n\n"
            "Son görünen başlıklar:\n"
            f"{snapshot}"
        )
        send_telegram(msg)
        save_state({"last_hash": current_hash})


if __name__ == "__main__":
    main()
