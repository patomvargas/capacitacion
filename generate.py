#!/usr/bin/env python3
"""Genera noticias tecnicas diarias por sistema, cada uno con su propio feed Atom.

Busca noticias reales via DuckDuckGo y las usa como fuente para el articulo."""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS
from feedgen.feed import FeedGenerator
from groq import Groq
from jinja2 import Environment, FileSystemLoader

BASE_DIR = Path(__file__).parent
DOCS_DIR = BASE_DIR / "docs"
FEEDS_DIR = DOCS_DIR / "feeds"
ARCHIVE_DIR = DOCS_DIR / "archive"


def load_config() -> dict:
    with open(BASE_DIR / "config.yaml") as f:
        return yaml.safe_load(f)


def pick_systems(config: dict, now: datetime) -> list[dict]:
    """Selecciona N sistemas rotativos para hoy."""
    topics = config["topics"]
    n = config["systems_per_day"]
    day_num = now.timetuple().tm_yday + now.year * 366
    selected = []
    for i in range(n):
        idx = (day_num + i) % len(topics)
        selected.append(topics[idx])
    return selected


def search_news(system: dict) -> list[dict]:
    """Busca noticias recientes sobre un sistema via DuckDuckGo."""
    queries = [
        f"{system['name']} release update changelog 2025 2026",
        f"{system['name']} new feature announcement",
    ]

    results = []
    with DDGS() as ddgs:
        for query in queries:
            try:
                hits = list(ddgs.news(query, max_results=5, timelimit="m"))
                results.extend(hits)
            except Exception:
                pass
            if not results:
                try:
                    hits = list(ddgs.text(query, max_results=5, timelimit="m"))
                    results.extend(hits)
                except Exception:
                    pass

    # Deduplicar por titulo
    seen = set()
    unique = []
    for r in results:
        title = r.get("title", "")
        if title not in seen:
            seen.add(title)
            unique.append(r)

    return unique[:8]


def generate_news_for_system(client: Groq, system: dict, search_results: list[dict], now: datetime) -> dict:
    """Genera una noticia para un sistema basandose en resultados de busqueda reales."""

    sources_text = ""
    for i, r in enumerate(search_results, 1):
        title = r.get("title", "Sin titulo")
        body = r.get("body", r.get("description", ""))
        url = r.get("url", r.get("href", ""))
        date = r.get("date", r.get("published", ""))
        sources_text += f"\n[{i}] {title}\n    Fecha: {date}\n    URL: {url}\n    {body}\n"

    if not sources_text.strip():
        sources_text = "\nNo se encontraron noticias recientes. Genera contenido basado en las mejores practicas mas actuales y features estables del sistema.\n"

    prompt = f"""Sos un editor tecnico que escribe articulos para sysadmins y DevOps engineers.

Escribi UN articulo tecnico sobre **{system['name']}** basandote en las fuentes reales de abajo.
El articulo debe:
- Estar basado en informacion real de las fuentes proporcionadas
- Ser tecnico y practico: incluir comandos, configuraciones o ejemplos concretos
- Cubrir novedades, cambios, mejoras o best practices reales del sistema
- Citar las fuentes al final (URLs)
- Si las fuentes no tienen info relevante, escribi sobre una best practice avanzada y actual

Enfoque del sistema: {system['focus']}
Fecha actual: {now.strftime('%Y-%m-%d %H:%M UTC')}

FUENTES:
{sources_text}

Responde UNICAMENTE con un JSON object (sin markdown ni texto adicional):
- "slug": "{system['slug']}"
- "title": titulo del articulo (maximo 100 caracteres, especifico)
- "summary": resumen en 1-2 oraciones
- "content": desarrollo completo en 3-6 parrafos. Usa saltos de linea entre parrafos. Incluye comandos o configuraciones cuando sea relevante (envueltos en backticks).
- "sources": array de objetos con "title" y "url" de las fuentes usadas

Responde en espanol tecnico (puede incluir terminos en ingles cuando es lo estandar)."""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5,
        max_tokens=4000,
    )

    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]

    raw = re.sub(r'[\x00-\x09\x0b\x0c\x0e-\x1f]', ' ', raw)
    return json.loads(raw, strict=False)


