#!/usr/bin/env python3
"""Собирает EPUB из простого Markdown для отправки через Send to Kindle.

Без внешних зависимостей (только стандартная библиотека).

Поддерживаемая разметка (этого достаточно для проповеди и чтения):
  # Заголовок документа      -> титульная страница
  ## Раздел                  -> отдельная глава (пункт оглавления)
  ### Подраздел
  > цитата                   -> blockquote
  - пункт / * пункт          -> маркированный список
  ---                        -> горизонтальная черта
  **жирный**, *курсив*
  Пустая строка разделяет абзацы; одиночный перевод строки внутри абзаца
  сохраняется как <br/> (важно для текста чтения: «Пр., гл. 7: …» / «Стих: …»).

Использование:
  python build_kindle_doc.py input.md --out Propoved_2026-10-11.epub \
      --title "Проповедь на 11 октября 2026" [--author "..."] [--lang ru]
"""
import argparse
import re
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from html import escape
from pathlib import Path

CSS = """\
body { line-height: 1.4; }
h1 { text-align: center; margin: 1.5em 0 1em; }
h2 { margin: 1em 0 0.8em; }
h3 { margin: 1em 0 0.5em; }
p { margin: 0 0 0.9em 0; text-align: left; }
blockquote { margin: 0.8em 1.2em; font-style: italic; }
hr { margin: 1.2em 0; }
"""

CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


# Windows: при выводе в канал/файл Python берёт локальную кодовую страницу (cp1251/cp1252),
# и кириллица с ударениями вызывает UnicodeEncodeError. Принудительно используем UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def inline(text: str) -> str:
    """Экранирует HTML и применяет **жирный** / *курсив*."""
    text = escape(text, quote=False)
    text = re.sub(r"\*\*(?!\s)(.+?)(?<!\s)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*(?!\s|\*)(.+?)(?<!\s)\*(?!\*)", r"<em>\1</em>", text)
    return text


def paragraph_html(lines):
    return "<p>" + "<br/>\n".join(inline(l) for l in lines) + "</p>"


def blocks_to_html(md: str) -> str:
    out = []
    para, quote, items = [], [], []

    def flush():
        nonlocal para, quote, items
        if para:
            out.append(paragraph_html(para))
            para = []
        if quote:
            out.append("<blockquote>" + paragraph_html(quote) + "</blockquote>")
            quote = []
        if items:
            out.append("<ul>" + "".join(f"<li>{inline(i)}</li>" for i in items) + "</ul>")
            items = []

    for raw in md.split("\n"):
        line = raw.rstrip()
        if not line.strip():
            flush()
            continue
        m = re.match(r"^(#{1,3})\s+(.*)$", line)
        if m:
            flush()
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{inline(m.group(2).strip())}</h{lvl}>")
            continue
        if re.match(r"^(-{3,}|\*{3,})$", line.strip()):
            flush()
            out.append("<hr/>")
            continue
        m = re.match(r"^>\s?(.*)$", line)
        if m:
            if para or items:
                flush()
            quote.append(m.group(1))
            continue
        m = re.match(r"^[-*]\s+(.*)$", line)
        if m:
            if para or quote:
                flush()
            items.append(m.group(1))
            continue
        if quote or items:
            flush()
        para.append(line.strip())
    flush()
    return "\n".join(out)


def split_sections(md: str):
    """Делит документ по '## ' на главы. Возвращает [(title, md_chunk), ...]."""
    lines = md.split("\n")
    sections, cur_title, cur = [], None, []
    for line in lines:
        m = re.match(r"^##\s+(.*)$", line)
        if m:
            if cur or cur_title is not None:
                sections.append((cur_title, "\n".join(cur)))
            cur_title, cur = m.group(1).strip(), [line]
        else:
            cur.append(line)
    sections.append((cur_title, "\n".join(cur)))
    # Пустое «вступление» без содержимого не нужно
    return [(t, c) for t, c in sections if c.strip()]


