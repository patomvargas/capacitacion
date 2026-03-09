# Tech News & Learning Feeds

Noticias técnicas diarias generadas automáticamente sobre herramientas de infraestructura, DevOps y productividad. Cada sistema tiene su propio feed Atom/RSS para suscribirse desde cualquier lector.

## Sistemas cubiertos

Google Workspace, FreeIPA, BIND9, OpenVPN, Kubernetes, OpenShift, Claude Code, Gemini, ChatGPT, Metabase, Airflow, n8n, Superset, Traefik, MetalLB, PostgreSQL, Docker, Git, Terraform, Ansible.

## Como suscribirse

### Importar todos los feeds de una (OPML)

La mayoría de lectores RSS permiten importar un archivo OPML:

```
https://patomvargas.github.io/capacitacion/feeds.opml
```

### Feeds individuales

Cada sistema tiene su propio feed en:

```
https://patomvargas.github.io/capacitacion/feeds/{slug}.xml
```

Por ejemplo:
- `feeds/kubernetes.xml`
- `feeds/postgresql.xml`
- `feeds/docker.xml`

## Como funciona

1. **GitHub Actions** ejecuta `generate.py` diariamente a las 08:00 UTC
2. El script selecciona 5 sistemas (rotando entre los 20 configurados)
3. Llama a **Groq** (Llama 3.3 70B) para generar una noticia técnica por sistema
4. Actualiza los feeds Atom y las páginas HTML estáticas
5. Commitea los cambios → **GitHub Pages** los publica automáticamente

En 4 días se cubren todos los sistemas. El historial se mantiene por 60 días.

## Desarrollo local

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

export GROQ_API_KEY=tu-key
python generate.py              # genera para hoy
python generate.py 2026-03-15   # genera para una fecha especifica
```

## Agregar un nuevo sistema

Editar `config.yaml` y agregar una entrada en `topics`:

```yaml
- name: Prometheus
  slug: prometheus
  tags: [monitoring, observabilidad, metricas]
  focus: "PromQL, alerting rules, recording rules, service discovery, federation"
```

## Stack

- **Groq** (Llama 3.3 70B) — generación de contenido, tier gratuito
- **GitHub Actions** — ejecución diaria
- **GitHub Pages** — hosting estático
- **feedgen** — generación de feeds Atom
- **Jinja2** — templates HTML
