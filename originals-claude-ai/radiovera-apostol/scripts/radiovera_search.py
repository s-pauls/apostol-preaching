#!/usr/bin/env python3
"""Поиск пояснений к апостольскому чтению на radiovera.ru по зачалу.

Использование:
    python radiovera_search.py "[Гал. 5:22–6:2](https://azbyka.ru/...), зач. 213: преподобного Сергия ..."
    python radiovera_search.py "2 Кор. 9:6-11, зач. 188" --out /mnt/user-data/outputs
    python radiovera_search.py --query "2 Кор., 188 зач., IX, 6-11"   # готовая строка поиска
    python radiovera_search.py "Гал. 5:22–6:2, зач. 213" --dry-run     # только показать строку/URL

Алгоритм:
 1. Разбор входной строки -> строка поиска вида «Гал., 213 зач., V, 22 — VI, 2».
 2. GET https://radiovera.ru/?s=<строка>&sorting=relevance
 3. Берутся только карточки, у которых текст <p class="excerpt"> НАЧИНАЕТСЯ с точной строки поиска.
 4. Из этих карточек забираются ссылки a.link-post, посты открываются,
    из div.single-content берётся всё до <hr class="wp-block-separator ...">.
 5. Результат пишется в один MD-файл: «# Вариант N» + текст поста.
"""
import argparse
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

BASE = "https://radiovera.ru/"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; radiovera-apostol-skill/1.0)"}
DASHES = "–—−‑-"
EM = "\u2014"

