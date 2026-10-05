#!/usr/bin/env python3
"""
Скачивает книгу «Апостол» (архим. Ианнуарий, azbyka.ru) и собирает apostol.md + index.json.

Запуск:
    pip install beautifulsoup4 requests
    python apostol_to_md.py build                # скачать, разобрать, записать apostol.md и index.json
    python apostol_to_md.py local Ианнуарий.html # то же, но из сохранённого HTML (без скачивания)
    python apostol_to_md.py find "Деян 1:1–8"    # найти переводы Ианнуария и заголовки
    python apostol_to_md.py find "Деян 1:1–8" --all   # то же, но включая чтения без перевода
    python apostol_to_md.py find "Деян 1:1–8" --synodal      # + синодальный текст зачала
    python apostol_to_md.py find "Деян 1:1–8" --translation  # + перевод архимандрита Ианнуария
    python apostol_to_md.py find "Деян 1:1–8" --text         # + оба текста

Структура apostol.md (текст книги не меняется, добавлены только маркеры секций):
    # Заголовок дня                      <- h1
    ## Прокимен                          <- от заголовка до курсивной строки «... чтение»
    ## Зачало                            <- от «... чтение» до ссылки (Деян 1:1–8)
    ## Аллилуиа                          <- опционально
    ## Перевод архимандрита Ианнуария    <- опционально, может стоять и до, и после Аллилуиа
"""
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

BASE = "https://azbyka.ru/otechnik/Iannuarij_Ivliev/apostol/"
CACHE = Path("cache")
OUT_MD = Path("apostol.md")
OUT_INDEX = Path("index.json")
TRANS_MARK = "Перевод архимандрита Ианнуария"
HEADINGS = ("h1", "h2", "h3", "h4")

SECTION_TITLES = {
    "prokimen": "Прокимен",
    "zachalo": "Зачало",
    "alleluia": "Аллилуиа",
    "translation": "Перевод архимандрита Ианнуария",
    "text": "Текст",
}


# ---------- утилиты ----------
_LATIN2CYR = str.maketrans({"a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х", "y": "у",
                            "A": "А", "E": "Е", "O": "О", "C": "С", "P": "Р", "X": "Х", "T": "Т",
                            "H": "Н", "B": "В", "M": "М", "K": "К"})


# Windows: при выводе в канал/файл Python берёт локальную кодовую страницу (cp1251/cp1252),
# и кириллица с ударениями вызывает UnicodeEncodeError. Принудительно используем UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def strip_acc(s: str) -> str:
    """Для сравнения: убирает ударения и заменяет латинские буквы-двойники на кириллические.
    В вывод не попадает — текст книги остаётся как есть."""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn" or c == "\u0306")  # оставить «й»
    s = unicodedata.normalize("NFC", s)
    return s.translate(_LATIN2CYR)


def norm_ref(s: str) -> str:
    s = strip_acc(s).lower()
    s = re.sub(r"[\u2010-\u2015\u2212]", "-", s)      # любые тире -> '-'
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s).strip(" ()")
    s = re.sub(r"\s*([:,\-])\s*", r"\1", s)
    return s


def inline(node) -> str:
    """HTML-узел -> markdown-строка; текст не меняется."""
    from bs4 import NavigableString
    out = []
    for c in node.children:
        if isinstance(c, NavigableString):
            out.append(re.sub(r"\s+", " ", str(c)))
            continue
        cls = c.get("class") or []
        if "bg_data_tooltip" in cls:
            continue
        if c.name == "br":
            out.append("\n")
        elif c.name in ("i", "em"):
            t = inline(c)
            out.append(f"*{t.strip()}*" + (" " if t.endswith(" ") else "") if t.strip() else t)
        elif c.name in ("b", "strong"):
            t = inline(c)
            out.append(f"**{t.strip()}**" + (" " if t.endswith(" ") else "") if t.strip() else t)
        elif c.name == "a":
            t = inline(c).strip()
            href = c.get("href")
            out.append(f"[{t}]({href})" if href else t)
        else:
            out.append(inline(c))
    return "".join(out)


def para_text(p) -> str:
    return re.sub(r"[ \t]+", " ", inline(p)).strip()


# ---------- загрузка ----------
def fetch(url: str, name: str) -> str:
    import requests
    CACHE.mkdir(exist_ok=True)
    f = CACHE / name
    if f.exists():
        return f.read_text(encoding="utf-8")
    for attempt in range(4):
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
        if r.status_code == 200:
            r.encoding = "utf-8"
            f.write_text(r.text, encoding="utf-8")
            time.sleep(0.7)  # не нагружаем сайт
            return r.text
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Не удалось скачать {url}: HTTP {r.status_code}")


def discover_parts() -> list[int]:
    html = fetch(BASE, "toc.html")
    nums = sorted({int(m) for m in re.findall(r"/apostol/(\d+)(?:[\"'#?/]|$)", html)})
    return nums or list(range(1, 59))


