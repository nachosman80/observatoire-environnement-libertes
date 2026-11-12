import os, re, json, html, hashlib
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from email.utils import format_datetime
from calendar import timegm
import feedparser
import requests

ROOT = os.path.dirname(__file__)
OUT = os.path.join(ROOT, "docs")
os.makedirs(OUT, exist_ok=True)

SITE_URL = "https://nachosman80.github.io/observatoire-environnement-libertes/"
MONTREAL_TZ = ZoneInfo("America/Toronto")
HEADERS = {
    "User-Agent": "ObservatoireCitoyen/1.2 (+https://github.com/nachosman80/observatoire-environnement-libertes)"
}

KEYWORDS = {
    "environment": [
        "environnement", "climat", "climatique", "émission", "carbone", "pollution",
        "biodiversité", "écosystème", "eau", "forêt", "énergie", "transition",
        "justice environnementale", "justice climatique", "évaluation environnementale",
        "permis", "mine", "pipeline", "oléoduc", "changements climatiques", "bape",
        "pacte vert", "aires protégées", "gaz à effet de serre",
        "environmental", "climate", "pollution", "biodiversity", "ecosystem",
        "environmental justice", "climate litigation", "climate law", "emissions",
        "carbon", "epa", "clean air", "clean water", "conservation", "fossil fuel"
    ],
    "civil_liberties": [
        "vie privée", "liberté", "libertés civiles", "surveillance", "police",
        "reconnaissance faciale", "données personnelles", "protection des données",
        "caméra", "flock", "profilage", "droit fondamental", "droits fondamentaux",
        "charte", "manifestation", "liberté d'expression", "rgpd", "cedh",
        "biométrique", "censure", "état d'urgence",
        "privacy", "civil liberties", "facial recognition", "police surveillance",
        "data protection", "data privacy", "human rights", "flock cameras",
        "surveillance", "fourth amendment", "first amendment", "aclu", "gdpr",
        "biometric", "digital rights", "free speech", "protest rights"
    ]
}

def clean(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(s or ""))).strip()

def extract_image(entry):
    for key in ("media_content", "media_thumbnail"):
        media = entry.get(key)
        if isinstance(media, list):
            for m in media:
                url = m.get("url", "")
                if url.startswith("http"):
                    return url
    for enc in entry.get("enclosures", []):
        if str(enc.get("type", "")).startswith("image/"):
            url = enc.get("href") or enc.get("url", "")
            if url.startswith("http"):
                return url
    raw_html = ""
    if "content" in entry and isinstance(entry["content"], list):
        raw_html += " ".join(c.get("value", "") for c in entry["content"])
    raw_html += " " + str(entry.get("summary") or entry.get("description") or "")
    match = re.search(r'<img[^>]+src=["\'](https?://[^"\']+)["\']', raw_html, re.IGNORECASE)
    if match:
        img_url = match.group(1)
        if not any(x in img_url.lower() for x in ("pixel", "tracker", "1x1", "beacon")):
            return img_url
    return None

def format_analysis_html(text):
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    return escaped.replace("\n", "<br>")

def score(text, words):
    t = text.casefold()
    return sum(2 if len(w) > 12 and w in t else 1 for w in words if w in t)

def ai_summary(item):
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return None
    model = os.getenv("OPENAI_MODEL") or "gpt-4o-mini"
    prompt = f"""Tu es documentaliste juridique francophone. À partir uniquement des informations ci-dessous
(même si elles sont en anglais, rédige entièrement en français), produis :
1) **Résumé disponible :** un résumé factuel de 3 à 4 phrases;
2) **Enjeu juridique et politique :** explique prudemment les questions de droit public, droits fondamentaux,
responsabilité, réglementation ou politiques publiques que le sujet peut soulever;
3) **À surveiller :** une question concrète ou une suite possible.
Ne prétends pas avoir lu l'article complet si seul l'extrait est fourni. N'invente ni loi, ni jugement, ni citation.
Signale les limites de l'information. Ce n'est pas un avis juridique.

Titre : {item['title']}
Région : {item['region']}
Source : {item['source']}
Date : {item['date']}
Extrait : {item['summary']}
Lien : {item['link']}"""
    try:
        r = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "input": prompt},
            timeout=60
        )
        r.raise_for_status()
        data = r.json()
        chunks = []
        for out in data.get("output", []):
            for c in out.get("content", []):
                if c.get("type") == "output_text":
                    chunks.append(c.get("text", ""))
        return "\n\n".join(chunks).strip() or None
    except Exception as e:
        print("IA indisponible pour un article:", e)
        return None

def fallback(item):
    excerpt = item["summary"] or "Aucun résumé fourni par le flux source."
    return (f"**Résumé disponible :** {excerpt}\n\n"
            "**Enjeu juridique et politique :** cet article a été retenu en raison de ses liens "
            "avec le thème. Il faudra consulter la source originale pour identifier précisément "
            "les règles applicables, les acteurs concernés et les conséquences.\n\n"
            "**À surveiller :** vérifier les textes officiels, décisions ou documents cités "
            "dans l'article.")

def main():
    feeds = json.load(open(os.path.join(ROOT, "feeds.json"), encoding="utf-8"))
    now = datetime.now(timezone.utc)
    now_mtl = now.astimezone(MONTREAL_TZ)
    items, failures = [], []
    seen = set()

    for f in feeds:
        try:
            resp = requests.get(f["url"], headers=HEADERS, timeout=20)
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
            if parsed.bozo and not parsed.entries:
                failures.append(f["name"])
                continue
            for e in parsed.entries[:60]:
                link = e.get("link", "").strip()
                title = clean(e.get("title"))
                if not link or not title:
                    continue
                uid = hashlib.sha256(link.encode()).hexdigest()
                if uid in seen:
                    continue
                seen.add(uid)
                raw_date = e.get("published_parsed") or e.get("updated_parsed")
                if raw_date:
                    published = datetime.fromtimestamp(timegm(raw_date), timezone.utc)
                else:
                    published = now
                if now - published > timedelta(days=5):
                    continue
                summary = clean(e.get("summary") or e.get("description") or "")
                text = f"{title} {summary}"
                env = score(text, KEYWORDS["environment"])
                civ = score(text, KEYWORDS["civil_liberties"])
                declared = f.get("theme", "both")
                if declared == "environment":
                    env += 2
                elif declared == "civil_liberties":
                    civ += 2
                if max(env, civ) < 1:
                    continue
                theme = "environment" if env >= civ else "civil_liberties"
                pub_mtl = published.astimezone(MONTREAL_TZ)
