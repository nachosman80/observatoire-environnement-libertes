import os, re, json, html, hashlib, time
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
    "User-Agent": "ObservatoireCitoyen/1.7 (+https://github.com/nachosman80/observatoire-environnement-libertes)"
}

KEYWORDS = {
    "environment": [
        "environnement", "climat", "climatique", "émission", "carbone", "pollution",
        "biodiversité", "écosystème", "eau", "forêt", "énergie", "transition",
        "justice environnementale", "justice climatique", "évaluation environnementale",
        "permis", "mine", "pipeline", "oléoduc", "changements climatiques", "bape",
        "pacte vert", "aires protégées", "gaz à effet de serre", "fossile", "nature",
        "cqde", "mémoire", "commission parlementaire", "projet de loi", "recours collectif",
        "injonction", "coalition", "lettre ouverte",
        "environmental", "climate", "pollution", "biodiversity", "ecosystem",
        "environmental justice", "climate litigation", "climate law", "emissions",
        "carbon", "epa", "clean air", "clean water", "conservation", "fossil fuel",
        "climate lawsuit"
    ],
    "civil_liberties": [
        "vie privée", "liberté", "libertés civiles", "surveillance", "police",
        "reconnaissance faciale", "données personnelles", "protection des données",
        "caméra", "flock", "profilage", "droit fondamental", "droits fondamentaux",
        "charte", "manifestation", "liberté d'expression", "rgpd", "cedh",
        "biométrique", "censure", "état d'urgence", "numérique", "ligue des droits",
        "mémoire", "commission parlementaire", "projet de loi", "consultation particulière",
        "action collective", "cour supérieure", "coalition",
        "privacy", "civil liberties", "facial recognition", "police surveillance",
        "data protection", "data privacy", "human rights", "flock cameras",
        "surveillance", "fourth amendment", "first amendment", "aclu", "gdpr",
        "biometric", "digital rights", "free speech", "protest rights", "constitutional challenge"
    ]
}

def clean(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(s or ""))).strip()

def truncate_text(text, max_len=240):
    if not text or len(text) <= max_len:
        return text
    cut = text[:max_len].rsplit(" ", 1)[0]
    return cut.rstrip(".,;:") + "…"

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

def detect_angle(text):
    t = text.casefold()
    angles = []
    if any(w in t for w in ("projet de loi", "bill ", "règlement", "réglementation", "loi ", "décret", "law", "regulation")):
        angles.append("📜 Législation / Réglementation")
    if any(w in t for w in ("cour ", "tribunal", "recours", "jugement", "injonction", "litige", "lawsuit", "court", "ruling", "sued")):
        angles.append("⚖️ Action en justice / Litige")
    if any(w in t for w in ("mémoire", "consultation", "bape", "commission", "audience", "brief", "hearing")):
        angles.append("🏛️ Consultation / Mémoire")
    if any(w in t for w in ("manifestation", "coalition", "lettre ouverte", "grève", "mobilisation", "protest", "campaign")):
        angles.append("✊ Mobilisation citoyenne")
    if any(w in t for w in ("surveillance", "caméra", "données", "biométrique", "ia ", "algorithme", "privacy", "facial", "tracking")):
        angles.append("👁️ Surveillance & Données")
    return " · ".join(angles) if angles else "📰 Veille thématique"

def extract_matched_keywords(text, theme):
    t = text.casefold()
    matched = [w.capitalize() for w in KEYWORDS[theme] if w in t]
    unique = list(dict.fromkeys(matched))[:4]
    return " · ".join(unique) if unique else "Thème général"

def ai_summary(item):
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        return None
    model = os.getenv("GEMINI_MODEL") or "gemini-2.5-flash"
    prompt = f"""Tu es analyste en droit public, environnement et libertés civiles. À partir UNIQUEMENT des informations ci-dessous
(rédige entièrement en français, même si la source est en anglais), produis une fiche ULTRA-COURTE en 3 lignes maximum :

**En bref :** [1 à 2 phrases factuelles maximum, 35 mots max]
**Enjeu concret :** [1 phrase courte sur l'impact réel, la loi ou l'acteur concerné]
**À suivre :** [1 question ou étape concrète en moins de 15 mots]

N'invente rien.

Titre : {item['title']}
Région : {item['region']}
Source : {item['source']}
Extrait : {item['summary']}"""
    try:
        time.sleep(2)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        r = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=60
        )
        r.raise_for_status()
        data = r.json()
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts).strip()
            return text or None
        return None
    except Exception as e:
        print("IA Gemini indisponible:", e)
        return None