# ---------- разбор одной страницы ----------
def parse_part(html: str, part: int, url: str) -> list[dict]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.get_text() if soup.title else "").split(", Апостол в Синодальном")[0].strip()

    elems = soup.find_all(list(HEADINGS) + ["p"])
    # где начинается контент: первый заголовок == названию части, иначе — заголовок перед первым «Прокимен»
    start = None
    for i, e in enumerate(elems):
        if e.name in HEADINGS and strip_acc(e.get_text(" ", strip=True)) == strip_acc(title):
            start = i
            break
    if start is None:
        for i, e in enumerate(elems):
            if e.name == "p" and strip_acc(e.get_text(strip=True)).startswith("Прокимен"):
                j = i
                while j > 0 and elems[j].name not in HEADINGS:
                    j -= 1
                start = j
                break
    if start is None:
        print(f"  ! часть {part}: не найдено начало контента", file=sys.stderr)
        return []

    return _run(elems[start:], part, url)


def _run(elems, part, url) -> list[dict]:
    entries: list[dict] = []
    cur = None
    mode = None
    pending_title = None

    def new_section(kind):
        nonlocal mode
        sec = {"kind": kind, "lines": [], "ref": None}
        cur["sections"].append(sec)
        mode = kind
        return sec

    def flush_title():
        nonlocal pending_title
        if pending_title is not None:
            sec = new_section("zachalo")
            sec["lines"].append(pending_title)
            pending_title = None

    for e in elems:
        text_plain = strip_acc(e.get_text(" ", strip=True))
        if e.name in HEADINGS:
            if text_plain.startswith("Вам может быть интересно"):
                break
            if cur is not None:
                flush_title()
            a = e.find_previous_sibling("a")
            anchor = a.get("id") if a is not None and a.get("id") else None
            e_part, e_url = part, url
            if anchor:
                e_part = int(anchor.split("_")[0]) if anchor.split("_")[0].isdigit() else part
                e_url = BASE + anchor
            cur = {"heading": e.get_text(" ", strip=True), "part": e_part, "url": e_url,
                   "sections": [], "refs": []}
            entries.append(cur)
            mode = None
            continue
        if text_plain.startswith("Источник:") or text_plain.startswith("Читать далее"):
            break
        if cur is None or not text_plain:
            continue

        # признаки абзаца
        sup = e.find("sup")
        has_trans = bool(sup and TRANS_MARK in sup.get_text())
        only_italic = e.find(["i", "em"]) is not None and not "".join(
            t for t in e.find_all(string=True) if t.find_parent(["i", "em"]) is None).strip()
        is_title = only_italic and text_plain.rstrip(":. ").lower().endswith("чтение")
        is_alleluia = text_plain.startswith("Аллилуиа") and e.find(["i", "em"]) is not None
        is_prokimen = text_plain.startswith("Прокимен")
        text = para_text(e)

        if is_prokimen:
            flush_title()
            if mode != "prokimen":
                new_section("prokimen")
            cur["sections"][-1]["lines"].append(text)
        elif is_title:
            flush_title()
            pending_title = text
            mode = "title"
        elif has_trans:
            sec = new_section("translation")
            if pending_title is not None:
                sec["lines"].append(pending_title)
                pending_title = None
            sec["lines"].append(text)
        elif is_alleluia:
            flush_title()
            sec = new_section("alleluia")
            sec["lines"].append(text)
        else:
            if pending_title is not None:
                flush_title()
            if mode is None:
                new_section("text")
            sec = cur["sections"][-1]
            sec["lines"].append(text)
            if mode == "zachalo":
                ref = None
                span = e.select_one(".bg_bibrefs a, span.bg_bibrefs a")
                if span:
                    ref = span.get_text(strip=True)
                else:
                    m = re.search(r"\(\s*(\d+:\d+[^)]*)\)\s*$", e.get_text(" ", strip=True))
                    if m:
                        ref = "(" + m.group(1).strip() + ")"  # ссылка без названия книги
                if ref:
                    sec["ref"] = ref
                    cur["refs"].append(ref)
                    mode = None  # зачало закончилось

    if cur is not None:
        flush_title()
    # привязка переводов к ссылке той же записи
    for ent in entries:
        last = None
        for sec in ent["sections"]:
            if sec["kind"] == "zachalo" and sec["ref"]:
                last = sec["ref"]
            elif sec["kind"] == "translation":
                sec["ref"] = last
        if ent["refs"]:
            for sec in ent["sections"]:
                if sec["kind"] == "translation" and not sec["ref"]:
                    sec["ref"] = ent["refs"][0]
    return entries