ROMAN = [(10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]


def to_roman(n: int) -> str:
    out = ""
    # поддержка глав до 399 (в посланиях глав меньше 30, но на всякий случай)
    for val, sym in [(100, "C"), (90, "XC"), (50, "L"), (40, "XL")] + ROMAN:
        while n >= val:
            out += sym
            n -= val
    return out


def clean_input(text: str) -> str:
    """Убрать markdown-ссылки [текст](url) -> текст, nbsp -> пробел."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = text.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip()


def build_query(raw: str) -> str:
    """Входная строка -> строка поиска в формате radiovera."""
    s = clean_input(raw)
    zach = re.search(r"зач(?:ало|\.)?\s*(\d+)", s, re.IGNORECASE)
    if not zach:
        raise ValueError("Не найден номер зачала (ожидается «зач. 213»)")
    book = re.search(r"((?:\d\s*)?[А-ЯЁ][а-яё]+)\.?\s*(\d+)\s*:\s*(\d+)", s)
    if not book:
        raise ValueError("Не найдены книга и глава:стих (ожидается «Гал. 5:22–6:2»)")
    name = re.sub(r"\s+", " ", book.group(1)).strip()
    # «2Кор» -> «2 Кор»
    name = re.sub(r"^(\d)\s*", r"\1 ", name)
    c1, v1 = int(book.group(2)), int(book.group(3))
    tail = s[book.end():]
    rng = re.match(rf"\s*[{DASHES}]\s*(?:(\d+)\s*:\s*)?(\d+)", tail)
    if rng:
        c2 = int(rng.group(1)) if rng.group(1) else c1
        v2 = int(rng.group(2))
    else:
        c2, v2 = c1, v1

    if c2 != c1:
        verses = f"{to_roman(c1)}, {v1} {EM} {to_roman(c2)}, {v2}"
    elif v2 != v1:
        verses = f"{to_roman(c1)}, {v1}-{v2}"
    else:
        verses = f"{to_roman(c1)}, {v1}"
    return f"{name}., {zach.group(1)} зач., {verses}"


def norm(text: str) -> str:
    """Нормализация для сравнения: nbsp -> пробел, любые тире -> «-», пробелы вокруг тире убраны."""
    t = text.replace("\u00a0", " ")
    t = re.sub(f"[{DASHES}]", "-", t)
    t = re.sub(r"\s*-\s*", "-", t)
    return re.sub(r"\s+", " ", t).strip()


def starts_with_exact(excerpt: str, query: str) -> bool:
    """StartsWith + граница, чтобы «IX, 6-11» не совпало с «IX, 6-117» или «IX, 6-11а».
    Короткое и длинное тире (и пробелы вокруг них) считаются одинаковыми."""
    e, q = norm(excerpt), norm(query)
    if not e.startswith(q):
        return False
    rest = e[len(q):len(q) + 1]
    return rest == "" or not (rest.isalnum() or rest == "-")


def query_variants(query: str):
    """Варианты написания строки для самого поиска: как есть и с заменой тире на альтернативное."""
    variants = [query]
    m = re.search(r"(\d+)-(\d+)$", query)
    if EM in query:
        variants.append(query.replace(f" {EM} ", "-"))
        variants.append(query.replace(f" {EM} ", EM))
    elif m:
        variants.append(query[:m.start()] + f"{m.group(1)}{EM}{m.group(2)}")
    return list(dict.fromkeys(variants))


def search_url(query: str, page: int = 1) -> str:
    q = quote(query, safe="")
    if page == 1:
        return f"{BASE}?s={q}&sorting=relevance"
    return f"{BASE}page/{page}/?s={q}&sorting=relevance"


def get(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    r.encoding = r.apparent_encoding if not r.encoding or r.encoding.lower() == "iso-8859-1" else r.encoding
    return r.text


def parse_exact_links(html: str, query: str):
    """Вернуть (ссылки точных совпадений, сколько карточек всего на странице)."""
    soup = BeautifulSoup(html, "html.parser")
    links, total = [], 0
    for card in soup.select("div.card-content"):
        ex = card.select_one("p.excerpt")
        if not ex:
            continue
        total += 1
        if not starts_with_exact(ex.get_text(), query):
            continue
        a = card.select_one("a.link-post")
        if a and a.get("href"):
            links.append(urljoin(BASE, a["href"].strip()))
    return links, total


# ---------- HTML -> Markdown ----------

VERSE_FMT = "**{n}**"  # как выводить номер стиха; например "<sup>{n}</sup>" для надстрочного
RED = re.compile(r"#ff0000|rgb\(\s*255\s*,\s*0\s*,\s*0\s*\)|\bred\b", re.IGNORECASE)


def is_verse_marker(node) -> bool:
    """<span style="color: #ff0000;"><sup>N&nbsp;</sup></span> - номер стиха."""
    return (
        isinstance(node, Tag)
        and node.name == "span"
        and bool(RED.search(node.get("style", "")))
        and node.find("sup") is not None
        and node.get_text().replace("\u00a0", " ").strip().isdigit()
    )


def has_verses(node) -> bool:
    return isinstance(node, Tag) and (is_verse_marker(node) or any(is_verse_marker(x) for x in node.find_all("span")))


def to_blockquote(text: str) -> str:
    return "\n".join(("> " + l.lstrip()) if l.strip() else ">" for l in text.splitlines())


def inline_md(node) -> str:
    if isinstance(node, NavigableString):
        return str(node).replace("\u00a0", " ")
    if not isinstance(node, Tag):
        return ""
    name = node.name
    if is_verse_marker(node):
        num = node.get_text().replace("\u00a0", " ").strip()
        return " " + VERSE_FMT.format(n=num) + " "
    inner = "".join(inline_md(c) for c in node.children)
    if name in ("b", "strong") and inner.strip():
        return f"**{inner.strip()}**"
    if name in ("i", "em") and inner.strip():
        return f"*{inner.strip()}*"
    if name == "br":
        return "  \n"
    if name == "a" and node.get("href") and inner.strip():
        return f"[{inner.strip()}]({node['href']})"
    return inner


def block_md(node) -> str:
    if isinstance(node, NavigableString):
        t = str(node).strip()
        return t
    if not isinstance(node, Tag):
        return ""
    name = node.name
    if name in ("script", "style", "iframe", "audio", "figure", "noscript"):
        return ""
    if name in ("ul", "ol"):
        items = []
        for i, li in enumerate(node.find_all("li", recursive=False), 1):
            mark = f"{i}." if name == "ol" else "-"
            items.append(f"{mark} {inline_md(li).strip()}")
        return "\n".join(items)
    if re.fullmatch(r"h[1-6]", name):
        level = min(int(name[1]) + 2, 6)  # заголовки поста ниже «# Вариант N»
        return f"{'#' * level} {inline_md(node).strip()}"
    if name == "blockquote":
        return "\n".join("> " + l for l in inline_md(node).strip().splitlines())
    if name == "p":
        return inline_md(node).strip()
    if name in ("div", "section"):
        return "\n\n".join(x for x in (block_md(c) for c in node.children) if x)
    return inline_md(node).strip()


def _walk(container):
    """Блоки поста по порядку; None - сигнал остановки (встретили hr-разделитель)."""
    for child in container.children:
        if isinstance(child, Tag) and child.name == "hr" and "wp-block-separator" in (child.get("class") or []):
            yield None
            return
        if isinstance(child, Tag) and child.name in ("div", "section") \
                and child.find("hr", class_="wp-block-separator") is not None:
            # обёртка, внутри которой лежит разделитель - заходим внутрь
            for x in _walk(child):
                yield x
                if x is None:
                    return
            continue
        yield child


def extract_post_text(html: str) -> str:
    """Текст поста до разделителя. Зачало (абзацы с красными номерами стихов)
    выносится в отдельный блок-цитату; соседние абзацы зачала склеиваются в один блок."""
    soup = BeautifulSoup(html, "html.parser")
    box = soup.select_one("div.single-content")
    if not box:
        raise RuntimeError("Не найден div.single-content")
    blocks = []  # (is_scripture, text)
    for child in _walk(box):
        if child is None:
            break
        text = re.sub(r"(?<=\S) {2,}(?=\S)", " ", block_md(child)).strip()
        if not text:
            continue
        blocks.append((has_verses(child), text))

    parts, quote = [], []
    for is_scr, text in blocks:
        if is_scr:
            quote.append(text)
            continue
        if quote:
            parts.append(to_blockquote("\n\n".join(quote)))
            quote = []
        parts.append(text)
    if quote:
        parts.append(to_blockquote("\n\n".join(quote)))
    return "\n\n".join(parts).strip()


def safe_name(query: str) -> str:
    s = query.replace(",", "").replace(f" {EM} ", "_")
    s = re.sub(r"[^\w\-]+", "_", s, flags=re.UNICODE).strip("_")
    return s or "radiovera"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", nargs="?", help="Входная строка с зачалом (можно с markdown-ссылками)")
    ap.add_argument("--query", help="Готовая строка поиска, например «2 Кор., 188 зач., IX, 6-11»")
    ap.add_argument("--out", default="/mnt/user-data/outputs", help="Папка для MD-файла")
    ap.add_argument("--max-pages", type=int, default=5, help="Максимум страниц выдачи")
    ap.add_argument("--delay", type=float, default=1.0, help="Пауза между запросами, сек")
    ap.add_argument("--dry-run", action="store_true", help="Только показать строку поиска и URL")
    args = ap.parse_args()

    if not args.query and not args.text:
        ap.error("нужна входная строка или --query")
    query = args.query or build_query(args.text)
    print(f"Строка поиска: {query}")
    print(f"URL: {search_url(query)}")
    if args.dry_run:
        return 0

    # 1. Собрать точные совпадения. На сайте встречаются и длинное, и короткое тире,
    #    поэтому ищем по каждому варианту написания, а фильтруем с нормализацией тире.
    #    Выдача отсортирована по релевантности: если на странице точных нет, дальше не листаем.
    links = []
    for variant in query_variants(query):
        for page in range(1, args.max_pages + 1):
            try:
                html = get(search_url(variant, page))
            except requests.HTTPError as e:
                if page > 1 and e.response is not None and e.response.status_code == 404:
                    break
                raise
            found, total = parse_exact_links(html, query)
            for l in found:
                if l not in links:
                    links.append(l)
            if total == 0 or not found:
                break
            time.sleep(args.delay)

    if not links:
        print("Точных совпадений не найдено.")
        return 2
    print(f"Точных совпадений: {len(links)}")

    # 2. Вычитать посты
    chunks = []
    for n, url in enumerate(links, 1):
        print(f"  Вариант {n}: {url}")
        text = extract_post_text(get(url))
        chunks.append(f"# Вариант {n}\n\n{text}\n")
        time.sleep(args.delay)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{safe_name(query)}.md"
    out.write_text("\n\n".join(chunks), encoding="utf-8")
    print(f"Готово: {out}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except requests.RequestException as e:
        print(f"Ошибка сети: {e}", file=sys.stderr)
        sys.exit(3)