def xhtml(title: str, body: str, lang: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{lang}" xml:lang="{lang}">
<head>
  <meta charset="utf-8"/>
  <title>{escape(title)}</title>
  <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
{body}
</body>
</html>
"""


def build(md: str, out: Path, title: str, author: str, lang: str):
    sections = split_sections(md)
    if not sections:
        sys.exit("Пустой входной документ.")

    # Если заголовка передано не было, берём первый '# ' из текста
    if not title:
        m = re.search(r"^#\s+(.*)$", md, re.M)
        title = m.group(1).strip() if m else "Проповедь"

    chapters = []  # (filename, toc_title, xhtml)
    for i, (sec_title, chunk) in enumerate(sections, 1):
        toc_title = sec_title or title
        fname = f"ch{i:02d}.xhtml"
        chapters.append((fname, toc_title, xhtml(toc_title, blocks_to_html(chunk), lang)))

    book_id = "urn:uuid:" + str(uuid.uuid4())
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
                '<item id="css" href="style.css" media-type="text/css"/>']
    spine = []
    for i, (fname, _, _) in enumerate(chapters, 1):
        manifest.append(f'<item id="c{i}" href="{fname}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="c{i}"/>')

    creator = f"<dc:creator>{escape(author)}</dc:creator>" if author else ""
    opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="{lang}">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">{book_id}</dc:identifier>
    <dc:title>{escape(title)}</dc:title>
    <dc:language>{lang}</dc:language>
    {creator}
    <meta property="dcterms:modified">{modified}</meta>
  </metadata>
  <manifest>
    {chr(10).join("    " + m for m in manifest).strip()}
  </manifest>
  <spine toc="ncx">
    {chr(10).join("    " + s for s in spine).strip()}
  </spine>
</package>
"""

    nav_items = "\n".join(
        f'      <li><a href="{f}">{escape(t)}</a></li>' for f, t, _ in chapters)
    nav = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{lang}" xml:lang="{lang}">
<head><meta charset="utf-8"/><title>Оглавление</title></head>
<body>
  <nav epub:type="toc" id="toc">
    <h1>Оглавление</h1>
    <ol>
{nav_items}
    </ol>
  </nav>
</body>
</html>
"""

    nav_points = "\n".join(
        f'    <navPoint id="np{i}" playOrder="{i}"><navLabel><text>{escape(t)}</text></navLabel>'
        f'<content src="{f}"/></navPoint>'
        for i, (f, t, _) in enumerate(chapters, 1))
    ncx = f"""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="{book_id}"/></head>
  <docTitle><text>{escape(title)}</text></docTitle>
  <navMap>
{nav_points}
  </navMap>
</ncx>
"""

    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w") as z:
        # mimetype должен идти первым и без сжатия
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip",
                   compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", CONTAINER_XML, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/nav.xhtml", nav, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/toc.ncx", ncx, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/style.css", CSS, compress_type=zipfile.ZIP_DEFLATED)
        for fname, _, content in chapters:
            z.writestr(f"OEBPS/{fname}", content, compress_type=zipfile.ZIP_DEFLATED)

    return title, len(chapters)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="Markdown-файл")
    ap.add_argument("--out", required=True, help="Путь к .epub (имя лучше латиницей: Propoved_YYYY-MM-DD.epub)")
    ap.add_argument("--title", default="", help="Название книги (по умолчанию — первый '# ' в тексте)")
    ap.add_argument("--author", default="", help="Автор (необязательно)")
    ap.add_argument("--lang", default="ru")
    args = ap.parse_args()

    md = Path(args.input).read_text(encoding="utf-8")
    title, n = build(md, Path(args.out), args.title, args.author, args.lang)
    size_kb = Path(args.out).stat().st_size / 1024
    print(f"OK: {args.out} — «{title}», глав: {n}, размер: {size_kb:.1f} КБ")


if __name__ == "__main__":
    main()
