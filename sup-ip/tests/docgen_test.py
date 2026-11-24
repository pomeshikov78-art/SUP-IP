#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
СУП-ИП. Автономные испытания генератора документов docgen.py (ТЗ §25).
Не требуют сервера и БД:  python3 tests/docgen_test.py
Проверяются DOCX/XLSX/PDF/CSV/HTML, кириллица, шрифты, разбор HTML-снимка отчёта.
"""
import io, os, re, sys, zipfile, zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import docgen

ok = fail = 0
def check(name, cond, extra=""):
    global ok, fail
    if cond: ok += 1; print("  ok  " + name)
    else: fail += 1; print("  FAIL " + name + ("  " + str(extra) if extra else ""))

def sample():
    d = docgen.Document("Протокол испытаний № 7", subtitle="Кириллица: ёжик, съешь булок — 1234567890 ₽",
                        meta=[("Дата", "24.11.2026"), ("Ответственный", "Морозов Дмитрий")])
    d.toc = True
    d.h1("1. Общие сведения").p("Первая строка.\nВторая строка абзаца.")
    d.list(["Пункт первый", "Пункт второй"], numbered=True)
    d.kv([("Стенд", "СИ-3"), ("Оператор", "Новиков Сергей")])
    d.table(["№", "Проверка", "Результат", "Отклонение"],
            [[1, "Бюджет радиолинии", 12.4, -3], [2, "Зона покрытия", 8.1, None], [3, "Надёжность", 0.9987, 0]],
            widths=[0.5, 3, 1, 1], aligns=["right", None, "right", "right"], title="Результаты",
            total=["Итого", "", 21.4987, -3])
    d.chart("bar", ["СИ-3", "РЛ-240", "МП-12"], [12.4, 8.1, 0.9987], title="Показатели", unit="дБ")
    d.chart("line", ["Н1", "Н2", "Н3"], [1, 5, 9], title="Динамика")
    d.chart("pie", ["Готово", "В работе", "Не начато"], [40, 35, 25], title="Структура")
    d.signatures([("Председатель", "Морозов Дмитрий"), ("Секретарь", "Козлова Мария")])
    return d

def main():
    d = sample()
    print("== Доступность форматов ==")
    av = docgen.available()
    check("шрифты DejaVu в поставке", av["pdf"] is True, av)
    check("DOCX/XLSX/CSV/HTML доступны", all(av[k] for k in ("docx", "xlsx", "csv", "html")))

    print("== DOCX ==")
    b = docgen.docx(d)
    check("DOCX — контейнер ZIP", b[:2] == b"PK")
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        names = z.namelist()
        doc = z.read("word/document.xml").decode("utf-8")
        ftr = z.read("word/footer1.xml").decode("utf-8")
        hdr = z.read("word/header1.xml").decode("utf-8")
        sty = z.read("word/styles.xml").decode("utf-8")
    for part in ("[Content_Types].xml", "_rels/.rels", "word/_rels/document.xml.rels",
                 "word/styles.xml", "word/numbering.xml", "word/header1.xml", "word/footer1.xml", "docProps/core.xml"):
        check("DOCX: часть %s" % part, part in names)
    check("DOCX: заголовок и кириллица", "Протокол испытаний" in doc and "ёжик" in doc)
    check("DOCX: стили заголовков", "Heading1" in sty and "Heading2" in sty)
    check("DOCX: таблица с шапкой", "<w:tblHeader/>" in doc and "<w:tblGrid>" in doc)
    check("DOCX: поля страниц", "PAGE" in ftr and "NUMPAGES" in ftr)
    check("DOCX: колонтитул с названием", "СУП-ИП" in hdr)
    check("DOCX: оглавление", "TOC" in doc)
    check("DOCX: блок подписей", "Председатель" in doc and "____________________" in doc)

    print("== XLSX ==")
    b = docgen.xlsx(d)
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        sheets = [n for n in z.namelist() if n.startswith("xl/worksheets/sheet")]
        s1 = z.read(sheets[0]).decode("utf-8")
        s2 = z.read(sheets[1]).decode("utf-8")
        sty = z.read("xl/styles.xml").decode("utf-8")
        wb = z.read("xl/workbook.xml").decode("utf-8")
    check("XLSX: отдельные листы по наборам данных", len(sheets) >= 3, sheets)
    check("XLSX: имена листов на русском", "Результаты" in wb)
    check("XLSX: автофильтр", "<autoFilter" in s2)
    check("XLSX: закрепление шапки", 'state="frozen"' in s2)
    check("XLSX: формула итога", "<f>SUM(" in s2)
    check("XLSX: формат даты", "dd.mm.yyyy" in sty)
    check("XLSX: пустая ячейка не ноль", re.search(r'<c r="[A-Z]+\d+" s="6"/>', s2) is not None)
    check("XLSX: источник и дата", "Источник:" in s1)

    print("== PDF ==")
    b = docgen.pdf(d)
    check("PDF: сигнатура", b.startswith(b"%PDF-1.7"))
    check("PDF: встроенные шрифты", b.count(b"/FontFile2") == 2)
    check("PDF: подмножество шрифта компактно", len(b) < 400_000, len(b))
    check("PDF: Identity-H и ToUnicode", b"/Identity-H" in b and b"/ToUnicode" in b)
    check("PDF: каталог и страницы", b"/Type /Catalog" in b and b"/Type /Pages" in b)
    check("PDF: язык документа ru-RU", b"/Lang (ru-RU)" in b)
    streams = []
    for m in re.finditer(rb"/Filter /FlateDecode >>\nstream\n", b):
        s = m.end(); e = b.find(b"\nendstream", s)
        try: streams.append(zlib.decompress(b[s:e]))
        except Exception: pass
    content = b"\n".join(x for x in streams if b" Tj" in x)
    check("PDF: текст и таблицы", b" Tj" in content and b" re S" in content)
    check("PDF: диаграммы (столбцы, линия, круг)", b" re f" in content and b" c" in content and b" l" in content)
    check("PDF: нумерация страниц «Стр. N из M»", len(re.findall(rb"/Type /Page ", b)) >= 1)
    check("PDF: метаданные UTF-16", b"/Title <FEFF" in b)
    check("PDF: xref и EOF", b"startxref" in b and b.rstrip().endswith(b"%%EOF"))

    print("== CSV / HTML ==")
    c = docgen.csv_bytes(d)
    check("CSV: BOM", c[:3] == b"\xef\xbb\xbf")
    check("CSV: разделитель ';'", b";" in c)
    h = docgen.html_bytes(d)
    check("HTML: автономность (нет внешних ссылок)", b"http://" not in h and b"https://" not in h and b"cdn" not in h.lower())
    check("HTML: таблица и заголовки", b"<table>" in h and b"<h2>" in h)

    print("== Разбор HTML-снимка отчёта (M13) ==")
    blocks = docgen.blocks_from_html("<h2>Раздел</h2><p>Текст</p><table><tr><th>А</th><th>Б</th></tr>"
                                     "<tr><td>1</td><td>2</td></tr></table><ol><li>Раз</li><li>Два</li></ol>")
    kinds = [b["t"] for b in blocks]
    check("HTML → заголовок", "h1" in kinds)
    check("HTML → абзац", "p" in kinds)
    check("HTML → таблица с шапкой", any(b["t"] == "table" and b["head"] == ["А", "Б"] for b in blocks))
    check("HTML → нумерованный список", any(b["t"] == "list" and b["numbered"] for b in blocks))
    d2 = docgen.Document("Из снимка")
    d2.blocks.extend(blocks)
    check("снимок → DOCX", docgen.docx(d2)[:2] == b"PK")
    check("снимок → PDF", docgen.pdf(d2)[:4] == b"%PDF")

    print("\nИТОГ: %d ok, %d FAIL" % (ok, fail))
    sys.exit(1 if fail else 0)

if __name__ == "__main__":
    main()