def make_entry_key(now: datetime) -> str:
    """Genera clave unica con fecha y hora."""
    return now.strftime("%Y-%m-%d_%H%M")


def load_system_archive(slug: str) -> dict:
    """Carga el archivo historico de un sistema. {entry_key: news_item}"""
    path = ARCHIVE_DIR / slug / "index.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def save_system_archive(slug: str, archive: dict) -> None:
    """Guarda el archivo historico de un sistema."""
    dir_path = ARCHIVE_DIR / slug
    dir_path.mkdir(parents=True, exist_ok=True)
    with open(dir_path / "index.json", "w") as f:
        json.dump(archive, f, indent=2, ensure_ascii=False)


def entry_key_to_datetime(key: str) -> datetime:
    """Convierte entry key a datetime para el feed."""
    if "_" in key:
        return datetime.strptime(key, "%Y-%m-%d_%H%M").replace(tzinfo=timezone.utc)
    # Retrocompatibilidad con claves viejas (solo fecha)
    return datetime.strptime(key, "%Y-%m-%d").replace(hour=8, tzinfo=timezone.utc)


def update_system_feed(config: dict, topic: dict, archive: dict) -> None:
    """Genera el feed Atom para un sistema."""
    FEEDS_DIR.mkdir(parents=True, exist_ok=True)
    slug = topic["slug"]
    base_url = config["site"]["url"].rstrip("/")
    max_entries = config["site"].get("max_entries", 60)

    fg = FeedGenerator()
    fg.id(f"{base_url}/feeds/{slug}.xml")
    fg.title(f"{topic['name']} - {config['site']['title']}")
    fg.subtitle(f"Noticias tecnicas sobre {topic['name']}: {topic['focus']}")
    fg.language(config["site"]["language"])
    fg.link(href=f"{base_url}/archive/{slug}/", rel="alternate")
    fg.link(href=f"{base_url}/feeds/{slug}.xml", rel="self")
    fg.updated(datetime.now(timezone.utc))

    sorted_keys = sorted(archive.keys(), reverse=True)[:max_entries]

    for key in sorted_keys:
        news = archive[key]
        entry_dt = entry_key_to_datetime(key)
        entry = fg.add_entry()
        entry.id(f"{base_url}/archive/{slug}/{key}.html")
        entry.title(news["title"])
        entry.link(href=f"{base_url}/archive/{slug}/{key}.html")
        entry.published(entry_dt)
        entry.updated(entry_dt)
        entry.summary(news.get("summary", ""))

        content_html = format_content_html(news)
        entry.content(content_html, type="html")

    fg.atom_file(str(FEEDS_DIR / f"{slug}.xml"), pretty=True)


def format_content_html(news: dict) -> str:
    """Convierte el contenido de una noticia a HTML."""
    content = news.get("content", "")
    paragraphs = [p.strip() for p in content.split("\n") if p.strip()]
    html_parts = []
    for p in paragraphs:
        p = re.sub(r"`([^`]+)`", r"<code>\1</code>", p)
        html_parts.append(f"<p>{p}</p>")

    # Agregar fuentes si existen
    sources = news.get("sources", [])
    if sources:
        html_parts.append("<hr><p><strong>Fuentes:</strong></p><ul>")
        for s in sources:
            title = s.get("title", s.get("url", ""))
            url = s.get("url", "")
            if url:
                html_parts.append(f'<li><a href="{url}">{title}</a></li>')
            else:
                html_parts.append(f"<li>{title}</li>")
        html_parts.append("</ul>")

    return "\n".join(html_parts)


def generate_opml(config: dict) -> None:
    """Genera archivo OPML para importar todos los feeds de una."""
    base_url = config["site"]["url"].rstrip("/")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<opml version="2.0">',
        "  <head>",
        f"    <title>{config['site']['title']}</title>",
        "  </head>",
        "  <body>",
        f'    <outline text="{config["site"]["title"]}" title="{config["site"]["title"]}">',
    ]
    for topic in config["topics"]:
        lines.append(
            f'      <outline type="rss" text="{topic["name"]}" title="{topic["name"]}" '
            f'xmlUrl="{base_url}/feeds/{topic["slug"]}.xml" '
            f'htmlUrl="{base_url}/archive/{topic["slug"]}/" />'
        )
    lines.append("    </outline>")
    lines.append("  </body>")
    lines.append("</opml>")

    with open(DOCS_DIR / "feeds.opml", "w") as f:
        f.write("\n".join(lines))


