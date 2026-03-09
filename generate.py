#!/usr/bin/env python3
"""Genera tips diarios sobre herramientas de infraestructura y DevOps."""

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml
from feedgen.feed import FeedGenerator
from groq import Groq
from jinja2 import Environment, FileSystemLoader

BASE_DIR = Path(__file__).parent
DOCS_DIR = BASE_DIR / "docs"
ARCHIVE_DIR = DOCS_DIR / "archive"


def load_config() -> dict:
    with open(BASE_DIR / "config.yaml") as f:
        return yaml.safe_load(f)


def pick_topics(config: dict, today: date) -> list[dict]:
    """Selecciona topics rotativos para hoy. Usa day-of-year para rotar."""
    topics = config["topics"]
    n = config["tips_per_day"]
    day_num = today.timetuple().tm_yday + today.year * 366
    selected = []
    for i in range(n):
        idx = (day_num + i) % len(topics)
        selected.append(topics[idx])
    return selected


def generate_tips(topics: list[dict], today: date) -> list[dict]:
    """Llama a Groq para generar los tips."""
    client = Groq(api_key=os.environ["GROQ_API_KEY"])

    topic_list = "\n".join(f"- {t['name']}" for t in topics)

    prompt = f"""Genera exactamente {len(topics)} tips tecnicos, uno por cada sistema/herramienta listado abajo.
Cada tip debe ser practico, accionable y util para un sysadmin o DevOps engineer.
Varia los temas: no repitas tips genericos. Busca funcionalidades especificas, trucos poco conocidos, mejores practicas o comandos utiles.

Fecha: {today.isoformat()}

Sistemas (uno tip por cada uno, en este orden):
{topic_list}

Responde UNICAMENTE con un JSON array, sin markdown ni texto adicional. Cada elemento debe tener:
- "system": nombre del sistema (exacto como se lista arriba)
- "title": titulo corto del tip (maximo 80 caracteres)
- "content": desarrollo del tip en 2-4 oraciones. Puede incluir comandos o ejemplos.

Responde en espanol."""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.8,
        max_tokens=4000,
    )

    raw = response.choices[0].message.content.strip()
    # Limpiar si viene envuelto en markdown
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]

    return json.loads(raw)


def update_feed(config: dict, today: date, tips: list[dict]) -> None:
    """Genera/actualiza el feed Atom."""
    feed_path = DOCS_DIR / "feed.xml"
    base_url = config["feed"]["url"].rstrip("/")

    fg = FeedGenerator()
    fg.id(f"{base_url}/feed.xml")
    fg.title(config["feed"]["title"])
    fg.subtitle(config["feed"]["description"])
    fg.language(config["feed"]["language"])
    fg.link(href=base_url, rel="alternate")
    fg.link(href=f"{base_url}/feed.xml", rel="self")
    fg.updated(datetime.now(timezone.utc))

    # Cargar entradas existentes del archivo JSON de archivo
    archive_index = load_archive_index()

    # Agregar entrada de hoy
    archive_index[today.isoformat()] = tips

    # Limitar a max_entries dias
    max_entries = config["feed"].get("max_entries", 90)
    sorted_dates = sorted(archive_index.keys(), reverse=True)[:max_entries]
    archive_index = {d: archive_index[d] for d in sorted_dates}

    # Generar entradas del feed (mas reciente primero)
    for date_str in sorted_dates:
        day_tips = archive_index[date_str]
        entry = fg.add_entry()
        entry.id(f"{base_url}/archive/{date_str}.html")
        entry.title(f"Tips del {date_str}")
        entry.link(href=f"{base_url}/archive/{date_str}.html")
        entry.published(datetime.fromisoformat(f"{date_str}T08:00:00+00:00"))
        entry.updated(datetime.fromisoformat(f"{date_str}T08:00:00+00:00"))

        # Contenido HTML
        html_content = render_tips_html(day_tips)
        entry.content(html_content, type="html")

    fg.atom_file(str(feed_path), pretty=True)

    # Guardar indice
    save_archive_index(archive_index)


def render_tips_html(tips: list[dict]) -> str:
    """Renderiza tips como HTML para el feed."""
    lines = []
    for tip in tips:
        lines.append(f'<h3>{tip["system"]}: {tip["title"]}</h3>')
        lines.append(f'<p>{tip["content"]}</p>')
    return "\n".join(lines)


def load_archive_index() -> dict:
    """Carga el indice de archivo."""
    index_path = DOCS_DIR / "archive" / "index.json"
    if index_path.exists():
        with open(index_path) as f:
            return json.load(f)
    return {}


def save_archive_index(index: dict) -> None:
    """Guarda el indice de archivo."""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    with open(ARCHIVE_DIR / "index.json", "w") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)


def generate_html_pages(config: dict, today: date, tips: list[dict]) -> None:
    """Genera paginas HTML estaticas."""
    env = Environment(loader=FileSystemLoader(BASE_DIR / "templates"))
    base_url = config["feed"]["url"].rstrip("/")
    archive_index = load_archive_index()
    sorted_dates = sorted(archive_index.keys(), reverse=True)

    # Pagina principal (hoy)
    tpl_index = env.get_template("index.html")
    html = tpl_index.render(
        config=config,
        today=today.isoformat(),
        tips=tips,
        dates=sorted_dates,
        base_url=base_url,
    )
    with open(DOCS_DIR / "index.html", "w") as f:
        f.write(html)

    # Paginas de archivo por dia
    tpl_day = env.get_template("day.html")
    for date_str in sorted_dates:
        day_tips = archive_index[date_str]
        html = tpl_day.render(
            config=config,
            date=date_str,
            tips=day_tips,
            dates=sorted_dates,
            base_url=base_url,
        )
        with open(ARCHIVE_DIR / f"{date_str}.html", "w") as f:
            f.write(html)


def main():
    today = date.today()

    # Permitir override de fecha para testing
    if len(sys.argv) > 1:
        today = date.fromisoformat(sys.argv[1])

    print(f"Generando tips para {today.isoformat()}...")

    config = load_config()

    if not config["feed"]["url"]:
        print("ADVERTENCIA: config.yaml feed.url esta vacio. Completalo con tu URL de GitHub Pages.")

    topics = pick_topics(config, today)
    print(f"Topics seleccionados: {', '.join(t['name'] for t in topics)}")

    tips = generate_tips(topics, today)
    print(f"Tips generados: {len(tips)}")

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    update_feed(config, today, tips)
    print("Feed actualizado.")

    generate_html_pages(config, today, tips)
    print("Paginas HTML generadas.")

    print("Listo!")


if __name__ == "__main__":
    main()
