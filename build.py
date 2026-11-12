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
    # 1. Balises media:content ou media:thumbnail
    for key in ("media_content", "media_thumbnail"):
        media = entry.get(key)
        if isinstance(media, list):
            for m in media:
                url = m.get("url", "")
                if url.startswith("http"):
                    return url
    # 2. Balises enclosures (images jointes)
    for enc in entry.get("enclosures", []):
        if str(enc.get("type", "")).startswith("image/"):
            url = enc.get("href") or enc.get("url", "")
            if url.startswith("http"):
                return url
    # 3. Balise <img> dans le contenu ou le résumé brut
    raw_html = ""
    if "content" in entry and isinstance(entry["content"], list):
        raw_html += " ".join(c.get("value", "") for c in entry["content"])
    raw_html += " " + str(entry.get("summary") or entry.get("description") or "")
    match = re.search(r'<img[^>]+src=["\'](https?://[^"\']+)["\']', raw_html, re.IGNORECASE)
    if match:
        img_url = match.group(1)
        # Ignorer les pixels de pistage de 1x1
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
                items.append({
                    "id": uid,
                    "title": title,
                    "link": link,
                    "image": extract_image(e),
                    "summary": summary[:1400],
                    "source": f["name"],
                    "region": f.get("region", ""),
                    "date": pub_mtl.strftime("%d/%m/%Y · %H:%M"),
                    "published": published,
                    "theme": theme,
                    "score": max(env, civ)
                })
        except Exception as ex:
            print("Flux inaccessible:", f["name"], ex)
            failures.append(f["name"])

    items.sort(key=lambda x: (x["score"], x["published"]), reverse=True)
    env_items = [x for x in items if x["theme"] == "environment"]
    civ_items = [x for x in items if x["theme"] == "civil_liberties"]
    selected = []
    for i in range(8):
        if i < len(env_items):
            selected.append(env_items[i])
        if i < len(civ_items):
            selected.append(civ_items[i])

    for item in selected:
        item["analysis"] = ai_summary(item) or fallback(item)

    stamp = now_mtl.strftime("%d-%m-%Y à %H:%M (heure de Montréal)")

    def article_html(x):
        is_env = x["theme"] == "environment"
        theme_label = "Environnement" if is_env else "Libertés civiles"
        badge_class = "badge-env" if is_env else "badge-civ"
        placeholder_class = "placeholder-env" if is_env else "placeholder-civ"
        icon = "🌿" if is_env else "⚖️"

        if x["image"]:
            media_block = f"""<div class="card-media">
                <img src="{html.escape(x['image'], quote=True)}" alt="" loading="lazy"
                     onerror="this.parentElement.classList.add('{placeholder_class}'); this.outerHTML='<span class=\\'placeholder-icon\\'>{icon}</span><span class=\\'placeholder-label\\'>{html.escape(theme_label)}</span>';">
            </div>"""
        else:
            media_block = f"""<div class="card-media {placeholder_class}">
                <span class="placeholder-icon">{icon}</span>
                <span class="placeholder-label">{html.escape(theme_label)}</span>
            </div>"""

        return f"""<article class="story" data-theme="{html.escape(x['theme'])}" data-region="{html.escape(x['region'])}">
            <a href="{html.escape(x['link'], quote=True)}" target="_blank" rel="noopener" class="media-link" aria-hidden="true" tabindex="-1">
                {media_block}
            </a>
            <div class="card-body">
                <div class="meta">
                    <span class="badge {badge_class}">{html.escape(theme_label)}</span>
                    <span class="region-tag">📍 {html.escape(x['region'])}</span>
                    <span class="date-tag">{html.escape(x['date'])}</span>
                </div>
                <h2><a href="{html.escape(x['link'], quote=True)}" target="_blank" rel="noopener">{html.escape(x['title'])}</a></h2>
                <p class="source">{html.escape(x['source'])}</p>
                <div class="analysis">{format_analysis_html(x['analysis'])}</div>
                <div class="card-footer">
                    <a class="read-more" href="{html.escape(x['link'], quote=True)}" target="_blank" rel="noopener">Lire l'article original <span>↗</span></a>
                </div>
            </div>
        </article>"""

    cards = "\n".join(article_html(x) for x in selected)
    warning = ("<div class='warning'>⚠️ Flux temporairement indisponibles : " +
               ", ".join(html.escape(x) for x in failures) + "</div>") if failures else ""

    page = f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Observatoire — Environnement & Libertés civiles</title>
<link rel="alternate" type="application/rss+xml" title="Flux RSS" href="{SITE_URL}feed.xml">
<script>
  (function() {{
    const saved = localStorage.getItem('theme');
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    if (saved === 'dark' || (!saved && prefersDark)) {{
      document.documentElement.setAttribute('data-theme', 'dark');
    }}
  }})();
</script>
<style>
  :root {{
    --bg: #f4f6f9;
    --surface: #ffffff;
    --surface-hover: #fcfdfe;
    --text: #18202c;
    --text-muted: #5a6678;
    --border: #e1e6ed;
    --accent: #1456a0;
    --accent-hover: #0e3f77;
    --shadow: 0 4px 16px rgba(15, 23, 42, 0.05);
    --shadow-hover: 0 10px 26px rgba(15, 23, 42, 0.1);
    --env-bg: #e6f4ea;
    --env-text: #137333;
    --civ-bg: #e8f0fe;
    --civ-text: #1967d2;
    --warn-bg: #fff8e1;
    --warn-border: #ffe082;
    --warn-text: #7a5901;
  }}

  [data-theme="dark"] {{
    --bg: #0d1117;
    --surface: #161b22;
    --surface-hover: #1c222b;
    --text: #e6edf3;
    --text-muted: #8b949e;
    --border: #2b323b;
    --accent: #58a6ff;
    --accent-hover: #79c0ff;
    --shadow: 0 4px 16px rgba(0, 0, 0, 0.35);
    --shadow-hover: 0 10px 28px rgba(0, 0, 0, 0.55);
    --env-bg: rgba(46, 160, 67, 0.18);
    --env-text: #56d364;
    --civ-bg: rgba(56, 139, 253, 0.18);
    --civ-text: #79c0ff;
    --warn-bg: rgba(187, 128, 9, 0.15);
    --warn-border: rgba(187, 128, 9, 0.4);
    --warn-text: #e3b341;
  }}

  * {{ box-sizing: border-box; }}

  body {{
    font: 15.5px/1.65 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    margin: 0;
    padding: 0;
    color: var(--text);
    background: var(--bg);
    transition: background-color 0.25s ease, color 0.25s ease;
  }}

  .container {{
    max-width: 1120px;
    margin: 0 auto;
    padding: 1.5rem 1.25rem 3rem;
  }}

  header {{
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 1rem;
    padding: 1rem 0 1.5rem;
    border-bottom: 1px solid var(--border);
  }}

  .brand h1 {{
    font-size: 1.85rem;
    font-weight: 800;
    letter-spacing: -0.02em;
    margin: 0 0 0.25rem;
    line-height: 1.2;