def fallback(item):
    full_text = f"{item['title']} {item['summary']}"
    angle = detect_angle(full_text)
    tags = extract_matched_keywords(full_text, item["theme"])
    short_excerpt = truncate_text(item["summary"], 240)
    lines = [f"**Nature :** {angle}", f"**Notions :** {tags}"]
    if short_excerpt and short_excerpt.lower() != item["title"].lower():
        lines.append(f"**En bref :** {short_excerpt}")
    return "\n".join(lines)

def select_balanced(pool, per_theme_limit):
    pool.sort(key=lambda x: (x["score"], x["published"]), reverse=True)
    env_items = [x for x in pool if x["theme"] == "environment"]
    civ_items = [x for x in pool if x["theme"] == "civil_liberties"]
    out = []
    for i in range(per_theme_limit):
        if i < len(env_items):
            out.append(env_items[i])
        if i < len(civ_items):
            out.append(civ_items[i])
    return out

def main():
    feeds = json.load(open(os.path.join(ROOT, "feeds.json"), encoding="utf-8"))
    now = datetime.now(timezone.utc)
    now_mtl = now.astimezone(MONTREAL_TZ)
    news_pool, substack_pool, memoires_pool, failures = [], [], [], []
    seen = set()

    for f in feeds:
        section = f.get("section", "news")
        max_age_days = 30 if section == "memoires" else (14 if section == "substack" else 5)
        try:
            resp = requests.get(f["url"], headers=HEADERS, timeout=20)
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
            if parsed.bozo and not parsed.entries:
                failures.append(f["name"])
                continue
            for e in parsed.entries[:50]:
                link = e.get("link", "").strip()
                title = clean(e.get("title"))
                if not link or not title:
                    continue
                uid = hashlib.sha256(link.encode()).hexdigest()
                if uid in seen:
                    continue
                seen.add(uid)
                raw_date = e.get("published_parsed") or e.get("updated_parsed")
                published = datetime.fromtimestamp(timegm(raw_date), timezone.utc) if raw_date else now
                if now - published > timedelta(days=max_age_days):
                    continue
                summary = clean(e.get("summary") or e.get("description") or "")
                text = f"{title} {summary}"
                env = score(text, KEYWORDS["environment"]) + (2 if f.get("theme") == "environment" else 0)
                civ = score(text, KEYWORDS["civil_liberties"]) + (2 if f.get("theme") == "civil_liberties" else 0)
                if max(env, civ) < 1:
                    continue
                theme = "environment" if env >= civ else "civil_liberties"
                pub_mtl = published.astimezone(MONTREAL_TZ)
                item_obj = {
                    "id": uid,
                    "title": title,
                    "link": link,
                    "image": extract_image(e),
                    "summary": summary[:1000],
                    "source": f["name"],
                    "region": f.get("region", ""),
                    "section": section,
                    "date": pub_mtl.strftime("%d/%m/%Y · %H:%M"),
                    "published": published,
                    "theme": theme,
                    "score": max(env, civ)
                }
                if section == "memoires":
                    memoires_pool.append(item_obj)
                elif section == "substack":
                    substack_pool.append(item_obj)
                else:
                    news_pool.append(item_obj)
        except Exception as ex:
            print("Flux inaccessible:", f["name"], ex)
            failures.append(f["name"])

    selected_news = select_balanced(news_pool, 9)
    selected_substack = select_balanced(substack_pool, 7)
    selected_memoires = select_balanced(memoires_pool, 7)
    all_selected = selected_news + selected_substack + selected_memoires

    for item in all_selected:
        item["analysis"] = ai_summary(item) or fallback(item)

    def article_html(x):
        is_env = x["theme"] == "environment"
        theme_label = "Environnement" if is_env else "Libertés civiles"
        badge_class = "badge-env" if is_env else "badge-civ"
        placeholder_class = "placeholder-env" if is_env else "placeholder-civ"
        icon = "🌿" if is_env else "⚖️"

        section_badge = ""
        if x["section"] == "substack":
            section_badge = '<span class="badge badge-sub">✍️ Substack / Blogue</span>'
        elif x["section"] == "memoires":
            section_badge = '<span class="badge badge-mem">🏛️ Mémoire / Commission</span>'

        if x["image"]:
            media_block = f'<div class="card-media"><img src="{html.escape(x["image"], quote=True)}" alt="" loading="lazy" onerror="this.parentElement.classList.add(\'{placeholder_class}\'); this.outerHTML=\'<span class=\\\'placeholder-icon\\\'>{icon}</span><span class=\\\'placeholder-label\\\'>{html.escape(theme_label)}</span>\';"></div>'
        else:
            media_block = f'<div class="card-media {placeholder_class}"><span class="placeholder-icon">{icon}</span><span class="placeholder-label">{html.escape(theme_label)}</span></div>'

        return f"""<article class="story" data-section="{html.escape(x['section'])}" data-theme="{html.escape(x['theme'])}" data-region="{html.escape(x['region'])}">
            <a href="{html.escape(x['link'], quote=True)}" target="_blank" rel="noopener" class="media-link" aria-hidden="true" tabindex="-1">{media_block}</a>
            <div class="card-body">
                <div class="meta">
                    {section_badge}
                    <span class="badge {badge_class}">{html.escape(theme_label)}</span>
                    <span class="region-tag">📍 {html.escape(x['region'])}</span>
                    <span class="date-tag">{html.escape(x['date'])}</span>
                </div>
                <h2><a href="{html.escape(x['link'], quote=True)}" target="_blank" rel="noopener">{html.escape(x['title'])}</a></h2>
                <p class="source">{html.escape(x['source'])}</p>
                <div class="analysis">{format_analysis_html(x['analysis'])}</div>
                <div class="card-footer">
                    <a class="read-more" href="{html.escape(x['link'], quote=True)}" target="_blank" rel="noopener">Consulter le document <span>↗</span></a>
                </div>
            </div>
        </article>"""

    cards = "\n".join(article_html(x) for x in all_selected)
    warning = ("<div class='warning'>⚠️ Flux temporairement indisponibles : " + ", ".join(html.escape(x) for x in failures) + "</div>") if failures else ""

    with open(os.path.join(ROOT, "template.html"), encoding="utf-8") as tpl_file:
        page = tpl_file.read()

    replacements = {
        "__SITE_URL__": SITE_URL,
        "__DATE_COURTE__": now_mtl.strftime("%d-%m-%Y"),
        "__COUNT_NEWS__": str(len(selected_news)),
        "__COUNT_MEMOIRES__": str(len(selected_memoires)),
        "__COUNT_SUBSTACK__": str(len(selected_substack)),
        "__WARNING__": warning,
        "__CARDS__": cards,
        "__STAMP__": html.escape(now_mtl.strftime("%d-%m-%Y à %H:%M (heure de Montréal)"))
    }
    for k, v in replacements.items():
        page = page.replace(k, v)

    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as fp:
        fp.write(page)

    rss_items = []
    for x in all_selected:
        prefix = "[Mémoire] " if x["section"] == "memoires" else ("[Substack] " if x["section"] == "substack" else "")
        rss_items.append(f"<item><title>{prefix}[{html.escape(x['region'])}] {html.escape(x['title'])}</title><link>{html.escape(x['link'])}</link><guid isPermaLink='false'>{x['id']}</guid><pubDate>{format_datetime(x['published'])}</pubDate><description>{html.escape(x['analysis'])}</description><category>{html.escape(x['theme'])}</category></item>")

    rss = f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Observatoire — environnement et libertés civiles</title><link>{SITE_URL}</link><description>Veille quotidienne (Québec, Canada, États-Unis, Europe).</description><language>fr-ca</language><lastBuildDate>{format_datetime(now)}</lastBuildDate>{"".join(rss_items)}</channel></rss>'
    with open(os.path.join(OUT, "feed.xml"), "w", encoding="utf-8") as fp:
        fp.write(rss)

    print(f"{len(selected_news)} actualités, {len(selected_memoires)} mémoires et {len(selected_substack)} billets Substack publiés; {len(failures)} flux en échec.")

if __name__ == "__main__":
    main()
