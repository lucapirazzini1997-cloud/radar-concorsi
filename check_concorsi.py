"""Controllo giornaliero di Radar Concorsi (eseguito da GitHub Actions).

1. Scarica i nuovi bandi di radiodiagnostica dal portale inPA -> collezione `feed`.
2. Per ogni concorso seguito dagli utenti controlla se la pagina è cambiata;
   se sì, riassume la novità con GitHub Models.
3. Invia a ogni utente (con email giornaliera attiva) un riepilogo, solo se ci sono novità.

Segreti richiesti: FIREBASE_SERVICE_ACCOUNT, GMAIL_USER, GMAIL_APP_PASSWORD.
GITHUB_TOKEN è fornito automaticamente da Actions.
"""
import datetime as dt
import difflib
import hashlib
import html
import json
import os
import re
import smtplib
from email.mime.text import MIMEText

import firebase_admin
import requests
from bs4 import BeautifulSoup
from firebase_admin import credentials, firestore

KEYWORDS = ["radiodiagnostica", "radiologia"]
INPA_API = "https://portale.inpa.gov.it/concorsi-smart/api/concorso-public-area/search-better"
INPA_DETAIL = "https://www.inpa.gov.it/bandi-e-avvisi/dettaglio-bando-avviso/?concorso_id={}"
SITE_URL = os.environ.get("SITE_URL", "")
UA = {"User-Agent": "Mozilla/5.0 (RadarConcorsi; +https://github.com)"}
MAX_TEXT = 60_000
now = dt.datetime.now(dt.timezone.utc)


def init_db():
    cred = credentials.Certificate(json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"]))
    firebase_admin.initialize_app(cred)
    return firestore.client()


# ---------- 1. Nuovi bandi da inPA ----------
def fetch_inpa():
    found = {}
    for kw in KEYWORDS:
        try:
            r = requests.post(f"{INPA_API}?page=0&size=50", json={"text": kw, "status": ["OPEN"]},
                              headers=UA, timeout=30)
            r.raise_for_status()
            for c in r.json().get("content", []):
                found[c["id"]] = {
                    "titolo": (c.get("titolo") or "").strip(),
                    "ente": ", ".join(c.get("entiRiferimento") or []),
                    "sedi": c.get("sedi") or [],
                    "numPosti": c.get("numPosti"),
                    "dataPubblicazione": c.get("dataPubblicazione") or "",
                    "dataScadenza": c.get("dataScadenza") or "",
                    "url": INPA_DETAIL.format(c["id"]),
                }
        except Exception as e:  # la fonte può essere temporaneamente giù
            print(f"inPA '{kw}': {e}")
    return found


def update_feed(db):
    new = []
    for cid, data in fetch_inpa().items():
        ref = db.collection("feed").document(cid)
        if not ref.get().exists:
            data["firstSeen"] = now
            new.append(data)
        ref.set(data, merge=True)
    print(f"Feed: {len(new)} nuovi bandi")
    return new


# ---------- 2. Controllo pagine seguite ----------
def page_text(url):
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    ctype = r.headers.get("content-type", "")
    if "pdf" in ctype:
        return f"[PDF] {hashlib.sha256(r.content).hexdigest()}"
    soup = BeautifulSoup(r.text, "html.parser")
    for t in soup(["script", "style", "noscript", "header", "footer", "nav", "form"]):
        t.decompose()
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in soup.get_text("\n").splitlines()]
    # niente righe con orari/contatori che cambiano a ogni visita
    lines = [ln for ln in lines if ln and not re.fullmatch(r"[\d:/.\s-]+", ln)]
    return "\n".join(lines)[:MAX_TEXT]


