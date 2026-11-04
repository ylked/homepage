# Homelab homepage

A static start page for the services in my homelab, generated from one YAML file.
There's no server, database or login: `build.py` turns `config.yaml` into a
self-contained `index.html` (CSS and JS inlined) that any web server can host.

- Categories and services are defined in [`config.yaml`](config.yaml), which also documents every field.
- Each service shows a 🌐 **Public** or 🔒 **LAN only** badge.
- Live up/down dots come from an Uptime Kuma status page (optional).
- Search: press `/` to focus, `Enter` opens the first match, `Esc` clears.
- Light/dark mode follows the system, and the button cycles auto → dark → light.

## Public and LAN-only services

There is one page, and every service on it is linked. Services marked 🔒 **LAN only**
(no `external: true`) only open from the home network or VPN. From outside, the
link just won't load.

The page contains every hostname in `config.yaml`, so only list addresses you
don't mind being visible.

## Usage

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
.venv/bin/python build.py            # build dist/index.html
```

```bash
.venv/bin/python build.py --serve    # build, rebuild on save, serve on http://127.0.0.1:8000
```

Other flags: `-c other.yaml`, `-o outdir`, `--watch`, `-p PORT`.
Config mistakes are reported with their location, e.g.
`error: categories[0].services[2] ('Sonarr') is missing a 'url'`.

### Icons

| Syntax | Source |
| --- | --- |
| `di:jellyfin` | [dashboard-icons](https://github.com/homarr-labs/dashboard-icons) (CDN) |
| `sh:jellyfin` | [selfh.st icons](https://selfh.st/icons/) (CDN) |
| `si:proxmox` | [Simple Icons](https://simpleicons.org) (monochrome, inverted in dark mode) |
| `local:foo.svg` | `icons/foo.svg`, inlined into the HTML at build time (works offline) |
| `🎬` / any URL | used as-is |

If you leave the icon out, the card shows the first letter of the name.

## Uptime Kuma status

1. In Uptime Kuma, create a **status page**, add the monitors to it, and publish it.
2. Point `site.uptime_kuma.url` and `slug` at it.
3. On each service, set `monitor:` to the monitor's name (case-insensitive) or its numeric id.

The visitor's browser reads `/api/status-page/<slug>` and
`/api/status-page/heartbeat/<slug>`, the same public endpoints Kuma's own status
page uses, so nothing secret is involved. It refreshes every `refresh` seconds and
pauses while the tab is hidden. Dots: green up, red down (pulsing), amber pending,
indigo maintenance, grey unknown. Hover a dot for uptime (24h), ping and error.

**CORS:** the homepage and Kuma live on different origins, so the browser needs
Kuma's responses to allow your homepage. If the summary says *Status unavailable*
and the console shows a CORS error, add the header in the reverse proxy in front
of Kuma:

```caddyfile
# Caddy
status.example.com {
    header /api/status-page/* Access-Control-Allow-Origin "https://home.example.com"
    reverse_proxy uptime-kuma:3001
}
```

```nginx
# nginx
location /api/status-page/ {
    add_header Access-Control-Allow-Origin "https://home.example.com" always;
    proxy_pass http://uptime-kuma:3001;
}
```

```yaml
# Traefik (labels on the uptime-kuma container)
- traefik.http.middlewares.kuma-cors.headers.accessControlAllowOriginList=https://home.example.com
- traefik.http.routers.kuma.middlewares=kuma-cors
```

If Kuma is unreachable the page still works: the dots stay grey and nothing else changes.

## Deploying

The page is a single file, so you can copy it to any static host:

```bash
rsync dist/index.html server:/srv/www/home/
```

Or build and serve it with nginx using Docker Compose. Run this again after
editing `config.yaml`:

```bash
docker compose up -d --build
```

It listens on port `8080` on all interfaces. To change that, set `HOMEPAGE_PORT`
or `HOMEPAGE_BIND` in the environment or in a `.env` file next to
`docker-compose.yml`, for example `HOMEPAGE_PORT=18080`.

## Customising

- **Content**: `config.yaml`
- **Look**: [`assets/style.css`](assets/style.css). Colours are CSS variables at the top, light and dark.
- **Markup**: [`templates/index.html.j2`](templates/index.html.j2) (Jinja2)
- **Behaviour**: [`assets/app.js`](assets/app.js) (search, theme, status)
