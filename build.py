import os, re, json, html, hashlib, time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from email.utils import format_datetime
from calendar import timegm
from concurrent.futures import ThreadPoolExecutor, as_completed
import feedparser
import requests

ROOT = os.path.dirname(__file__)
OUT = os.path.join(ROOT, "docs")
os.makedirs(OUT, exist_ok=True)
SEEN_FILE = os.path.join(OUT, "seen_ids.json")
CACHE_FILE = os.path.join(OUT, "summaries_cache.json")

SITE_URL = "https://nachosman80.github.io/observatoire-environnement-libertes/"
MONTREAL_TZ = ZoneInfo("America/Toronto")
HEADERS = {
    "User-Agent": "ObservatoireCitoyen/1.8 (+https://github.com/nachosman80/observatoire-environnement-libertes)"
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

# Précompilation des expressions régulières pour un scoring rapide et précis
KEYWORD_PATTERNS = {
    theme: [
        (
            w,
            2 if len(w) > 12 else 1,
            re.compile(rf"\b{re.escape(w)}\b", re.IGNORECASE)
            if len(w) <= 5
            else re.compile(re.escape(w), re.IGNORECASE)
        )
        for w in words
    ]
    for theme, words in KEYWORDS.items()
}

def load_json_file(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fp:
                return json.load(fp), True
        except Exception as e:
            print(f"Impossible de lire {os.path.basename(path)}:", e)
    return default, False

def save_seen_ids(existing_list, new_ids):
    seen_set = set(existing_list)
    updated = list(existing_list)
    for uid in new_ids:
        if uid not in seen_set:
            updated.append(uid)
            seen_set.add(uid)
    updated = updated[-1500:]
    with open(SEEN_FILE, "w", encoding="utf-8") as fp:
        json.dump(updated, fp, indent=2)

def save_summaries_cache(cache_dict, active_ids):
    # Garde les résumés actifs + les 500 plus récents pour éviter la croissance infinie
    trimmed = {}
    for uid in active_ids:
        if uid in cache_dict:
            trimmed[uid] = cache_dict[uid]
    for uid, val in list(cache_dict.items())[-500:]:
        if uid not in trimmed:
            trimmed[uid] = val
    with open(CACHE_FILE, "w", encoding="utf-8") as fp:
        json.dump(trimmed, fp, ensure_ascii=False, indent=2)

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

def score(text, theme):
    return sum(weight for _, weight, pat in KEYWORD_PATTERNS[theme] if pat.search(text))

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
    matched = [w.capitalize() for w, _, pat in KEYWORD_PATTERNS[theme] if pat.search(text)]
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
        time.sleep(1.5)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        r = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=45
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

def fetch_single_feed(feed_cfg):
    """Télécharge et parse un flux RSS de manière isolée pour exécution parallèle."""
    try:
        resp = requests.get(feed_cfg["url"], headers=HEADERS, timeout=12)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
        if parsed.bozo and not parsed.entries:
            return feed_cfg, None, "Flux vide ou invalide"
        return feed_cfg, parsed.entries[:50], None
    except Exception as ex:
        return feed_cfg, None, str(ex)

def main():
    t_start = time.time()
    feeds = json.load(open(os.path.join(ROOT, "feeds.json"), encoding="utf-8"))
    previous_ids_list, has_history = load_json_file(SEEN_FILE, [])
    if not isinstance(previous_ids_list, list):
        previous_ids_list, has_history = [], False
    previous_ids_set = set(previous_ids_list)

    summaries_cache, _ = load_json_file(CACHE_FILE, {})
    if not isinstance(summaries_cache, dict):
        summaries_cache = {}

    now = datetime.now(timezone.utc)
    now_mtl = now.astimezone(MONTREAL_TZ)
    news_pool, substack_pool, memoires_pool, failures = [], [], [], []
    seen = set()

    # Téléchargement parallèle de tous les flux RSS (12 travailleurs simultanés)
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = [executor.submit(fetch_single_feed, f) for f in feeds]
        for future in as_completed(futures):
            f, entries, err = future.result()
            if err or entries is None:
                print("Flux inaccessible:", f["name"], err)
                failures.append(f["name"])
                continue

            section = f.get("section", "news")
            max_age_days = 30 if section == "memoires" else (14 if section == "substack" else 5)
            min_required = f.get("min_score", 1)

            for e in entries:
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
                env = score(text, "environment") + (2 if f.get("theme") == "environment" else 0)
                civ = score(text, "civil_liberties") + (2 if f.get("theme") == "civil_liberties" else 0)
                if max(env, civ) < min_required:
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
                    "score": max(env, civ),
                    "is_new": has_history and (uid not in previous_ids_set)
                }
                if section == "memoires":
                    memoires_pool.append(item_obj)
                elif section == "substack":
                    substack_pool.append(item_obj)
                else:
                    news_pool.append(item_obj)

    selected_news = select_balanced(news_pool, 9)
    selected_substack = select_balanced(substack_pool, 7)
    selected_memoires = select_balanced(memoires_pool, 7)
    all_selected = selected_news + selected_substack + selected_memoires

    ai_calls = 0
    for item in all_selected:
        uid = item["id"]
        if uid in summaries_cache:
            item["analysis"] = summaries_cache[uid]
        else:
            ai_text = ai_summary(item)
            if ai_text:
                ai_calls += 1
                summaries_cache[uid] = ai_text
                item["analysis"] = ai_text
            else:
                item["analysis"] = fallback(item)

    def article_html(x):
        is_env = x["theme"] == "environment"
        theme_label = "Environnement" if is_env else "Libertés civiles"
        badge_class = "badge-env" if is_env else "badge-civ"
        placeholder_class = "placeholder-env" if is_env else "placeholder-civ"
        icon = "🌿" if is_env else "⚖️"

        new_badge = '<span class="badge badge-new">✨ Nouveau</span>' if x["is_new"] else ""

        section_badge = ""
        if x["section"] == "substack":
            section_badge = '<span class="badge badge-sub">✍️ Substack / Blogue</span>'
        elif x["section"] == "memoires":
            section_badge = '<span class="badge badge-mem">🏛️ Mémoire / Commission</span>'

        if x["image"]:
            media_block = f'<div class="card-media"><img src="{html.escape(x["image"], quote=True)}" alt="" loading="lazy" onerror="this.parentElement.classList.add('{placeholder_class}'); this.outerHTML='<span class=\\'placeholder-icon\\'>{icon}</span><span class=\\'placeholder-label\\'>{html.escape(theme_label)}</span>';"></div>'
        else:
            media_block = f'<div class="card-media {placeholder_class}"><span class="placeholder-icon">{icon}</span><span class="placeholder-label">{html.escape(theme_label)}</span></div>'

        card_classes = "story is-new" if x["is_new"] else "story"
        new_attr = ' data-new="true"' if x["is_new"] else ""

        return f"""<article class="{card_classes}" data-id="{html.escape(x['id'])}" data-title="{html.escape(x['title'], quote=True)}" data-source="{html.escape(x['source'], quote=True)}" data-link="{html.escape(x['link'], quote=True)}" data-section="{html.escape(x['section'])}" data-theme="{html.escape(x['theme'])}" data-region="{html.escape(x['region'])}"{new_attr}>
            <a href="{html.escape(x['link'], quote=True)}" target="_blank" rel="noopener" class="media-link" aria-hidden="true" tabindex="-1">{media_block}</a>
            <div class="card-body">
                <div class="meta">
                    {new_badge}
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
        new_prefix = "[NOUVEAU] " if x["is_new"] else ""
        prefix = "[Mémoire] " if x["section"] == "memoires" else ("[Substack] " if x["section"] == "substack" else "")
        rss_items.append(f"<item><title>{new_prefix}{prefix}[{html.escape(x['region'])}] {html.escape(x['title'])}</title><link>{html.escape(x['link'])}</link><guid isPermaLink='false'>{x['id']}</guid><pubDate>{format_datetime(x['published'])}</pubDate><description>{html.escape(x['analysis'])}</description><category>{html.escape(x['theme'])}</category></item>")

    rss = f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Observatoire — environnement et libertés civiles</title><link>{SITE_URL}</link><description>Veille quotidienne (Québec, Canada, États-Unis, Europe).</description><language>fr-ca</language><lastBuildDate>{format_datetime(now)}</lastBuildDate>{"".join(rss_items)}</channel></rss>'
    with open(os.path.join(OUT, "feed.xml"), "w", encoding="utf-8") as fp:
        fp.write(rss)

    active_ids = [x["id"] for x in all_selected]
    save_seen_ids(previous_ids_list, active_ids)
    save_summaries_cache(summaries_cache, active_ids)

    elapsed = round(time.time() - t_start, 1)
    new_count = sum(1 for x in all_selected if x["is_new"])
    print(f"Terminé en {elapsed}s : {len(selected_news)} actualités, {len(selected_memoires)} mémoires et {len(selected_substack)} billets Substack ({new_count} nouveaux, {ai_calls} appels Gemini); {len(failures)} flux en échec.")

if __name__ == "__main__":
    main()
