#!/usr/bin/env python3
"""Sirve la pantalla de la barra y el editor en la red local."""

import gzip
import io
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlsplit

from PIL import Image

import firestore_io

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
CONTENT_PATH = ROOT / "content.json"
SCREENS_PATH = ROOT / "screens.json"
PLAYLIST_PATH = ROOT / "playlist.json"
ELEMENTS_PATH = ROOT / "elements.json"
CATALOG_PATH = ROOT / "catalog.json"
CREATIONS_PATH = ROOT / "creations.json"
ELEMENT_IDS = ("rail", "offer", "fresh")
CUSTOM_ID = re.compile(r"^c\d{1,16}$")
LIST_ID = re.compile(r"^l\d{1,16}$")
CREATION_FONTS = {
    "Poppins, sans-serif",
    "Montserrat, sans-serif",
    "Oswald, sans-serif",
    '"Bebas Neue", sans-serif',
    "Anton, sans-serif",
    '"Playfair Display", serif',
}
PORT = 8787
SCREEN_BASE = [
    {"id": "121", "online": False},
    {"id": "122", "online": False},
    {"id": "123", "online": True},
    {"id": "124", "online": False},
]
BRANCHES = [
    {"id": "mixo", "name": "Mixxo"},
    {"id": "centro", "name": "Centro"},
    {"id": "alta", "name": "Alta Córdoba"},
    {"id": "arguello", "name": "Argüello"},
]
BRANCH_IDS = {branch["id"] for branch in BRANCHES}
SCREEN_BRANCH = "mixo"
BRANDS = (
    "Las Tres Estrellas",
    "Las Tres Niñas",
    "La Serenísima",
    "De La Familia",
    "Gran Compra",
    "Provitalis",
    "Apóstoles",
    "Milkaut",
    "Nestlé",
    "Cindor",
    "Ilolay",
    "Manfrey",
)

DEFAULT = {
    "products": [
        {
            "brand": "De La Familia",
            "name": "Leche entera",
            "detail": "Brick 1 litro",
            "price": 1599,
            "listPrice": 1909,
            "badge": "Oferta",
            "image": "/img/leche.png",
        },
        {
            "brand": "Ilolay",
            "name": "Yogur frutilla",
            "detail": "Sachet 900 g",
            "price": 1899,
            "listPrice": 2679,
            "badge": "Oferta",
            "image": "/img/yogur.png",
        },
        {
            "brand": "Las Tres Estrellas",
            "name": "Queso cremoso",
            "detail": "Precio por kg",
            "price": 8990,
            "listPrice": 12009,
            "badge": "Oferta",
            "image": "/img/queso.png",
        },
    ]
}

ELEMENT_DEFAULT = {
    "rail": {
        "shapes": {"spine": True, "frames": True, "gleam": True},
    },
    "offer": {
        "kicker": "Ofertas",
        "hero": "2×1",
        "title": "Llevá 2, pagá 1",
        "detail": "en leches, yogures y quesos",
        "shapes": {"spine": True, "pill": True, "halo": True, "particles": True, "shine": True},
    },
    "fresh": {
        "kicker": "Lácteos",
        "hero": "Fresco",
        "seal": "Hoy",
        "sealDetail": "en góndola",
        "title": "Recién repuesto",
        "detail": "Cadena de frío · todos los días",
        "shapes": {
            "spine": True,
            "pill": True,
            "seal": True,
            "rings": True,
            "particles": True,
            "mist": True,
            "shine": True,
        },
    },
}
ELEMENT_FIELDS = {
    "rail": [],
    "offer": (("kicker", 24), ("hero", 16), ("title", 48), ("detail", 80)),
    "fresh": (
        ("kicker", 24),
        ("hero", 24),
        ("seal", 16),
        ("sealDetail", 28),
        ("title", 48),
        ("detail", 80),
    ),
}

lock = threading.Lock()
content = json.loads(json.dumps(DEFAULT))
screens = []
playlist = {}
elements = {}
catalog = {"products": []}
creations = {"items": []}


def clip(value, limit=60):
    text = "" if value is None else str(value)
    return text.strip()[:limit]


def pesos(value):
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(round(value)))
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return int(digits) if digits else 0


def image_path(value, fallback):
    text = "" if value is None else str(value).strip()
    if text.startswith("/img/") and ".." not in text and len(text) < 180:
        return text
    if text.startswith("https://") and len(text) < 400:
        return text
    return fallback