# ---------- вывод ----------
def render(entries: list[dict]) -> str:
    out = []
    for ent in entries:
        out.append(f"# {ent['heading']}\n")
        out.append(f"<!-- part: {ent['part']} | {ent['url']} -->\n")
        for sec in ent["sections"]:
            out.append(f"## {SECTION_TITLES[sec['kind']]}\n")
            if sec["kind"] == "translation" and sec["ref"]:
                out.append(f"<!-- ref: {sec['ref']} -->\n")
            out.append("\n\n".join(sec["lines"]) + "\n")
    return "\n".join(out)


_TITLE_RE = re.compile(r"\*[^*]*чтение\*")


def _split_title(lines: list[str]):
    """Отделяет курсивную строку «… чтение» от текста."""
    if lines and _TITLE_RE.fullmatch(strip_acc(lines[0]).strip()):
        return lines[0], lines[1:]
    return None, lines


def build_index(entries: list[dict]) -> dict:
    """ключ = нормализованная ссылка -> список записей (по одной на каждый заголовок, где ссылка встречается)."""
    idx: dict[str, list[dict]] = {}
    for ent in entries:
        per_ref: dict[str, dict] = {}
        for sec in ent["sections"]:
            ref = sec.get("ref")
            if not ref or sec["kind"] not in ("zachalo", "translation"):
                continue
            rec = per_ref.setdefault(ref, {"synodal": None, "synodal_title": None,
                                           "translation": None, "translation_title": None})
            title, body = _split_title(sec["lines"])
            if sec["kind"] == "zachalo" and rec["synodal"] is None:
                rec["synodal_title"], rec["synodal"] = title, "\n\n".join(body)
            elif sec["kind"] == "translation":
                if rec["translation"] is None:
                    rec["translation_title"], rec["translation"] = title, "\n\n".join(body)
                else:
                    rec["translation"] += "\n\n" + "\n\n".join(body)
        for ref, rec in per_ref.items():
            idx.setdefault(norm_ref(ref), []).append({
                "ref": ref,
                "heading": ent["heading"],
                "part": ent["part"],
                "url": ent["url"],
                "has_translation": rec["translation"] is not None,
                "synodal_title": rec["synodal_title"],
                "synodal": rec["synodal"],
                "translation_title": rec["translation_title"],
                "translation": rec["translation"],
            })
    return idx


def cmd_build():
    all_entries = []
    parts = discover_parts()
    print(f"Частей: {len(parts)}")
    for n in parts:
        url = f"{BASE}{n}"
        print(f"  часть {n}/{parts[-1]}")
        ents = parse_part(fetch(url, f"{n}.html"), n, url)
        all_entries.extend(ents)
    OUT_MD.write_text(render(all_entries), encoding="utf-8")
    idx = build_index(all_entries)
    OUT_INDEX.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    n_tr = sum(1 for v in idx.values() for x in v if x["has_translation"])
    print(f"Готово: {len(all_entries)} заголовков, {len(idx)} уникальных ссылок, "
          f"{n_tr} записей с переводом -> {OUT_MD}, {OUT_INDEX}")


def cmd_local(path: str):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(Path(path).read_text(encoding="utf-8"), "html.parser")
    root = soup.find(id="book-chapters") or soup
    elems = root.find_all(list(HEADINGS) + ["p"])
    entries = _run(elems, 0, BASE)
    OUT_MD.write_text(render(entries), encoding="utf-8")
    idx = build_index(entries)
    OUT_INDEX.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    n_tr = sum(1 for v in idx.values() for x in v if x["has_translation"])
    print(f"Готово: {len(entries)} заголовков, {len(idx)} уникальных ссылок, "
          f"{n_tr} записей с переводом -> {OUT_MD}, {OUT_INDEX}")
    return entries


def cmd_find(query: str, only_tr: bool = True, synodal: bool = False, translation: bool = False):
    idx = json.loads(OUT_INDEX.read_text(encoding="utf-8"))
    hits = idx.get(norm_ref(query), [])
    if only_tr:
        hits = [h for h in hits if h["has_translation"]]
    if not hits:
        print("Не найдено.")
        return
    for h in hits:
        mark = "перевод есть" if h["has_translation"] else "перевода нет"
        print(f"# {h['heading']}\n{h['ref']}  |  часть {h['part']}  |  {mark}\n{h['url']}\n")
        if synodal:
            print("--- Синодальный перевод ---")
            if h.get("synodal_title"):
                print(h["synodal_title"])
            print(h.get("synodal") or "(нет)", "\n")
        if translation:
            print("--- Перевод архимандрита Ианнуария ---")
            if h.get("translation_title"):
                print(h["translation_title"])
            print(h.get("translation") or "(нет)", "\n")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["build"]:
        cmd_build()
    elif a[:1] == ["local"] and len(a) >= 2:
        cmd_local(a[1])
    elif a[:1] == ["find"] and len(a) >= 2:
        cmd_find(a[1], only_tr="--all" not in a,
                 synodal="--synodal" in a or "--text" in a,
                 translation="--translation" in a or "--text" in a)
    else:
        print(__doc__)
