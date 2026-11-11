import os, re, json, html, hashlib
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from urllib.parse import urlparse
import feedparser
import requests

ROOT = os.path.dirname(__file__)
OUT = os.path.join(ROOT, "docs")
os.makedirs(OUT, exist_ok=True)

KEYWORDS = {
    "environment": [
        "environnement", "climat", "climatique", "émission", "carbone", "pollution",
        "biodiversité", "écosystème", "eau", "forêt", "énergie", "transition",
        "justice environnementale", "évaluation environnementale", "permis", "mine",
        "pipeline", "changements climatiques", "environmental", "climate", "pollution",
        "biodiversity", "environmental justice", "climate litigation"
    ],
    "civil_liberties": [
        "vie privée", "liberté", "libertés civiles", "surveillance", "police",
        "reconnaissance faciale", "données personnelles", "protection des données",
        "caméra", "flock", "profilage", "droit fondamental", "charte", "manifestation",
        "liberté d'expression", "privacy", "civil liberties", "facial recognition",
        "police surveillance", "data protection", "human rights"
    ]
}

def clean(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(s or ""))).strip()

def score(text, words):
    t = text.casefold()
    return sum(2 if len(w) > 12 and w in t else 1 for w in words if w in t)

def ai_summary(item):
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return None
    model = os.getenv("OPENAI_MODEL") or "gpt-6-luna"
    prompt = f"""Tu es documentaliste juridique francophone. À partir uniquement des informations ci-dessous,
rédige en français :
1) un résumé factuel de 3 à 5 phrases;
2) « Enjeu juridique et politique » : explique prudemment les questions de droit public, droits fondamentaux,
responsabilité, réglementation ou politiques publiques que le sujet peut soulever;
3) « À surveiller » : une question concrète ou une suite possible.
Ne prétends pas avoir lu l'article complet si seul l'extrait est fourni. N'invente ni loi, ni jugement, ni citation.
Signale les limites de l'information. Ce n'est pas un avis juridique.

Titre : {item['title']}
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
    items, failures = [], []
    seen = set()
    for f in feeds:
        try:
            parsed = feedparser.parse(f["url"])
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
                    from calendar import timegm
                    published = datetime.fromtimestamp(timegm(raw_date), timezone.utc)
                else:
                    published = now
                # Garder une fenêtre de 10 jours pour absorber les retards des flux.
                if now - published > timedelta(days=10):
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
                items.append({
                    "id": uid, "title": title, "link": link, "summary": summary[:1400],
                    "source": f["name"], "region": f.get("region", ""),
                    "date": published.strftime("%Y-%m-%d %H:%M UTC"),
                    "published": published, "theme": theme, "score": max(env, civ)
                })
        except Exception as ex:
            print("Flux inaccessible:", f["name"], ex)
            failures.append(f["name"])

    # Les sources officielles et journalistiques sont combinées; équilibre entre thèmes.
    items.sort(key=lambda x: (x["score"], x["published"]), reverse=True)
    env_items = [x for x in items if x["theme"] == "environment"]
    civ_items = [x for x in items if x["theme"] == "civil_liberties"]
    selected = []
    for i in range(8):
        if i < len(env_items): selected.append(env_items[i])
        if i < len(civ_items): selected.append(civ_items[i])
    selected = selected[:15]
    for item in selected:
        item["analysis"] = ai_summary(item) or fallback(item)

    stamp = now.strftime("%Y-%m-%d %H:%M UTC")
    def article_html(x):
        theme = "Environnement" if x["theme"] == "environment" else "Libertés civiles"
        return f"""<article class="story">
        <div class="meta">{html.escape(theme)} · {html.escape(x['region'])} · {html.escape(x['date'])}</div>
        <h2><a href="{html.escape(x['link'], quote=True)}">{html.escape(x['title'])}</a></h2>
        <p class="source">Source du flux : {html.escape(x['source'])}</p>
        <div class="analysis">{html.escape(x['analysis']).replace(chr(10), '<br>')}</div>
        <p><a href="{html.escape(x['link'], quote=True)}">Lire la source originale ↗</a></p>
        </article>"""

    cards = "\n".join(article_html(x) for x in selected)
    warning = ("<p class='warning'>Flux temporairement indisponibles : " +
               ", ".join(html.escape(x) for x in failures) + "</p>") if failures else ""
    page = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Observatoire — environnement et libertés civiles</title>
    <style>
    body{{font:16px/1.6 system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;color:#20252b;background:#f7f8fa}}
    header{{padding:1.5rem 0;border-bottom:1px solid #ccd2d8}}h1{{line-height:1.2}}
    .intro,.story{{background:white;border:1px solid #dce1e6;border-radius:12px;padding:1.2rem;margin:1rem 0}}
    .meta,.source{{font-size:.85rem;color:#586574}}h2{{font-size:1.2rem;line-height:1.35}}
    a{{color:#1456a0}}.analysis{{margin-top:1rem}}.warning{{background:#fff3cd;padding:1rem;border-radius:8px}}
    footer{{font-size:.85rem;color:#586574;margin:2rem 0}}
    </style></head><body><header><h1>Observatoire citoyen</h1>
    <p>Environnement · Libertés civiles · Droit public</p></header>
    <section class="intro"><strong>Bulletin du {now.astimezone().strftime('%d-%m-%Y')}</strong>
    <p>Jusqu'à 15 nouvelles sélectionnées, avec une répartition équilibrée entre les deux thèmes.
    Les analyses sont générées automatiquement et doivent être vérifiées à la source.</p>
    <p><a href="feed.xml">S'abonner au flux RSS</a></p></section>
    {warning}{cards or '<p>Aucun article pertinent trouvé pour cette exécution.</p>'}
    <footer>Généré automatiquement le {html.escape(stamp)}. Outil de veille documentaire, pas un avis juridique.
    Vérifie les sources originales avant toute utilisation ou citation.</footer></body></html>"""
    open(os.path.join(OUT, "index.html"), "w", encoding="utf-8").write(page)

    rss_items = []
    for x in selected:
        desc = html.escape(x["analysis"])
        pub = format_datetime(x["published"])
        rss_items.append(f"""<item><title>{html.escape(x['title'])}</title>
        <link>{html.escape(x['link'])}</link><guid isPermaLink="false">{x['id']}</guid>
        <pubDate>{pub}</pubDate><description>{desc}</description>
        <category>{html.escape(x['theme'])}</category></item>""")
    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><title>Observatoire — environnement et libertés civiles</title>
    <link>./</link><description>Veille deux fois par semaine sur l'environnement et les libertés civiles.</description>
    <language>fr-ca</language><lastBuildDate>{format_datetime(now)}</lastBuildDate>
    {''.join(rss_items)}</channel></rss>"""
    open(os.path.join(OUT, "feed.xml"), "w", encoding="utf-8").write(rss)
    print(f"{len(selected)} articles publiés; {len(failures)} flux en échec.")

if __name__ == "__main__":
    main()