def sanitize(data):
    if not isinstance(data, dict) or not isinstance(data.get("products"), list):
        data = json.loads(json.dumps(DEFAULT))
    incoming = data.get("products") if isinstance(data, dict) else None
    if not isinstance(incoming, list):
        incoming = []
    count = data.get("count") if isinstance(data, dict) else None
    if isinstance(count, bool) or not isinstance(count, int):
        count = len(incoming) if incoming else 3
    count = min(4, max(2, count))
    defaults = DEFAULT["products"]
    products = []
    for index in range(count):
        item = incoming[index] if index < len(incoming) and isinstance(incoming[index], dict) else {}
        product_id = clip(item.get("productId", ""), 24)
        name = clip(item.get("name", ""), 42)
        fallback = defaults[index]["image"] if (product_id or name) and index < len(defaults) else ""
        image = image_path(item.get("image"), fallback) if item.get("image") or fallback else ""
        products.append({
            "brand": clip(item.get("brand", ""), 32),
            "name": name,
            "detail": clip(item.get("detail", ""), 32),
            "price": pesos(item.get("price", 0)),
            "listPrice": pesos(item.get("listPrice", 0)),
            "badge": clip(item.get("badge", ""), 16),
            "image": image,
            "productId": product_id,
        })
    return {"count": count, "products": products}


def sanitize_screens(data):
    incoming = {}
    items = data.get("screens") if isinstance(data, dict) else None
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and item.get("id") is not None:
                incoming[str(item.get("id"))] = item
    out = []
    for base in SCREEN_BASE:
        item = incoming.get(base["id"], {})
        out.append({
            "id": base["id"],
            "online": base["online"],
            "name": clip(item.get("name", ""), 40),
            "description": clip(item.get("description", ""), 90),
        })
    return {"screens": out}