def generate_html(config: dict, now: datetime, todays_news: dict[str, dict], entry_key: str) -> None:
    """Genera todas las paginas HTML."""
    env = Environment(loader=FileSystemLoader(BASE_DIR / "templates"))
    base_url = config["site"]["url"].rstrip("/")

    # Landing page
    all_systems = []
    for topic in config["topics"]:
        archive = load_system_archive(topic["slug"])
        latest = None
        if archive:
            latest_key = sorted(archive.keys(), reverse=True)[0]
            latest = {"key": latest_key, **archive[latest_key]}
        all_systems.append({**topic, "latest": latest, "count": len(archive)})

    tpl_index = env.get_template("index.html")
    html = tpl_index.render(
        config=config,
        today=now.strftime("%Y-%m-%d %H:%M UTC"),
        systems=all_systems,
        todays_slugs=[s["slug"] for s in all_systems if s["slug"] in todays_news],
        base_url=base_url,
    )
    with open(DOCS_DIR / "index.html", "w") as f:
        f.write(html)

    # Paginas por sistema
    tpl_system = env.get_template("system.html")
    tpl_article = env.get_template("article.html")

    for topic in config["topics"]:
        archive = load_system_archive(topic["slug"])
        if not archive:
            continue

        sorted_keys = sorted(archive.keys(), reverse=True)
        system_dir = ARCHIVE_DIR / topic["slug"]
        system_dir.mkdir(parents=True, exist_ok=True)

        # Index del sistema
        html = tpl_system.render(
            config=config,
            topic=topic,
            keys=sorted_keys,
            archive=archive,
            base_url=base_url,
        )
        with open(system_dir / "index.html", "w") as f:
            f.write(html)

        # Articulos individuales
        for i, key in enumerate(sorted_keys):
            news = archive[key]
            prev_key = sorted_keys[i + 1] if i + 1 < len(sorted_keys) else None
            next_key = sorted_keys[i - 1] if i > 0 else None

            html = tpl_article.render(
                config=config,
                topic=topic,
                news=news,
                entry_key=key,
                prev_key=prev_key,
                next_key=next_key,
                base_url=base_url,
            )
            with open(system_dir / f"{key}.html", "w") as f:
                f.write(html)


def main():
    now = datetime.now(timezone.utc)

    print(f"Generando noticias - {now.strftime('%Y-%m-%d %H:%M UTC')}...")

    config = load_config()
    systems = pick_systems(config, now)
    print(f"Sistemas de hoy: {', '.join(s['name'] for s in systems)}")

    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    entry_key = make_entry_key(now)
    news = {}

    for system in systems:
        print(f"  [{system['name']}] buscando noticias...")
        search_results = search_news(system)
        print(f"  [{system['name']}] {len(search_results)} fuentes encontradas, generando articulo...")

        try:
            article = generate_news_for_system(client, system, search_results, now)
            news[system["slug"]] = article
            print(f"  [{system['name']}] OK: {article.get('title', '?')}")
        except Exception as e:
            print(f"  [{system['name']}] ERROR: {e}")

    print(f"\nNoticias generadas: {len(news)}")
    if not news:
        print("ERROR: No se genero ninguna noticia, abortando.")
        sys.exit(1)

    # Actualizar archivos y feeds por sistema
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    max_entries = config["site"].get("max_entries", 60)
    for topic in config["topics"]:
        archive = load_system_archive(topic["slug"])

        if topic["slug"] in news:
            archive[entry_key] = news[topic["slug"]]
            # Limitar historial
            sorted_keys = sorted(archive.keys(), reverse=True)[:max_entries]
            archive = {k: archive[k] for k in sorted_keys}
            save_system_archive(topic["slug"], archive)

        if archive:
            update_system_feed(config, topic, archive)

    generate_opml(config)
    generate_html(config, now, news, entry_key)

    print("Listo!")


if __name__ == "__main__":
    main()
