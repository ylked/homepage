#!/usr/bin/env python3
"""Generate a static homelab homepage from a YAML config.

Every service is linked. LAN-only ones are marked with a lock and simply
won't open from outside the home network.

Usage:
    python build.py           # build dist/index.html
    python build.py --serve   # build, watch and serve on localhost
"""

from __future__ import annotations

import argparse
import base64
import functools
import http.server
import json
import mimetypes
import re
import socketserver
import sys
import threading
import time
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).resolve().parent

# Remote icon packs, addressed in the config as "<prefix>:<name>".
ICON_PACKS = {
    "di": "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/{name}.svg",
    "sh": "https://cdn.jsdelivr.net/gh/selfhst/icons/svg/{name}.svg",
    "si": "https://cdn.jsdelivr.net/npm/simple-icons@13.0.0/icons/{name}.svg",
}

DEFAULT_LABELS = {
    "external": "Public",
    "internal": "LAN only",
    "search_placeholder": "Search services…",
    "empty": "No service matches that search.",
    "lan_link": "LAN",
}

class ConfigError(Exception):
    """A problem in config.yaml that the user needs to fix."""


def fail(message: str) -> ConfigError:
    return ConfigError(message)


# --------------------------------------------------------------------------- #
# Config loading
# --------------------------------------------------------------------------- #

def load_config(path: Path) -> dict:
    if not path.exists():
        raise fail(f"config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise fail(f"{path.name} is not valid YAML:\n{exc}") from exc
    if raw is None:
        raise fail(f"{path.name} is empty")
    if not isinstance(raw, dict):
        raise fail(f"{path.name} must be a mapping with 'site' and 'categories' keys")

    site = raw.get("site") or {}
    if not isinstance(site, dict):
        raise fail("'site' must be a mapping")
    site.setdefault("title", "Homelab")
    for optional in ("subtitle", "favicon", "footer"):
        site.setdefault(optional, None)
    site.setdefault("theme", "auto")
    site.setdefault("lang", "en")
    site.setdefault("open_in_new_tab", True)
    site.setdefault("show_uptime", True)

    labels = dict(DEFAULT_LABELS)
    labels.update(site.get("labels") or {})
    site["labels"] = labels
    site["uptime_kuma"] = normalize_kuma(site.get("uptime_kuma"))

    categories = [
        normalize_category(entry, index)
        for index, entry in enumerate(raw.get("categories") or [])
    ]
    if not any(cat["services"] for cat in categories):
        raise fail("no visible services found — add at least one category with services")

    return {"site": site, "categories": categories}


def normalize_kuma(value: object) -> dict | None:
    """Validate the Uptime Kuma block; returns None when status is not wanted."""
    if not value:
        return None
    if not isinstance(value, dict):
        raise fail("'site.uptime_kuma' must be a mapping")
    url = (value.get("url") or "").rstrip("/")
    slug = value.get("slug")
    if not url:
        raise fail("'site.uptime_kuma' needs a 'url' (e.g. https://status.example.com)")
    if not slug:
        raise fail("'site.uptime_kuma' needs the 'slug' of a published status page")
    refresh = value.get("refresh", 60)
    if not isinstance(refresh, int) or refresh < 10:
        raise fail("'site.uptime_kuma.refresh' must be an integer of at least 10 (seconds)")
    return {"url": url, "slug": str(slug), "refresh": refresh}


def normalize_category(entry: object, index: int) -> dict:
    where = f"categories[{index}]"
    if not isinstance(entry, dict):
        raise fail(f"{where} must be a mapping, got {type(entry).__name__}")
    name = entry.get("name")
    if not name:
        raise fail(f"{where} is missing a 'name'")

    services = entry.get("services") or []
    if not isinstance(services, list):
        raise fail(f"{where} ('{name}'): 'services' must be a list")

    return {
        "name": str(name),
        "description": entry.get("description"),
        "icon": resolve_icon(entry.get("icon"), f"{where}.icon"),
        "hidden": bool(entry.get("hidden")),
        "services": [
            normalize_service(svc, f"{where}.services[{i}]")
            for i, svc in enumerate(services)
        ],
    }


def normalize_service(entry: object, where: str) -> dict:
    if not isinstance(entry, dict):
        raise fail(f"{where} must be a mapping, got {type(entry).__name__}")
    name = entry.get("name")
    if not name:
        raise fail(f"{where} is missing a 'name'")
    url = entry.get("url")
    if not url:
        raise fail(f"{where} ('{name}') is missing a 'url'")

    tags = entry.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]

    return {
        "name": str(name),
        "url": str(url),
        "local_url": entry.get("local_url"),
        "description": entry.get("description"),
        "external": bool(entry.get("external", False)),
        "badge": entry.get("badge"),
        "monitor": entry.get("monitor"),
        "hidden": bool(entry.get("hidden")),
        "tags": [str(tag) for tag in tags],
        "icon": resolve_icon(entry.get("icon"), f"{where}.icon"),
    }


# --------------------------------------------------------------------------- #
# View
# --------------------------------------------------------------------------- #