def read_local(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def mirror(path, data):
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    try:
        if path.is_file() and path.read_text(encoding="utf-8") == text:
            return
        path.write_text(text, encoding="utf-8")
    except OSError:
        return


def publish(doc_id, data):
    try:
        firestore_io.push(doc_id, data)
    except Exception as exc:
        print(f"Firestore no guardó {doc_id}: {exc}", flush=True)


def hydrate(doc_id, path, sanitize):
    local = read_local(path)
    remote = None
    try:
        remote = firestore_io.pull(doc_id)
    except Exception as exc:
        print(f"Firestore no disponible ({doc_id}): {exc}", flush=True)
    data = sanitize(remote if remote is not None else local)
    mirror(path, data)
    if remote is None:
        publish(doc_id, data)
    return data


def load_screens():
    global screens
    screens = hydrate("screens", SCREENS_PATH, sanitize_screens)["screens"]


def save_screens(data):
    global screens
    screens = data["screens"]
    stored = {"screens": screens}
    mirror(SCREENS_PATH, stored)
    publish("screens", stored)


def flag(value):
    return True if value is None else bool(value) if isinstance(value, bool) else True


def seconds(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 5
    if number < 1:
        return 1
    if number > 600:
        return 600
    return number


def sanitize_entries(items):
    seen = set()
    elements = []
    if not isinstance(items, list):
        return elements
    for item in items:
        if not isinstance(item, dict) or item.get("id") is None:
            continue
        element_id = str(item.get("id"))
        if element_id in seen:
            continue
        if element_id not in ELEMENT_IDS and not CUSTOM_ID.match(element_id):
            continue
        seen.add(element_id)
        elements.append({
            "id": element_id,
            "visible": flag(item.get("visible")),
            "seconds": seconds(item.get("seconds")),
        })
    return elements


def fresh_list_id(used):
    stamp = int(time.time() * 1000)
    while "l" + str(stamp) in used:
        stamp += 1
    return "l" + str(stamp)


def sanitize_one_list(item, fallback_name):
    raw_id = str(item.get("id") or "")
    name = clip(item.get("name"), 40) or fallback_name
    return {
        "id": raw_id if LIST_ID.match(raw_id) else "",
        "name": name,
        "playing": item.get("playing") is True,
        "elements": sanitize_entries(item.get("elements")),
    }


def ensure_playing(lists):
    if not any(item["playing"] for item in lists):
        lists[0]["playing"] = True
        return
    seen = False
    for item in lists:
        if item["playing"] and not seen:
            seen = True
            continue
        item["playing"] = False


def sanitize_playlist(data, previous=None):
    data = data if isinstance(data, dict) else {}
    previous = previous if isinstance(previous, dict) else {}
    raw_lists = data.get("lists") if isinstance(data.get("lists"), list) else None
    lists = []
    used = set()
    if raw_lists is None:
        prev = previous.get("lists") if isinstance(previous.get("lists"), list) else []
        for raw in prev:
            if not isinstance(raw, dict):
                continue
            item = sanitize_one_list(raw, "Lista")
            if not item["id"] or item["id"] in used:
                item["id"] = fresh_list_id(used)
            used.add(item["id"])
            lists.append(item)
        if not lists:
            elements = sanitize_entries(data.get("elements"))
            if not elements:
                elements = sanitize_entries(previous.get("elements"))
            lists = [{
                "id": "l1",
                "name": "Góndola 3",
                "playing": True,
                "elements": elements,
            }]
    else:
        for index, raw in enumerate(raw_lists[:12]):
            if not isinstance(raw, dict):
                continue
            item = sanitize_one_list(raw, "Lista " + str(index + 1))
            if not item["id"] or item["id"] in used:
                item["id"] = fresh_list_id(used)
            used.add(item["id"])
            lists.append(item)
        if not lists:
            lists = [{
                "id": "l1",
                "name": "Lista",
                "playing": True,
                "elements": sanitize_entries(data.get("elements")),
            }]
    ensure_playing(lists)
    if isinstance(data.get("elements"), list):
        posted = sanitize_entries(data.get("elements"))
        for item in lists:
            if item["playing"]:
                item["elements"] = posted
    playing = next(item for item in lists if item["playing"])
    visible = data.get("visible") if "visible" in data else previous.get("visible", True)
    return {
        "visible": flag(visible),
        "elements": playing["elements"],
        "lists": lists,
    }


def color_hex(value, default):
    text = "" if value is None else str(value).strip().lower()
    if re.fullmatch(r"#[0-9a-f]{6}", text):
        return text
    return default


def percent_point(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 50
    if number < 0:
        return 0
    if number > 100:
        return 100
    return round(number, 2)


def gradient_angle(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 90
    if number < 0:
        return 0
    if number > 360:
        return 360
    return number


def fx_kind(value):
    text = str(value or "").strip().lower()
    if text in ("dust", "spark", "snow", "bokeh", "streak"):
        return text
    return ""


def sanitize_gradient(value):
    if not isinstance(value, dict) or value.get("on") is not True:
        return None
    return {
        "on": True,
        "from": color_hex(value.get("from"), "#0e1014"),
        "to": color_hex(value.get("to"), "#f47b20"),
        "angle": gradient_angle(value.get("angle")),
    }


def piece_size(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 38
    if number < 18:
        return 18
    if number > 78:
        return 78
    return number


def sanitize_piece(item):
    if not isinstance(item, dict):
        return None
    text = clip(item.get("text"), 80)
    if not text:
        return None
    font = item.get("font")
    if font not in CREATION_FONTS:
        font = "Poppins, sans-serif"
    return {
        "text": text,
        "x": percent_point(item.get("x")),
        "y": percent_point(item.get("y")),
        "font": font,
        "size": piece_size(item.get("size")),
        "bold": item.get("bold") is True,
        "color": color_hex(item.get("color"), "#ffffff"),
    }


def product_count(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return 3
    if value < 2:
        return 2
    if value > 4:
        return 4
    return value


def product_on_file(product_id):
    for item in catalog.get("products", []):
        if item.get("id") == product_id and product_on_branch(item):
            return item
    return None


def fill_product_ids(preferred, count):
    ids = []

    def add(product_id):
        if len(ids) >= count:
            return
        item_id = clip(product_id, 24)
        if item_id and item_id not in ids and product_on_file(item_id):
            ids.append(item_id)

    if isinstance(preferred, list):
        for raw in preferred:
            add(raw)
    for item in content.get("products", []):
        if isinstance(item, dict):
            add(item.get("productId"))
    for item in catalog.get("products", []):
        add(item.get("id"))
    return ids


def resolved_card(linked):
    price = pesos(branch_value(linked, "prices", "price"))
    discount = percent(branch_value(linked, "discounts", "discount"))
    on_offer = branch_value(linked, "offers", "onOffer") is True
    brand, name = split_label(linked["name"])
    return {
        "productId": linked["id"],
        "brand": brand,
        "name": name,
        "detail": linked["description"],
        "price": price,
        "listPrice": list_price(price, discount, on_offer),
        "badge": "Oferta" if on_offer else "",
        "image": linked["image"] or "",
    }


def present_creations(data):
    items = []
    for item in data.get("items", []):
        copy = dict(item)
        if copy.get("template") == "products":
            copy["products"] = [
                resolved_card(product_on_file(product_id))
                for product_id in copy.get("productIds", [])
                if product_on_file(product_id)
            ]
        items.append(copy)
    return {"items": items}


def sanitize_creations(data):
    data = data if isinstance(data, dict) else {}
    items = data.get("items") if isinstance(data.get("items"), list) else []
    out = []
    seen = set()
    for item in items[:40]:
        if not isinstance(item, dict):
            continue
        creation_id = str(item.get("id") or "")
        if not CUSTOM_ID.match(creation_id) or creation_id in seen:
            continue
        template = item.get("template") if item.get("template") in ("products", "feature") else "text"
        feature = sanitize_feature(item.get("feature")) if template == "feature" else None
        raw_pieces = item.get("pieces") if isinstance(item.get("pieces"), list) else []
        pieces = []
        if template == "feature":
            if not feature:
                continue
        elif template == "text":
            for raw in raw_pieces[:12]:
                piece = sanitize_piece(raw)
                if piece:
                    pieces.append(piece)
            if not pieces:
                continue
        count = product_count(item.get("count"))
        product_ids = fill_product_ids(item.get("productIds"), count) if template == "products" else []
        if template == "products":
            if len(product_ids) < 2:
                continue
            count = min(count, len(product_ids))
            product_ids = product_ids[:count]
        seen.add(creation_id)
        stored = {
            "id": creation_id,
            "name": clip(item.get("name"), 40) or (pieces[0]["text"] if pieces else "Productos"),
            "template": template,
            "background": color_hex(item.get("background"), ""),
            "gradient": sanitize_gradient(item.get("gradient")),
            "particles": item.get("particles") is True,
            "wash": item.get("wash") is True,
            "fx": fx_kind(item.get("fx")) if item.get("particles") is True else "",
            "pieces": pieces if template == "text" else [],
        }
        if template == "products":
            stored["count"] = count
            stored["productIds"] = product_ids
            if not clip(item.get("name"), 40):
                stored["name"] = "Productos · " + str(count)
        if template == "feature":
            stored["feature"] = feature
            stored["name"] = clip(feature["name"], 40)
        out.append(stored)
    return {"items": out}


def load_creations():
    global creations
    creations = hydrate("creations", CREATIONS_PATH, sanitize_creations)


def save_creations(data):
    global creations
    creations = data
    mirror(CREATIONS_PATH, creations)
    publish("creations", creations)
    publish_live()


def load_playlist():
    global playlist
    playlist = hydrate("playlist", PLAYLIST_PATH, sanitize_playlist)


def save_playlist(data):
    global playlist
    playlist = data
    mirror(PLAYLIST_PATH, playlist)
    publish("playlist", playlist)
    publish_live()


def text_field(item, key, default, limit):
    if not isinstance(item, dict) or key not in item or item.get(key) is None:
        return default
    return clip(item.get(key), limit)


def shape_flags(item, defaults):
    incoming = item.get("shapes") if isinstance(item, dict) and isinstance(item.get("shapes"), dict) else {}
    return {key: flag(incoming.get(key)) for key in defaults}


def sanitize_elements(data):
    data = data if isinstance(data, dict) else {}
    out = {}
    for element_id, base in ELEMENT_DEFAULT.items():
        item = data.get(element_id) if isinstance(data.get(element_id), dict) else {}
        piece = {"shapes": shape_flags(item, base["shapes"])}
        for key, limit in ELEMENT_FIELDS[element_id]:
            piece[key] = text_field(item, key, base[key], limit)
        out[element_id] = piece
    return out


def load_elements():
    global elements
    elements = hydrate("elements", ELEMENTS_PATH, sanitize_elements)


def percent(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    if number < 0:
        return 0
    if number > 90:
        return 90
    return number


def sanitize_scope(value):
    if not isinstance(value, list):
        return "all"
    chosen = []
    for branch in BRANCHES:
        if branch["id"] in value or branch["id"] in {clip(item, 24) for item in value}:
            chosen.append(branch["id"])
    return chosen or "all"


def sanitize_prices(value, chosen, shared):
    incoming = value if isinstance(value, dict) else {}
    prices = {}
    for branch_id in chosen:
        if branch_id in incoming:
            prices[branch_id] = pesos(incoming[branch_id])
        else:
            prices[branch_id] = shared
    return prices


def sanitize_offers(value, chosen, shared):
    incoming = value if isinstance(value, dict) else {}
    offers = {}
    for branch_id in chosen:
        if branch_id in incoming:
            offers[branch_id] = incoming[branch_id] is True
        else:
            offers[branch_id] = shared
    return offers


def sanitize_discounts(value, chosen, shared):
    incoming = value if isinstance(value, dict) else {}
    discounts = {}
    for branch_id in chosen:
        if branch_id in incoming:
            discounts[branch_id] = percent(incoming[branch_id])
        else:
            discounts[branch_id] = shared
    return discounts


def sanitize_catalog(data):
    items = data.get("products") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return {"products": []}
    products = []
    for index, item in enumerate(items[:80]):
        if not isinstance(item, dict):
            continue
        name = clip(item.get("name"), 72)
        if not name:
            continue
        item_id = clip(item.get("id"), 24) or str(index + 1)
        shared_offer = item.get("onOffer") is True
        shared_discount = percent(item.get("discount"))
        shared = pesos(item.get("price"))
        scope = sanitize_scope(item.get("branches"))
        if scope == "all":
            offers, discounts, prices = {}, {}, {}
        else:
            offers = sanitize_offers(item.get("offers"), scope, shared_offer)
            discounts = sanitize_discounts(item.get("discounts"), scope, shared_discount)
            prices = sanitize_prices(item.get("prices"), scope, shared)
        products.append({
            "id": item_id,
            "name": name,
            "description": clip(item.get("description"), 80),
            "onOffer": shared_offer,
            "discount": shared_discount,
            "price": shared,
            "branches": scope,
            "offers": offers,
            "discounts": discounts,
            "prices": prices,
            "image": image_path(item.get("image"), ""),
        })
    return {"products": products}


def split_label(name):
    for brand in BRANDS:
        if name.endswith(brand) and len(name) > len(brand) + 1:
            head = name[: -len(brand)].strip(" -–")
            if head:
                return brand, head
        prefix = brand + " "
        if name.startswith(prefix):
            head = name[len(prefix) :].strip()
            if head:
                return brand, head
    return "", name


def branch_value(product, mapped, shared):
    if product.get("branches") == "all":
        return product.get(shared)
    values = product.get(mapped)
    if isinstance(values, dict) and SCREEN_BRANCH in values:
        return values[SCREEN_BRANCH]
    return product.get(shared)


def list_price(price, discount, on_offer):
    if not on_offer or discount <= 0 or discount >= 100 or price <= 0:
        return 0
    return int(round(price * 100 / (100 - discount)))


def product_on_branch(product):
    scope = product.get("branches")
    if scope == "all":
        return True
    return isinstance(scope, list) and SCREEN_BRANCH in scope


def resolve_content(stored):
    by_id = {item["id"]: item for item in catalog.get("products", [])}
    products = []
    for item in stored["products"]:
        linked = by_id.get(item.get("productId") or "")
        if not linked or not product_on_branch(linked):
            products.append(item)
            continue
        price = pesos(branch_value(linked, "prices", "price"))
        discount = percent(branch_value(linked, "discounts", "discount"))
        on_offer = branch_value(linked, "offers", "onOffer") is True
        brand, name = split_label(linked["name"])
        products.append({
            "productId": linked["id"],
            "brand": brand,
            "name": name,
            "detail": linked["description"],
            "price": price,
            "listPrice": list_price(price, discount, on_offer),
            "badge": "Oferta" if on_offer else "",
            "image": linked["image"] or item.get("image") or "",
        })
    return {"count": len(products), "products": products}


def load_catalog():
    global catalog
    catalog = hydrate("catalog", CATALOG_PATH, sanitize_catalog)


def save_catalog(data):
    global catalog
    catalog = data
    mirror(CATALOG_PATH, catalog)
    publish("catalog", catalog)
    publish_live()


def save_elements(data):
    global elements
    elements = data
    mirror(ELEMENTS_PATH, elements)
    publish("elements", elements)
    publish_live()


def load():
    global content
    content = hydrate("content", CONTENT_PATH, sanitize)


def save(data):
    global content
    content = data
    mirror(CONTENT_PATH, content)
    publish("content", content)
    publish_live()


def publish_live():
    publish("live", {
        "updatedAt": int(time.time() * 1000),
        "content": resolve_content(content),
        "elements": elements,
        "playlist": playlist,
        "creations": present_creations(creations),
    })


CORDIEZ_HOSTS = {"www.cordiez.com.ar", "cordiez.com.ar"}
IMAGE_HOST = "arcordiezb2c.vtexassets.com"


def hex_color(pixel):
    return "#{:02x}{:02x}{:02x}".format(pixel[0], pixel[1], pixel[2])


def color_luma(pixel):
    return 0.2126 * pixel[0] + 0.7152 * pixel[1] + 0.0722 * pixel[2]


def color_span(pixel):
    return max(pixel) - min(pixel)


def clear_backdrop(image):
    rgba = image.convert("RGBA")
    width, height = rgba.size
    pixels = rgba.load()

    def is_backdrop(x, y):
        red, green, blue, alpha = pixels[x, y]
        if not alpha:
            return False
        if min(red, green, blue) < 236:
            return False
        return max(red, green, blue) - min(red, green, blue) < 18

    seen = bytearray(width * height)
    stack = [(x, 0) for x in range(width)]
    stack.extend((x, height - 1) for x in range(width))
    stack.extend((0, y) for y in range(height))
    stack.extend((width - 1, y) for y in range(height))
    while stack:
        x, y = stack.pop()
        if x < 0 or y < 0 or x >= width or y >= height:
            continue
        index = y * width + x
        if seen[index]:
            continue
        seen[index] = 1
        if not is_backdrop(x, y):
            continue
        pixels[x, y] = (0, 0, 0, 0)
        stack.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
    for y in range(height):
        for x in range(width):
            red, green, blue, alpha = pixels[x, y]
            if not alpha or min(red, green, blue) < 200:
                continue
            touches = False
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if nx < 0 or ny < 0 or nx >= width or ny >= height or pixels[nx, ny][3] == 0:
                    touches = True
                    break
            if not touches:
                continue
            fade = max(0, min(255, (255 - min(red, green, blue)) * 5))
            pixels[x, y] = (red, green, blue, fade)
    return rgba


def sample_colors(raw):
    image = Image.open(io.BytesIO(raw)).convert("RGB")
    image = image.resize((48, 48))
    pixels = [pixel for pixel in image.getdata() if not (pixel[0] > 236 and pixel[1] > 236 and pixel[2] > 236)]
    if len(pixels) < 12:
        pixels = list(image.getdata())
    darks = [pixel for pixel in pixels if color_luma(pixel) < 78]
    vivid = [pixel for pixel in pixels if color_span(pixel) > 34 and 36 < color_luma(pixel) < 220]
    if vivid:
        accent = max(vivid, key=lambda pixel: (color_span(pixel), color_luma(pixel)))
    else:
        accent = (232, 176, 112)
    if color_span(accent) < 22:
        accent = (232, 176, 112)
    if darks:
        darks.sort(key=color_luma)
        chosen = darks[:max(8, len(darks) // 3)]
        bg = tuple(sum(pixel[channel] for pixel in chosen) // len(chosen) for channel in range(3))
    else:
        bg = (12, 11, 14)
    bg = tuple(max(8, int(channel * 0.4)) for channel in bg)
    if sum(bg) > 150:
        bg = (12, 11, 14)
    glow = tuple(min(255, max(28, int(accent[channel] * 0.5 + 24))) for channel in range(3))
    edge = tuple(min(255, int(bg[channel] * 0.78 + accent[channel] * 0.22)) for channel in range(3))
    return {
        "bg": hex_color(bg),
        "edge": hex_color(edge),
        "ink": "#fff8f2",
        "accent": hex_color(accent),
        "glow": hex_color(glow),
    }


def read_limited(response, limit):
    chunks = []
    total = 0
    while total < limit:
        block = response.read(min(65536, limit - total))
        if not block:
            break
        chunks.append(block)
        total += len(block)
    return b"".join(chunks)


def http_get(url, limit, hosts):
    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; MixxoBar/1.0)",
        "Accept": "*/*",
        "Accept-Encoding": "identity",
    })
    with urllib.request.urlopen(request, timeout=12) as response:
        final = urlparse(response.geturl())
        if final.scheme != "https" or final.hostname not in hosts:
            raise ValueError("Pegá un link de un producto de cordiez.com.ar")
        raw = read_limited(response, limit)
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return raw


def state_node(state, node):
    if isinstance(node, dict) and node.get("id") in state:
        return state[node["id"]]
    return node if isinstance(node, dict) else {}


def product_price(state, key):
    selling = state.get("$" + key + ".priceRange.sellingPrice")
    listing = state.get("$" + key + ".priceRange.listPrice")
    price = 0
    list_price = 0
    if isinstance(selling, dict):
        price = selling.get("highPrice") or selling.get("lowPrice") or 0
    if isinstance(listing, dict):
        list_price = listing.get("highPrice") or listing.get("lowPrice") or 0
    if price:
        return price, list_price or price
    for state_key, value in state.items():
        if not state_key.endswith(".commertialOffer") or not isinstance(value, dict):
            continue
        if key not in state_key:
            continue
        offer = value.get("Price") or 0
        if offer:
            return offer, value.get("ListPrice") or offer
    return 0, 0


def fetch_cordiez(url):
    text = "" if url is None else str(url).strip()
    parsed = urlparse(text)
    parts = [part for part in parsed.path.split("/") if part]
    slug = parts[-2] if len(parts) >= 2 and parts[-1] == "p" else ""
    if parsed.scheme != "https" or parsed.hostname not in CORDIEZ_HOSTS or not re.fullmatch(r"[a-z0-9-]{3,120}", slug):
        raise ValueError("Pegá un link de un producto de cordiez.com.ar")
    page = "https://www.cordiez.com.ar/" + slug + "/p"
    raw = http_get(page, 2_000_000, CORDIEZ_HOSTS)
    html = raw.decode("utf-8", "replace")
    marker = "__STATE__ = "
    start = html.find(marker)
    end = html.find("</script>", start + len(marker)) if start >= 0 else -1
    if start < 0 or end < 0:
        raise ValueError("No encontré ese producto")
    try:
        state = json.loads(html[start + len(marker):end].strip().rstrip(";"))
    except json.JSONDecodeError as exc:
        raise ValueError("No encontré ese producto") from exc
    key = "Product:" + slug
    product = state.get(key)
    if not isinstance(product, dict) or not product.get("productName"):
        raise ValueError("No encontré ese producto")
    items = product.get("items") if isinstance(product.get("items"), list) else []
    sku = state_node(state, items[0]) if items else {}
    images = sku.get("images") if isinstance(sku.get("images"), list) else []
    image = state_node(state, images[0]) if images else {}
    image_url = str(image.get("imageUrl") or "")
    image_host = urlparse(image_url).hostname
    if image_host != IMAGE_HOST:
        raise ValueError("Ese producto no tiene foto")
    sized = re.sub(r"/ids/(\d+)/", r"/ids/\1-800-800/", image_url, count=1)
    photo = http_get(sized, 4_000_000, {IMAGE_HOST})
    colors = sample_colors(photo)
    saved = clear_backdrop(Image.open(io.BytesIO(photo)))
    saved.thumbnail((900, 900))
    product_id = re.sub(r"[^0-9]", "", str(product.get("productId") or "")) or slug[:40]
    filename = "feat-" + product_id + ".png"
    folder = PUBLIC / "img"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        saved.save(folder / filename, "PNG")
    except OSError:
        folder = Path("/tmp/mixxo-img")
        folder.mkdir(parents=True, exist_ok=True)
        saved.save(folder / filename, "PNG")
    detail = product.get("metaTagDescription") or product.get("description") or ""
    detail = re.sub(r"\s+", " ", str(detail)).strip()
    if len(detail) > 88:
        detail = detail[:88].rsplit(" ", 1)[0]
    price, list_price = product_price(state, key)
    return {
        "url": page,
        "brand": str(product.get("brand") or "").strip(),
        "name": str(product.get("productName") or "").strip(),
        "detail": detail,
        "price": max(0, int(round(float(price or 0)))),
        "listPrice": max(0, int(round(float(list_price or 0)))),
        "image": "/img/" + filename,
        "bg": colors["bg"],
        "edge": colors["edge"],
        "ink": colors["ink"],
        "accent": colors["accent"],
        "glow": colors["glow"],
    }


def feature_image(value):
    text = image_path(value, "")
    if re.fullmatch(r"/img/feat-[a-z0-9-]{1,48}\.(?:jpg|png)", text):
        return text
    if text.startswith("https://" + IMAGE_HOST + "/") and len(text) < 400:
        return text
    return ""


def sanitize_feature(value):
    if not isinstance(value, dict):
        return None
    name = clip(value.get("name"), 160)
    image = feature_image(value.get("image"))
    if not name or not image:
        return None
    return {
        "url": clip(value.get("url"), 240),
        "brand": clip(value.get("brand"), 32),
        "name": name,
        "detail": clip(value.get("detail"), 90),
        "price": pesos(value.get("price")),
        "listPrice": pesos(value.get("listPrice")),
        "image": image,
        "bg": color_hex(value.get("bg"), "#0c0c0e"),
        "edge": color_hex(value.get("edge"), "#1a140f"),
        "ink": color_hex(value.get("ink"), "#fff8f2"),
        "accent": color_hex(value.get("accent"), "#f47b20"),
        "glow": color_hex(value.get("glow"), "#c47a3a"),
    }


def lan_ip():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.168.0.243", 9))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def path_only(self):
        return self.path.split("?", 1)[0]

    def send_bytes(self, code, body, content_type):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def restore_route(self):
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        route = query.pop("__route", [""])[0]
        if not route:
            return
        if not route.startswith("/"):
            route = "/" + route
        rest = urlencode(query, doseq=True)
        self.path = route + (("?" + rest) if rest else "")

    def do_GET(self):
        self.restore_route()
        path = self.path_only()
        if path == "/":
            self.send_file(PUBLIC / "display.html", "text/html; charset=utf-8")
        elif path == "/home":
            self.send_file(PUBLIC / "home.html", "text/html; charset=utf-8")
        elif path == "/pantalla/123":
            self.send_file(PUBLIC / "pantalla.html", "text/html; charset=utf-8")
        elif path == "/editor":
            self.send_file(PUBLIC / "editor.html", "text/html; charset=utf-8")
        elif path.startswith("/editor/"):
            element_id = path[len("/editor/"):]
            if element_id not in ELEMENT_IDS:
                self.send_bytes(404, "No encontrado", "text/plain; charset=utf-8")
                return
            self.send_file(PUBLIC / "piece.html", "text/html; charset=utf-8")
        elif path == "/api/elements":
            with lock:
                body = json.dumps(elements, ensure_ascii=False)
            self.send_bytes(200, body, "application/json; charset=utf-8")
        elif path == "/api/catalog":
            with lock:
                payload = {"branches": BRANCHES, "products": catalog["products"]}
                body = json.dumps(payload, ensure_ascii=False)
            self.send_bytes(200, body, "application/json; charset=utf-8")
        elif path == "/api/content":
            with lock:
                body = json.dumps(resolve_content(content), ensure_ascii=False)
            self.send_bytes(200, body, "application/json; charset=utf-8")
        elif path == "/api/screens":
            with lock:
                body = json.dumps({"screens": screens}, ensure_ascii=False)
            self.send_bytes(200, body, "application/json; charset=utf-8")
        elif path == "/api/playlist":
            with lock:
                body = json.dumps(playlist, ensure_ascii=False)
            self.send_bytes(200, body, "application/json; charset=utf-8")
        elif path == "/api/creations":
            with lock:
                body = json.dumps(present_creations(creations), ensure_ascii=False)
            self.send_bytes(200, body, "application/json; charset=utf-8")
        elif path == "/favicon.svg":
            self.send_file(PUBLIC / "favicon.svg", "image/svg+xml")
        elif path.startswith("/img/"):
            self.send_image(path)
        else:
            self.send_bytes(404, "No encontrado", "text/plain; charset=utf-8")

    def send_image(self, path):
        name = path[len("/img/"):]
        if not name or "/" in name or ".." in name or not all(ch.isalnum() or ch in "._-" for ch in name):
            self.send_bytes(404, "No encontrado", "text/plain; charset=utf-8")
            return
        kind = "image/png" if name.endswith(".png") else "image/jpeg"
        for folder in (PUBLIC / "img", Path("/tmp/mixxo-img")):
            file_path = folder / name
            if file_path.is_file():
                self.send_file(file_path, kind)
                return
        self.send_bytes(404, "No encontrado", "text/plain; charset=utf-8")

    def send_file(self, file_path, content_type):
        if not file_path.is_file():
            self.send_bytes(404, "No encontrado", "text/plain; charset=utf-8")
            return
        self.send_bytes(200, file_path.read_bytes(), content_type)

    def do_POST(self):
        self.restore_route()
        path = self.path_only()
        if path not in ("/api/content", "/api/screens", "/api/playlist", "/api/elements", "/api/catalog", "/api/creations", "/api/cordiez"):
            self.send_bytes(404, "No encontrado", "text/plain; charset=utf-8")
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0 or length > 32768:
            self.send_bytes(400, '{"error":"cuerpo inválido"}', "application/json; charset=utf-8")
            return
        if path == "/api/cordiez":
            try:
                raw = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("Pegá un link de un producto de cordiez.com.ar")
                data = fetch_cordiez(raw.get("url"))
            except urllib.error.URLError:
                self.send_bytes(502, '{"error":"No pude entrar al sitio"}', "application/json; charset=utf-8")
                return
            except ValueError as exc:
                text = str(exc)
                known = text.startswith("Pegá") or text.startswith("No ") or text.startswith("Ese ")
                message = text if known else "No pude leer ese producto"
                self.send_bytes(400, json.dumps({"error": message}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            except (UnicodeError, OSError):
                self.send_bytes(400, '{"error":"No pude leer ese producto"}', "application/json; charset=utf-8")
                return
            self.send_bytes(200, json.dumps(data, ensure_ascii=False), "application/json; charset=utf-8")
            return
        try:
            raw = json.loads(self.rfile.read(length).decode("utf-8"))
            if path == "/api/content":
                data = sanitize(raw)
            elif path == "/api/screens":
                data = sanitize_screens(raw)
            elif path == "/api/playlist":
                data = sanitize_playlist(raw, playlist)
            elif path == "/api/elements":
                data = sanitize_elements(raw)
            elif path == "/api/creations":
                data = sanitize_creations(raw)
            else:
                data = sanitize_catalog(raw)
        except (UnicodeError, json.JSONDecodeError, ValueError):
            self.send_bytes(400, '{"error":"contenido inválido"}', "application/json; charset=utf-8")
            return
        with lock:
            if path == "/api/content":
                save(data)
                body = json.dumps(content, ensure_ascii=False)
            elif path == "/api/screens":
                save_screens(data)
                body = json.dumps({"screens": screens}, ensure_ascii=False)
            elif path == "/api/playlist":
                save_playlist(data)
                body = json.dumps(playlist, ensure_ascii=False)
            elif path == "/api/elements":
                save_elements(data)
                body = json.dumps(elements, ensure_ascii=False)
            elif path == "/api/creations":
                save_creations(data)
                body = json.dumps(creations, ensure_ascii=False)
            else:
                save_catalog(data)
                body = json.dumps(catalog, ensure_ascii=False)
        self.send_bytes(200, body, "application/json; charset=utf-8")


def boot():
    load()
    load_screens()
    load_playlist()
    load_elements()
    load_catalog()
    load_creations()


if os.environ.get("VERCEL"):
    boot()


def main():
    boot()
    publish_live()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    ip = lan_ip()
    print(f"Editor:   http://{ip}:{PORT}/editor", flush=True)
    print(f"Pantalla: http://{ip}:{PORT}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