def summarize(title, added, removed):
    token = os.environ.get("GITHUB_TOKEN")
    fallback = "Novità sulla pagina:\n" + "\n".join("• " + a for a in added[:8])
    if not token:
        return fallback
    prompt = (
        f"Sei un assistente per un medico radiologo che segue il concorso: «{title}».\n"
        "La pagina del concorso è cambiata. Righe aggiunte:\n" + "\n".join(added[:80]) +
        "\n\nRighe rimosse:\n" + "\n".join(removed[:30]) +
        "\n\nIn italiano, massimo 3 frasi: spiega cosa è cambiato di rilevante (graduatoria, date e sedi "
        "delle prove, ammessi/esclusi, rettifiche, proroghe). Se il cambiamento è irrilevante "
        "(menu, date di aggiornamento, cookie) rispondi solo IRRILEVANTE."
    )
    try:
        r = requests.post(
            "https://models.github.ai/inference/chat/completions",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"model": "openai/gpt-4o-mini", "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.2, "max_tokens": 250},
            timeout=60,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"GitHub Models: {e}")
        return fallback


def check_follows(db):
    cache, report = {}, {}
    for user in db.collection("users").stream():
        u = user.to_dict()
        changes = []
        for f in user.reference.collection("follows").stream():
            d, url = f.to_dict(), f.to_dict().get("url")
            if not url:
                continue
            if url not in cache:
                try:
                    cache[url] = (page_text(url), None)
                except Exception as e:
                    cache[url] = (None, str(e)[:120])
            text, err = cache[url]
            if err:
                f.reference.update({"lastCheck": now, "lastError": "pagina non raggiungibile"})
                continue
            h = hashlib.sha256(text.encode()).hexdigest()
            upd = {"lastCheck": now, "lastError": firestore.DELETE_FIELD, "lastHash": h, "lastText": text}
            old = d.get("lastText")
            if d.get("lastHash") and d["lastHash"] != h and old is not None:
                diff = list(difflib.ndiff(old.splitlines(), text.splitlines()))
                added = [x[2:] for x in diff if x.startswith("+ ")]
                removed = [x[2:] for x in diff if x.startswith("- ")]
                if added or removed:
                    s = summarize(d.get("title", ""), added, removed)
                    if "IRRILEVANTE" not in s.upper():
                        upd.update({"lastChange": now, "lastSummary": s})
                        changes.append({**d, "summary": s})
            f.reference.update(upd)
        report[user.id] = (u, changes)
    return report


# ---------- 3. Email ----------
def send_emails(report, new_feed):
    gu, gp = os.environ.get("GMAIL_USER"), os.environ.get("GMAIL_APP_PASSWORD")
    if not (gu and gp):
        print("Email non configurata: salto l'invio")
        return
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(gu, gp)
        for uid, (u, changes) in report.items():
            if not u.get("emailDaily", True) or not u.get("email") or not (changes or new_feed):
                continue
            parts = [f"<h2>Radar Concorsi · {now.astimezone().strftime('%d/%m/%Y')}</h2>"]
            if changes:
                parts.append("<h3>Novità sui tuoi concorsi</h3>")
                for c in changes:
                    parts.append(f"<p><b>{html.escape(c.get('title',''))}</b> — {html.escape(c.get('ente',''))}<br>"
                                 f"{html.escape(c['summary']).replace(chr(10), '<br>')}<br>"
                                 f"<a href=\"{html.escape(c['url'])}\">Apri la pagina</a></p>")
            if new_feed:
                parts.append("<h3>Nuovi bandi di radiodiagnostica</h3><ul>")
                for b in new_feed:
                    parts.append(f"<li><a href=\"{html.escape(b['url'])}\">{html.escape(b['titolo'])}</a> — "
                                 f"{html.escape(b['ente'])} (scadenza {b['dataScadenza'][:10]})</li>")
                parts.append("</ul>")
            if SITE_URL:
                parts.append(f"<p><a href=\"{SITE_URL}\">Gestisci i tuoi concorsi</a></p>")
            msg = MIMEText("".join(parts), "html", "utf-8")
            msg["Subject"] = f"Radar Concorsi: {len(changes)} novità, {len(new_feed)} nuovi bandi"
            msg["From"] = f"Radar Concorsi <{gu}>"
            msg["To"] = u["email"]
            s.send_message(msg)
            print(f"Email inviata a utente {uid}")


if __name__ == "__main__":
    db = init_db()
    new_feed = update_feed(db)
    report = check_follows(db)
    send_emails(report, new_feed)