def project(config: dict) -> dict:
    """Drop hidden categories and services, and add what the template needs."""
    categories = []
    for cat in config["categories"]:
        if cat["hidden"]:
            continue
        services = [view_service(svc) for svc in cat["services"] if not svc["hidden"]]
        if services:
            categories.append({**cat, "services": services})

    if not categories:
        raise fail("no visible services found — every service is hidden")
    return {"site": config["site"], "categories": categories}


def view_service(svc: dict) -> dict:
    search = " ".join(
        part.lower()
        for part in [svc["name"], svc["description"] or "", *svc["tags"], svc["url"]]
    ).strip()
    return {**svc, "search": search}


# --------------------------------------------------------------------------- #
# Icons
# --------------------------------------------------------------------------- #

def resolve_icon(value: object, where: str) -> dict | None:
    """Turn an icon shorthand into something the template can render.

    Accepted forms:
      emoji                 -> rendered as text
      di:/sh:/si:<name>     -> fetched from a CDN icon pack
      local:<file>          -> read from icons/ and inlined as a data URI
      http(s)://… or /path  -> used as an image URL verbatim
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise fail(f"{where} must be a string")
    value = value.strip()
    if not value:
        return None

    prefix, _, rest = value.partition(":")
    if prefix in ICON_PACKS and rest:
        return {"kind": "img", "src": ICON_PACKS[prefix].format(name=rest), "mono": prefix == "si"}
    if prefix == "local" and rest:
        return {"kind": "img", "src": inline_local_icon(rest, where), "mono": False}
    if value.startswith(("http://", "https://", "/", "./", "data:")):
        return {"kind": "img", "src": value, "mono": False}
    return {"kind": "text", "text": value[:2]}


@functools.lru_cache(maxsize=None)
def inline_local_icon(filename: str, where: str) -> str:
    path = ROOT / "icons" / filename
    if not path.is_file():
        raise fail(f"{where}: icons/{filename} not found")
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{payload}"


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def read_asset(name: str) -> str:
    path = ROOT / "assets" / name
    if not path.is_file():
        raise fail(f"missing asset: assets/{name}")
    return path.read_text(encoding="utf-8")


def render(view: dict) -> str:
    env = Environment(
        loader=FileSystemLoader(ROOT / "templates"),
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    site = view["site"]
    kuma = site["uptime_kuma"]
    runtime = {
        "kuma": kuma,
        "labels": {"empty": site["labels"]["empty"]},
        "showUptime": bool(site["show_uptime"] and kuma),
    }
    html = env.get_template("index.html.j2").render(
        site=site,
        categories=view["categories"],
        total=sum(len(cat["services"]) for cat in view["categories"]),
        # "</" would end the <script> element the JSON is embedded in.
        runtime=json.dumps(runtime).replace("</", "<\\/"),
        css=read_asset("style.css"),
        js=read_asset("app.js"),
    )
    return re.sub(r"\n{3,}", "\n\n", html)


def build(config_path: Path, out_dir: Path) -> tuple[Path, int]:
    view = project(load_config(config_path))
    target = out_dir / "index.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(view), encoding="utf-8")
    return target, sum(len(c["services"]) for c in view["categories"])


# --------------------------------------------------------------------------- #
# Watch / serve
# --------------------------------------------------------------------------- #

def collect_sources(config_path: Path) -> dict[Path, float]:
    watched = [config_path]
    for folder in ("templates", "assets", "icons"):
        watched.extend(p for p in (ROOT / folder).rglob("*") if p.is_file())
    stamps = {}
    for path in watched:
        try:
            stamps[path] = path.stat().st_mtime
        except OSError:
            continue
    return stamps


def watch(config_path: Path, out_dir: Path) -> None:
    print("Watching config.yaml, templates/, assets/ and icons/ — Ctrl-C to stop.")
    stamps = collect_sources(config_path)
    while True:
        time.sleep(0.5)
        current = collect_sources(config_path)
        if current == stamps:
            continue
        stamps = current
        try:
            _, count = build(config_path, out_dir)
        except ConfigError as exc:
            print(f"\n  error: {exc}\n", file=sys.stderr)
            continue
        print(f"  rebuilt — {count} services")


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def serve(out_dir: Path, port: int) -> None:
    handler = functools.partial(QuietHandler, directory=str(out_dir))
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", port), handler)
    print(f"Serving {out_dir} at http://127.0.0.1:{port}")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()


# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a static homelab homepage from YAML.")
    parser.add_argument("-c", "--config", default="config.yaml", type=Path)
    parser.add_argument("-o", "--out", default="dist", type=Path, help="output directory")
    parser.add_argument("-w", "--watch", action="store_true", help="rebuild on file changes")
    parser.add_argument("-s", "--serve", action="store_true", help="serve the output and watch")
    parser.add_argument("-p", "--port", default=8000, type=int)
    args = parser.parse_args(argv)

    config_path = args.config if args.config.is_absolute() else ROOT / args.config
    out_dir = args.out if args.out.is_absolute() else ROOT / args.out

    try:
        target, count = build(config_path, out_dir)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    shown = target.relative_to(ROOT) if target.is_relative_to(ROOT) else target
    print(f"Built {shown} — {count} services")

    if args.serve:
        serve(out_dir, args.port)
    if args.serve or args.watch:
        try:
            watch(config_path, out_dir)
        except KeyboardInterrupt:
            print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
