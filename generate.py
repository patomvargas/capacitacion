#!/usr/bin/env python3
"""Genera noticias tecnicas diarias por sistema, cada uno con su propio feed Atom."""

import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import yaml
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


def pick_systems(config: dict, today: date) -> list[dict]:
    """Selecciona N sistemas rotativos para hoy."""
    topics = config["topics"]
    n = config["systems_per_day"]
    day_num = today.timetuple().tm_yday + today.year * 366
    selected = []
    for i in range(n):
        idx = (day_num + i) % len(topics)
        selected.append(topics[idx])
    return selected


def generate_news(systems: list[dict], today: date) -> dict[str, dict]:
    """Llama a Groq para generar una noticia tecnica por sistema. Retorna {slug: news}."""
    client = Groq(api_key=os.environ["GROQ_API_KEY"])

    system_descriptions = "\n".join(
        f"- {s['name']} (slug: {s['slug']}): {s['focus']}" for s in systems
    )

    prompt = f"""Sos un editor tecnico que escribe noticias educativas para sysadmins y DevOps engineers.

Para cada sistema listado abajo, genera UNA noticia tecnica. Cada noticia debe:
- Cubrir una funcionalidad especifica, un cambio reciente, una best practice avanzada, o un caso de uso poco conocido
- Ser tecnica y practica: incluir comandos, configuraciones, o ejemplos concretos cuando aplique
- Tener profundidad suficiente para que el lector aprenda algo nuevo y accionable
- NO ser un tip generico. Debe leerse como una noticia o articulo corto de un blog tecnico

Fecha: {today.isoformat()}

Sistemas:
{system_descriptions}

Responde UNICAMENTE con un JSON array, sin markdown ni texto adicional. Cada elemento:
- "slug": slug del sistema (exacto como se lista)
- "title": titulo de la noticia (maximo 100 caracteres, descriptivo y especifico)
- "summary": resumen en 1-2 oraciones
- "content": desarrollo completo en 3-6 parrafos. Usa saltos de linea entre parrafos. Incluye comandos o ejemplos de configuracion cuando sea relevante (envueltos en backticks).

Responde en espanol tecnico (puede incluir terminos en ingles cuando es lo estandar)."""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.8,
        max_tokens=8000,
    )

    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]

    # Limpiar caracteres de control que el modelo a veces genera
    import re
    raw = re.sub(r'[\x00-\x09\x0b\x0c\x0e-\x1f]', ' ', raw)

    news_list = json.loads(raw, strict=False)
    return {item["slug"]: item for item in news_list}


def load_system_archive(slug: str) -> dict:
    """Carga el archivo historico de un sistema. {date_str: news_item}"""
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

    sorted_dates = sorted(archive.keys(), reverse=True)[:max_entries]

    for date_str in sorted_dates:
        news = archive[date_str]
        entry = fg.add_entry()
        entry.id(f"{base_url}/archive/{slug}/{date_str}.html")
        entry.title(news["title"])
        entry.link(href=f"{base_url}/archive/{slug}/{date_str}.html")
        entry.published(datetime.fromisoformat(f"{date_str}T08:00:00+00:00"))
        entry.updated(datetime.fromisoformat(f"{date_str}T08:00:00+00:00"))
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
        # Convertir backticks a <code>
        import re
        p = re.sub(r"`([^`]+)`", r"<code>\1</code>", p)
        html_parts.append(f"<p>{p}</p>")
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


def generate_html(config: dict, today: date, todays_news: dict[str, dict]) -> None:
    """Genera todas las paginas HTML."""
    env = Environment(loader=FileSystemLoader(BASE_DIR / "templates"))
    base_url = config["site"]["url"].rstrip("/")

    # Landing page
    all_systems = []
    for topic in config["topics"]:
        archive = load_system_archive(topic["slug"])
        latest = None
        if archive:
            latest_date = sorted(archive.keys(), reverse=True)[0]
            latest = {"date": latest_date, **archive[latest_date]}
        all_systems.append({**topic, "latest": latest, "count": len(archive)})

    tpl_index = env.get_template("index.html")
    html = tpl_index.render(
        config=config,
        today=today.isoformat(),
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

        sorted_dates = sorted(archive.keys(), reverse=True)
        system_dir = ARCHIVE_DIR / topic["slug"]
        system_dir.mkdir(parents=True, exist_ok=True)

        # Index del sistema
        html = tpl_system.render(
            config=config,
            topic=topic,
            dates=sorted_dates,
            archive=archive,
            base_url=base_url,
        )
        with open(system_dir / "index.html", "w") as f:
            f.write(html)

        # Articulos individuales
        for i, date_str in enumerate(sorted_dates):
            news = archive[date_str]
            prev_date = sorted_dates[i + 1] if i + 1 < len(sorted_dates) else None
            next_date = sorted_dates[i - 1] if i > 0 else None

            html = tpl_article.render(
                config=config,
                topic=topic,
                news=news,
                date=date_str,
                prev_date=prev_date,
                next_date=next_date,
                base_url=base_url,
            )
            with open(system_dir / f"{date_str}.html", "w") as f:
                f.write(html)


def main():
    today = date.today()
    if len(sys.argv) > 1:
        today = date.fromisoformat(sys.argv[1])

    print(f"Generando noticias para {today.isoformat()}...")

    config = load_config()
    systems = pick_systems(config, today)
    print(f"Sistemas de hoy: {', '.join(s['name'] for s in systems)}")

    news = generate_news(systems, today)
    print(f"Noticias generadas: {len(news)}")

    # Actualizar archivos y feeds por sistema
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    max_entries = config["site"].get("max_entries", 60)
    for topic in config["topics"]:
        archive = load_system_archive(topic["slug"])

        if topic["slug"] in news:
            archive[today.isoformat()] = news[topic["slug"]]
            # Limitar historial
            sorted_dates = sorted(archive.keys(), reverse=True)[:max_entries]
            archive = {d: archive[d] for d in sorted_dates}
            save_system_archive(topic["slug"], archive)
            print(f"  [{topic['name']}] nueva noticia guardada")

        if archive:
            update_system_feed(config, topic, archive)

    generate_opml(config)
    print("OPML generado.")

    generate_html(config, today, news)
    print("HTML generado.")

    print("Listo!")


if __name__ == "__main__":
    main()
