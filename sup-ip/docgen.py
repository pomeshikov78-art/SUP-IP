#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
СУП-ИП. Генератор документов: DOCX, XLSX, PDF, CSV, HTML, JSON.
ТЗ СУП-ИП §25 «Форматы документов» (M13), §2.8 M16 (протокол совещания),
§25.1 DOCX, §25.2 PDF, §25.3 XLSX. Только стандартная библиотека Python —
полная автономность (ТЗ §8), никаких внешних сервисов и CDN.

Единая модель документа (список блоков) → любой из форматов:
    d = Document("Заголовок", subtitle="…", meta=[("Проект", "…")])
    d.h1("Раздел").p("Текст").table(["Колонка"], [[…]]).signatures([("Роль", "Имя")])
    docx_bytes = docgen.docx(d);  pdf_bytes = docgen.pdf(d);  xlsx_bytes = docgen.xlsx(d)

Возможности:
  DOCX — стили заголовков, автоматическая нумерация списков, таблицы с повторяющейся
         шапкой, колонтитулы, номера страниц (поля PAGE/NUMPAGES), оглавление (поле TOC),
         блок подписей, кириллица.
  XLSX — отдельные листы, автофильтр, закрепление шапки, типы данных (числа/даты),
         форматы дат и чисел, итоговые формулы, источник и дата формирования,
         пустые значения не подменяются нулями.
  PDF  — встроенные шрифты TrueType (кириллица), перенос строк, таблицы с границами
         и повтором шапки, векторные диаграммы, нумерация страниц «Стр. N из M».
"""
import datetime, io, os, re, struct, sys, zipfile, zlib

APP = "СУП-ИП"
BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
FONT_DIR = os.path.join(BASE, "assets", "fonts")

# ---------------------------------------------------------------- модель документа

def _s(v):
    if v is None: return ""
    if isinstance(v, float):
        return ("%.2f" % v).rstrip("0").rstrip(".") if v == v else ""
    return str(v)

class Document:
    """Независимое от формата описание документа."""

    def __init__(self, title, subtitle="", meta=None, org=APP, footer=None):
        self.title = title
        self.subtitle = subtitle
        self.meta = list(meta or [])
        self.org = org
        self.footer = footer or ""
        self.blocks = []
        self.created = datetime.datetime.now()
        self.toc = False
        self.page_numbers = True   # нумерация страниц «Стр. N из M» (блок шаблона MTG-10)

    # --- конструкторы блоков (цепочка вызовов) ---
    def h1(self, text): self.blocks.append({"t": "h1", "text": _s(text)}); return self
    def h2(self, text): self.blocks.append({"t": "h2", "text": _s(text)}); return self
    def p(self, text=""): self.blocks.append({"t": "p", "text": _s(text)}); return self
    def note(self, text): self.blocks.append({"t": "note", "text": _s(text)}); return self
    def list(self, items, numbered=False):
        self.blocks.append({"t": "list", "items": [_s(i) for i in items], "numbered": bool(numbered)}); return self
    def kv(self, pairs):
        self.blocks.append({"t": "kv", "pairs": [(_s(k), _s(v)) for k, v in pairs]}); return self
    def table(self, head, rows, widths=None, aligns=None, title="", total=None):
        self.blocks.append({"t": "table", "head": [_s(h) for h in head],
                            "rows": [[c for c in r] for r in rows],
                            "widths": widths, "aligns": aligns or [], "title": title,
                            "total": total})
        return self
    def chart(self, kind, labels, values, title="", unit=""):
        self.blocks.append({"t": "chart", "kind": kind, "labels": [_s(l) for l in labels],
                            "values": [float(v or 0) for v in values], "title": title, "unit": unit})
        return self
    def signatures(self, items):
        self.blocks.append({"t": "sign", "items": [(_s(a), _s(b)) for a, b in items]}); return self
    def pagebreak(self): self.blocks.append({"t": "pagebreak"}); return self

    def sheets(self):
        """Логически разные наборы данных → отдельные листы XLSX (ТЗ §25.3)."""
        out = []
        cur = {"name": "Сводка", "head": ["Показатель", "Значение"],
               "rows": [[k, v] for k, v in self.meta], "title": self.title}
        for b in self.blocks:
            if b["t"] == "table":
                name = b["title"] or (out and "Таблица %d" % (len(out) + 1)) or "Таблица"
                out.append({"name": name, "head": b["head"], "rows": b["rows"],
                            "aligns": b["aligns"], "total": b["total"], "title": b["title"]})
            elif b["t"] == "kv":
                cur["rows"].extend([[k, v] for k, v in b["pairs"]])
            elif b["t"] == "chart":
                out.append({"name": b["title"] or "Диаграмма", "head": ["Категория", b["unit"] or "Значение"],
                            "rows": [[l, v] for l, v in zip(b["labels"], b["values"])], "aligns": [], "total": None,
                            "title": b["title"]})
        if cur["rows"]: out.insert(0, cur)
        if not out:
            out = [{"name": "Документ", "head": ["Текст"], "rows": [[b.get("text", "")] for b in self.blocks],
                    "aligns": [], "total": None, "title": self.title}]
        return out

    def plain(self):
        """Текстовое представление (для полнотекстового поиска и предпросмотра)."""
        parts = [self.title, self.subtitle]
        parts += ["%s: %s" % (k, v) for k, v in self.meta]
        for b in self.blocks:
            t = b["t"]
            if t in ("h1", "h2", "p", "note"): parts.append(b["text"])
            elif t == "list": parts += ["• " + i for i in b["items"]]
            elif t == "kv": parts += ["%s: %s" % kv for kv in b["pairs"]]
            elif t == "table":
                if b["title"]: parts.append(b["title"])
                parts.append(" | ".join(b["head"]))
                parts += [" | ".join(_s(c) for c in r) for r in b["rows"]]
            elif t == "chart":
                parts.append(b["title"])
                parts += ["%s: %s" % (l, v) for l, v in zip(b["labels"], b["values"])]
            elif t == "sign": parts += ["%s ______________ %s" % s for s in b["items"]]
        return "\n".join(x for x in parts if x)


# ---------------------------------------------------------------- DOCX (ТЗ §25.1)

def _x(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))

def _runs(text, bold=False, size=None, color=None, italic=False):
    rpr = "<w:rPr>"
    if bold: rpr += "<w:b/>"
    if italic: rpr += "<w:i/>"
    if color: rpr += '<w:color w:val="%s"/>' % color
    if size: rpr += '<w:sz w:val="%d"/><w:szCs w:val="%d"/>' % (size * 2, size * 2)
    rpr += "</w:rPr>"
    out = []
    for i, line in enumerate(str(text).split("\n")):
        if i: out.append("<w:r><w:br/></w:r>")
        out.append('<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>' % (rpr, _x(line)))
    return "".join(out)

def _para(text="", style=None, bold=False, size=None, color=None, align=None, spacing=120,
          numbered=False, italic=False, keep=False):
    ppr = "<w:pPr>"
    if style: ppr += '<w:pStyle w:val="%s"/>' % style
    if numbered: ppr += '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>'
    if align: ppr += '<w:jc w:val="%s"/>' % align
    ppr += '<w:spacing w:after="%d" w:line="264" w:lineRule="auto"/>' % spacing
    if keep: ppr += "<w:keepNext/>"
    ppr += "</w:pPr>"
    return "<w:p>%s%s</w:p>" % (ppr, _runs(text, bold, size, color, italic))

def _cell(text, w, bold=False, shade=None, align=None):
    tcpr = '<w:tcPr><w:tcW w:w="%d" w:type="dxa"/>' % w
    if shade: tcpr += '<w:shd w:val="clear" w:color="auto" w:fill="%s"/>' % shade
    tcpr += '<w:tcMar><w:top w:w="40" w:type="dxa"/><w:bottom w:w="40" w:type="dxa"/>' \
            '<w:left w:w="70" w:type="dxa"/><w:right w:w="70" w:type="dxa"/></w:tcMar></w:tcPr>'
    body = _para(_s(text), bold=bold, size=9, align=align, spacing=0)
    return "<w:tc>%s%s</w:tc>" % (tcpr, body)

def _table_xml(b, total_w=9638):
    head, rows = b["head"], b["rows"]
    n = max(len(head), max((len(r) for r in rows), default=1)) or 1
    widths = b.get("widths")
    if widths and len(widths) == n:
        s = float(sum(widths)); ws = [int(total_w * w / s) for w in widths]
    else:
        lens = []
        for i in range(n):
            col = [_s(head[i]) if i < len(head) else ""] + [_s(r[i]) if i < len(r) else "" for r in rows]
            lens.append(max(4, min(40, max(len(c) for c in col))))
        s = float(sum(lens)); ws = [max(700, int(total_w * l / s)) for l in lens]
    borders = ("<w:tblBorders>" + "".join(
        '<w:%s w:val="single" w:sz="4" w:space="0" w:color="9AA5B1"/>' % k
        for k in ("top", "left", "bottom", "right", "insideH", "insideV")) + "</w:tblBorders>")
    xml = ['<w:tbl><w:tblPr><w:tblW w:w="%d" w:type="dxa"/>%s'
           '<w:tblLayout w:type="fixed"/></w:tblPr>' % (sum(ws), borders)]
    xml.append("<w:tblGrid>" + "".join('<w:gridCol w:w="%d"/>' % w for w in ws) + "</w:tblGrid>")
    if head:
        xml.append('<w:tr><w:trPr><w:tblHeader/></w:trPr>' +
                   "".join(_cell(head[i] if i < len(head) else "", ws[i], bold=True, shade="E8EDF3")
                           for i in range(n)) + "</w:tr>")
    for r in rows:
        xml.append("<w:tr>" + "".join(
            _cell(r[i] if i < len(r) else "", ws[i],
                  align=(b["aligns"][i] if i < len(b.get("aligns") or []) else None))
            for i in range(n)) + "</w:tr>")
    if b.get("total"):
        xml.append("<w:tr>" + "".join(_cell(b["total"][i] if i < len(b["total"]) else "", ws[i], bold=True,
                                          shade="F2F5F9") for i in range(n)) + "</w:tr>")
    xml.append("</w:tbl>")
    xml.append(_para("", spacing=60))
    return "".join(xml)

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial"/>
<w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="ru-RU"/></w:rPr></w:rPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/>
<w:pPr><w:spacing w:after="120"/></w:pPr><w:rPr><w:b/><w:sz w:val="36"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>
<w:pPr><w:keepNext/><w:outlineLvl w:val="0"/><w:spacing w:before="240" w:after="120"/></w:pPr>
<w:rPr><w:b/><w:sz w:val="28"/><w:color w:val="1F3864"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/>
<w:pPr><w:keepNext/><w:outlineLvl w:val="1"/><w:spacing w:before="180" w:after="80"/></w:pPr>
<w:rPr><w:b/><w:sz w:val="24"/><w:color w:val="2E5496"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Caption"><w:name w:val="caption"/><w:basedOn w:val="Normal"/>
<w:rPr><w:i/><w:sz w:val="18"/><w:color w:val="555F6D"/></w:rPr></w:style>
</w:styles>"""

_NUMBERING = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="singleLevel"/>
<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/>
<w:lvlJc w:val="left"/><w:pPr><w:ind w:left="482" w:hanging="284"/></w:pPr></w:lvl></w:abstractNum>
<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>"""

def _field(instr, placeholder="1"):
    return ('<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            '<w:r><w:instrText xml:space="preserve"> %s </w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
            '<w:r><w:t>%s</w:t></w:r>'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r>' % (instr, placeholder))

def docx(d: Document) -> bytes:
    body = [_para(d.title, style="Title")]
    if d.subtitle: body.append(_para(d.subtitle, size=12, color="555F6D"))
    if d.meta:
        body.append(_table_xml({"head": ["Реквизит", "Значение"],
                                "rows": [[k, v] for k, v in d.meta], "widths": [1, 2.4], "aligns": []}))
    if d.toc:
        body.append(_para("Оглавление", style="Heading1"))
        body.append("<w:p><w:pPr></w:pPr>" + _field('TOC \\o "1-2" \\h \\z \\u',
                    "Оглавление: обновите поле в Word (Ctrl+A, F9)") + "</w:p>")
    for b in d.blocks:
        t = b["t"]
        if t == "h1": body.append(_para(b["text"], style="Heading1"))
        elif t == "h2": body.append(_para(b["text"], style="Heading2"))
        elif t == "p": body.append(_para(b["text"]))
        elif t == "note": body.append(_para(b["text"], italic=True, size=9, color="555F6D"))
        elif t == "list":
            for i, it in enumerate(b["items"]):
                body.append(_para(it, numbered=b["numbered"]) if b["numbered"]
                            else _para("•  " + it))
        elif t == "kv":
            body.append(_table_xml({"head": [], "rows": [[k, v] for k, v in b["pairs"]],
                                    "widths": [1, 2.4], "aligns": []}))
        elif t == "table":
            if b["title"]: body.append(_para(b["title"], style="Heading2"))
            body.append(_table_xml(b))
        elif t == "chart":
            if b["title"]: body.append(_para(b["title"], style="Heading2"))
            body.append(_table_xml({"head": ["Категория", b["unit"] or "Значение"],
                                    "rows": [[l, v] for l, v in zip(b["labels"], b["values"])],
                                    "widths": [3, 1], "aligns": [None, "right"]}))
            body.append(_para("Данные диаграммы. Графическое представление — в PDF-версии документа.",
                              italic=True, size=9, color="555F6D"))
        elif t == "sign":
            body.append(_para(""))
            for role, name in b["items"]:
                body.append(_para("%s ____________________  %s" % (role, name), spacing=200))
        elif t == "pagebreak":
            body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
    sect = ('<w:sectPr><w:headerReference w:type="default" r:id="rIdH"/><w:footerReference w:type="default" r:id="rIdF"/>'
            '<w:pgSz w:w="11906" w:h="16838"/>'
            '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" w:header="567" w:footer="567"/>'
            '</w:sectPr>')
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<w:body>' + "".join(body) + sect + '</w:body></w:document>')
    stamp = d.created.strftime("%d.%m.%Y %H:%M")
    header = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              + _para("%s · %s" % (d.org, d.title), size=8, color="77808C", align="right", spacing=0)
              + '</w:hdr>')
    footer = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              '<w:p><w:pPr><w:jc w:val="center"/></w:pPr>'
              + _runs("%s · сформировано %s%s" % (d.footer or d.org, stamp, " · стр. " if d.page_numbers else ""),
                      size=8, color="77808C")
              + (_field("PAGE", "1") + _runs(" из ", size=8, color="77808C") + _field("NUMPAGES", "1")
                 if d.page_numbers else "")
              + '</w:p></w:ftr>')
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            '<dc:title>%s</dc:title><dc:creator>%s</dc:creator><cp:lastModifiedBy>%s</cp:lastModifiedBy>'
            '<dcterms:created xsi:type="dcterms:W3CDTF">%s</dcterms:created>'
            '</cp:coreProperties>' % (_x(d.title), _x(d.org), _x(d.org), d.created.strftime("%Y-%m-%dT%H:%M:%SZ")))
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
          '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
          '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
          '<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>'
          '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
          '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
          '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            '</Relationships>')
    drels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             '<Relationship Id="rIdS" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
             '<Relationship Id="rIdN" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>'
             '<Relationship Id="rIdH" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>'
             '<Relationship Id="rIdF" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>'
             '</Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("docProps/core.xml", core)
        z.writestr("word/document.xml", document)
        z.writestr("word/styles.xml", _STYLES)
        z.writestr("word/numbering.xml", _NUMBERING)
        z.writestr("word/header1.xml", header)
        z.writestr("word/footer1.xml", footer)
        z.writestr("word/_rels/document.xml.rels", drels)
    return buf.getvalue()


# ---------------------------------------------------------------- XLSX (ТЗ §25.3)

def _sheet_name(name, used):
    n = re.sub(r"[\[\]\:\*\?\/\\]", " ", str(name or "Лист")).strip()[:31] or "Лист"
    base, i = n, 2
    while n.lower() in used:
        suf = " (%d)" % i; n = base[:31 - len(suf)] + suf; i += 1
    used.add(n.lower())
    return n

def _col_letter(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s

_EXCEL_EPOCH = datetime.datetime(1899, 12, 30)

def _cell_value(v):
    """→ (тип, значение, стиль). Пустые значения остаются пустыми (ТЗ §25.3)."""
    if v is None or v == "": return ("blank", None, 0)
    if isinstance(v, bool): return ("b", 1 if v else 0, 0)
    if isinstance(v, (int, float)): return ("n", v, 4 if isinstance(v, float) else 3)
    if isinstance(v, (datetime.date, datetime.datetime)):
        dd = datetime.datetime(v.year, v.month, v.day) if isinstance(v, datetime.date) and not isinstance(v, datetime.datetime) else v
        return ("n", (dd - _EXCEL_EPOCH).days + (dd.hour * 3600 + dd.minute * 60 + dd.second) / 86400.0, 5)
    s = str(v)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        try:
            dt = datetime.date.fromisoformat(s)
            return ("n", (datetime.datetime(dt.year, dt.month, dt.day) - _EXCEL_EPOCH).days, 5)
        except ValueError: pass
    return ("s", s, 0)

_XLSX_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="3"><numFmt numFmtId="164" formatCode="#,##0"/>
<numFmt numFmtId="165" formatCode="#,##0.00"/><numFmt numFmtId="166" formatCode="dd.mm.yyyy"/></numFmts>
<fonts count="5">
<font><sz val="11"/><name val="Calibri"/></font>
<font><b/><sz val="14"/><color rgb="FF1F3864"/><name val="Calibri"/></font>
<font><b/><sz val="11"/><color rgb="FF1F3864"/><name val="Calibri"/></font>
<font><i/><sz val="9"/><color rgb="FF555F6D"/><name val="Calibri"/></font>
<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFE8EDF3"/><bgColor indexed="64"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFF2F5F9"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border>
<border><left style="thin"><color rgb="FF9AA5B1"/></left><right style="thin"><color rgb="FF9AA5B1"/></right>
<top style="thin"><color rgb="FF9AA5B1"/></top><bottom style="thin"><color rgb="FF9AA5B1"/></bottom><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="8">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>
<xf numFmtId="0" fontId="2" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>
<xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"/>
<xf numFmtId="165" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"/>
<xf numFmtId="166" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"/>
<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
<xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1"/>
</cellXfs>
</styleSheet>"""

def xlsx(d: Document, extra_sheets=None) -> bytes:
    sheets = d.sheets() + list(extra_sheets or [])
    used, parts, wb_sheets, wb_rels = set(), [], [], []
    stamp = d.created.strftime("%d.%m.%Y %H:%M")
    for si, sh in enumerate(sheets, 1):
        name = _sheet_name(sh.get("name") or sh.get("title") or "Лист %d" % si, used)
        head = sh.get("head") or []
        rows = sh.get("rows") or []
        n = max(len(head), max((len(r) for r in rows), default=1), 1)
        xml = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">']
        widths = []
        for i in range(n):
            col = [_s(head[i]) if i < len(head) else ""] + [_s(r[i]) if i < len(r) else "" for r in rows]
            widths.append(min(52, max(10, max(len(c) for c in col) + 2)))
        xml.append("<cols>" + "".join('<col min="%d" max="%d" width="%d" customWidth="1"/>' % (i + 1, i + 1, w)
                                      for i, w in enumerate(widths)) + "</cols>")
        body, r = [], 1
        # шапка документа: источник и дата формирования (ТЗ §25.3)
        body.append('<row r="1"><c r="A1" s="1" t="inlineStr"><is><t>%s</t></is></c></row>' % _x(sh.get("title") or d.title))
        body.append('<row r="2"><c r="A2" s="7" t="inlineStr"><is><t>%s</t></is></c></row>'
                    % _x("Источник: %s · данные СУП-ИП (PostgreSQL) · сформировано %s" % (d.org, stamp)))
        r = 4
        hdr_row = r
        if head:
            cells = "".join('<c r="%s%d" s="2" t="inlineStr"><is><t>%s</t></is></c>'
                            % (_col_letter(i), r, _x(head[i] if i < len(head) else "")) for i in range(n))
            body.append('<row r="%d" ht="28" customHeight="1">%s</row>' % (r, cells)); r += 1
        first_data = r
        for row in rows:
            cells = []
            for i in range(n):
                v = row[i] if i < len(row) else None
                typ, val, st = _cell_value(v)
                ref = "%s%d" % (_col_letter(i), r)
                if typ == "blank":
                    cells.append('<c r="%s" s="6"/>' % ref)
                elif typ == "s":
                    cells.append('<c r="%s" s="6" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (ref, _x(val)))
                else:
                    cells.append('<c r="%s" s="%d"><v>%s</v></c>' % (ref, st, val))
            body.append('<row r="%d">%s</row>' % (r, "".join(cells))); r += 1
        last_data = r - 1
        # итоговая строка с формулами (ТЗ §25.3: формулы там, где необходимы)
        if sh.get("total") or (rows and any(isinstance(c, (int, float)) and not isinstance(c, bool)
                                           for row in rows for c in row)):
            cells = []
            for i in range(n):
                ref = "%s%d" % (_col_letter(i), r)
                numeric = [row[i] for row in rows if i < len(row) and isinstance(row[i], (int, float))
                           and not isinstance(row[i], bool)]
                if i == 0:
                    cells.append('<c r="%s" s="2" t="inlineStr"><is><t>%s</t></is></c>'
                                 % (ref, _x((sh.get("total") or ["Итого"])[0] or "Итого")))
                elif numeric and last_data >= first_data:
                    cells.append('<c r="%s" s="3"><f>SUM(%s%d:%s%d)</f></c>'
                                 % (ref, _col_letter(i), first_data, _col_letter(i), last_data))
                else:
                    cells.append('<c r="%s" s="6"/>' % ref)
            body.append('<row r="%d">%s</row>' % (r, "".join(cells))); r += 1
        xml.append('<sheetData>' + "".join(body) + '</sheetData>')
        if head and last_data >= first_data:
            xml.append('<autoFilter ref="A%d:%s%d"/>' % (hdr_row, _col_letter(n - 1), last_data))
        xml.append('<sheetViews><sheetView workbookViewId="0"><pane ySplit="%d" topLeftCell="A%d" '
                   'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>' % (hdr_row, hdr_row + 1))
        xml.append('<pageMargins left="0.5" right="0.5" top="0.6" bottom="0.6" header="0.3" footer="0.3"/>')
        xml.append('<headerFooter><oddHeader>&amp;L%s&amp;R%s</oddHeader>'
                   '<oddFooter>&amp;L%s&amp;RСтр. &amp;P из &amp;N</oddFooter></headerFooter>'
                   % (_x(d.title[:60]), _x(stamp), _x(d.org)))
        xml.append("</worksheet>")
        # sheetViews должен предшествовать sheetData по схеме — пересобираем в верном порядке
        s = "".join(xml)
        views = re.search(r"<sheetViews>.*?</sheetViews>", s, re.S).group(0)
        s = s.replace(views, "")
        s = s.replace("<cols>", views + "<cols>", 1)
        parts.append(("xl/worksheets/sheet%d.xml" % si, s))
        wb_sheets.append('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (_x(name), si, si))
        wb_rels.append('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet%d.xml"/>' % (si, si))
    styles_id = len(sheets) + 1
    wb_rels.append('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>' % styles_id)
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets>' + "".join(wb_sheets) + '</sheets></workbook>')
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
          '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
          + "".join('<Override PartName="/%s" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' % p
                    for p, _ in parts) + '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(wb_rels) + '</Relationships>')
        z.writestr("xl/styles.xml", _XLSX_STYLES)
        for p, s in parts: z.writestr(p, s)
    return buf.getvalue()


# ---------------------------------------------------------------- PDF (ТЗ §25.2)

class TTFont:
    """Разбор TrueType: метрики и cmap для встроенного шрифта PDF (Identity-H)."""

    def __init__(self, path):
        with open(path, "rb") as f: self.data = f.read()
        self.tables = {}
        num = struct.unpack(">H", self.data[4:6])[0]
        for i in range(num):
            off = 12 + 16 * i
            tag = self.data[off:off + 4].decode("latin-1")
            o, l = struct.unpack(">II", self.data[off + 8:off + 16])
            self.tables[tag] = (o, l)
        head = self._t("head")
        self.upem = struct.unpack(">H", head[18:20])[0]
        hhea = self._t("hhea")
        self.ascent = struct.unpack(">h", hhea[4:6])[0]
        self.descent = struct.unpack(">h", hhea[6:8])[0]
        self.num_hmetrics = struct.unpack(">H", hhea[34:36])[0]
        self.num_glyphs = struct.unpack(">H", self._t("maxp")[4:6])[0]
        self.advances = []
        hmtx = self._t("hmtx")
        for i in range(self.num_hmetrics):
            self.advances.append(struct.unpack(">H", hmtx[i * 4:i * 4 + 2])[0])
        if self.advances:
            self.advances += [self.advances[-1]] * (self.num_glyphs - len(self.advances))
        self.cmap = self._read_cmap()
        self.used = {}

    def _t(self, tag):
        o, l = self.tables[tag]
        return self.data[o:o + l]

    def _read_cmap(self):
        cm = self._t("cmap")
        n = struct.unpack(">H", cm[2:4])[0]
        best = None
        for i in range(n):
            pid, eid, off = struct.unpack(">HHI", cm[4 + i * 8:12 + i * 8])
            fmt = struct.unpack(">H", cm[off:off + 2])[0]
            score = {(3, 10): 5, (3, 1): 4, (0, 4): 3, (0, 3): 3, (0, 6): 2}.get((pid, eid), 1 if pid == 0 else 0)
            if fmt in (4, 12) and (best is None or score > best[0]): best = (score, off, fmt)
        m = {}
        if not best: return m
        _, off, fmt = best
        if fmt == 4:
            segx2 = struct.unpack(">H", cm[off + 6:off + 8])[0]
            ends = struct.unpack(">%dH" % (segx2 // 2), cm[off + 14:off + 14 + segx2])
            so = off + 16 + segx2
            starts = struct.unpack(">%dH" % (segx2 // 2), cm[so:so + segx2])
            do = so + segx2
            deltas = struct.unpack(">%dh" % (segx2 // 2), cm[do:do + segx2])
            ro = do + segx2
            ranges = struct.unpack(">%dH" % (segx2 // 2), cm[ro:ro + segx2])
            for i in range(segx2 // 2):
                for c in range(starts[i], min(ends[i], 0xFFFF) + 1):
                    if ranges[i] == 0: g = (c + deltas[i]) & 0xFFFF
                    else:
                        gi = ro + i * 2 + ranges[i] + (c - starts[i]) * 2
                        if gi + 2 > len(cm): continue
                        g = struct.unpack(">H", cm[gi:gi + 2])[0]
                        if g: g = (g + deltas[i]) & 0xFFFF
                    if g: m[c] = g
        else:
            ngroups = struct.unpack(">I", cm[off + 12:off + 16])[0]
            for i in range(ngroups):
                s, e, gs = struct.unpack(">III", cm[off + 16 + i * 12:off + 28 + i * 12])
                for c in range(s, min(e, s + 65535) + 1): m[c] = gs + (c - s)
        return m

    def gid(self, ch):
        g = self.cmap.get(ord(ch), 0)
        if g: self.used[g] = ord(ch)
        return g

    def width(self, ch, size):
        g = self.cmap.get(ord(ch), 0)
        adv = self.advances[g] if g < len(self.advances) else self.upem // 2
        return adv * size / float(self.upem)

    def text_width(self, text, size):
        return sum(self.width(c, size) for c in text)

    def hexstr(self, text):
        return "".join("%04X" % self.gid(c) for c in text)

    # --- подмножество шрифта: неиспользуемые глифы обнуляются (PDF остаётся компактным) ---
    def subset(self):
        need = set(self.used) | {0}
        glyf_o, glyf_l = self.tables["glyf"]
        loca_o, _ = self.tables["loca"]
        head = bytearray(self._t("head"))
        long_loca = struct.unpack(">h", head[50:52])[0] == 1
        n = self.num_glyphs
        if long_loca:
            loca = list(struct.unpack(">%dI" % (n + 1), self.data[loca_o:loca_o + 4 * (n + 1)]))
        else:
            loca = [2 * x for x in struct.unpack(">%dH" % (n + 1), self.data[loca_o:loca_o + 2 * (n + 1)])]
        glyf = self.data[glyf_o:glyf_o + glyf_l]

        def components(g):
            s, e = loca[g], loca[g + 1]
            if e - s < 10: return []
            if struct.unpack(">h", glyf[s:s + 2])[0] >= 0: return []   # простой глиф
            out, i, flags = [], s + 10, 0x20
            while flags & 0x20:
                flags, gi = struct.unpack(">HH", glyf[i:i + 4]); out.append(gi)
                i += 4
                i += 4 if flags & 0x1 else 2
                if flags & 0x8: i += 2
                elif flags & 0x40: i += 4
                elif flags & 0x80: i += 8
            return out

        stack = list(need)
        while stack:
            g = stack.pop()
            for c in components(g):
                if c not in need: need.add(c); stack.append(c)
        chunks, offs, pos = [], [0], 0
        for g in range(n):
            s, e = loca[g], loca[g + 1]
            data = glyf[s:e] if (g in need and e > s) else b""
            if len(data) % 2: data += b"\0"
            chunks.append(data); pos += len(data); offs.append(pos)
        new_glyf = b"".join(chunks)
        new_loca = struct.pack(">%dI" % (n + 1), *offs)
        struct.pack_into(">h", head, 50, 1)
        tables = {}
        for tag, (o, l) in self.tables.items():
            tables[tag] = new_glyf if tag == "glyf" else new_loca if tag == "loca" else \
                bytes(head) if tag == "head" else self.data[o:o + l]
        return _sfnt(tables)


def _sfnt(tables):
    """Сборка TrueType из набора таблиц (выравнивание 4, контрольные суммы)."""
    tags = sorted(tables)
    num = len(tags)
    sr = 16
    while sr * 2 <= num * 16: sr *= 2
    out = bytearray(struct.pack(">IHHHH", 0x00010000, num, sr, (sr // 16).bit_length() - 1, num * 16 - sr))
    offset = 12 + 16 * num
    directory, body = bytearray(), bytearray()
    for tag in tags:
        data = tables[tag]
        pad = (-len(data)) % 4
        chk = 0
        padded = data + b"\0" * pad
        for i in range(0, len(padded), 4):
            chk = (chk + struct.unpack(">I", padded[i:i + 4])[0]) & 0xFFFFFFFF
        directory += struct.pack(">4sIII", tag.encode("latin-1"), chk, offset, len(data))
        body += padded
        offset += len(padded)
    return bytes(out + directory + body)


_FONT_CACHE = {}

def _font(bold=False):
    key = "b" if bold else "r"
    if key not in _FONT_CACHE:
        name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
        _FONT_CACHE[key] = TTFont(os.path.join(FONT_DIR, name))
    return _FONT_CACHE[key]

def _circle(cx, cy, r):
    """Окружность четырьмя кривыми Безье (PDF не имеет оператора arc)."""
    k = 0.5522847498 * r
    return ("%.2f %.2f m "
            "%.2f %.2f %.2f %.2f %.2f %.2f c "
            "%.2f %.2f %.2f %.2f %.2f %.2f c "
            "%.2f %.2f %.2f %.2f %.2f %.2f c "
            "%.2f %.2f %.2f %.2f %.2f %.2f c") % (
        cx - r, cy,
        cx - r, cy + k, cx - k, cy + r, cx, cy + r,
        cx + k, cy + r, cx + r, cy + k, cx + r, cy,
        cx + r, cy - k, cx + k, cy - r, cx, cy - r,
        cx - k, cy - r, cx - r, cy - k, cx - r, cy)

def _pdf_escape(s):
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

class PDFWriter:
    """Постраничная вёрстка A4 со встроенными шрифтами (кириллица), таблицами и диаграммами."""

    W, H = 595.28, 841.89
    M = 42.0

    def __init__(self, d: Document):
        self.d = d
        self.reg = _font(False)
        self.bold = _font(True)
        self.pages = []
        self.cur = []
        self.y = self.H - self.M
        self._newpage(first=True)

    # --- примитивы ---
    def _newpage(self, first=False):
        if self.cur: self.pages.append(self.cur)
        self.cur = []
        self.y = self.H - self.M - 14
        if not first:
            self._header()

    def _header(self):
        t = "%s · %s" % (self.d.org, self.d.title)
        self.cur.append("BT /F1 7.5 Tf 0.45 0.48 0.52 rg 1 0 0 1 %.2f %.2f Tm <%s> Tj ET"
                        % (self.M, self.H - 28, self.reg.hexstr(t[:110])))
        self.cur.append("0.75 0.79 0.83 RG 0.5 w %.2f %.2f m %.2f %.2f l S"
                        % (self.M, self.H - 33, self.W - self.M, self.H - 33))

    def _need(self, h):
        if self.y - h < self.M + 26: self._newpage()

    def text(self, s, size=10, bold=False, color=(0.11, 0.13, 0.16), x=None, dy=None, leading=1.32):
        f = self.bold if bold else self.reg
        x = self.M if x is None else x
        self.cur.append("BT /%s %.2f Tf %.3f %.3f %.3f rg 1 0 0 1 %.2f %.2f Tm <%s> Tj ET"
                        % ("F2" if bold else "F1", size, color[0], color[1], color[2], x, self.y, f.hexstr(s)))
        self.y -= (dy if dy is not None else size * leading)

    def wrap(self, s, size, maxw, bold=False):
        f = self.bold if bold else self.reg
        lines = []
        for para in str(s).split("\n"):
            words, line = para.split(" "), ""
            for w in words:
                cand = (line + " " + w).strip()
                if f.text_width(cand, size) <= maxw or not line:
                    line = cand
                else:
                    lines.append(line); line = w
                while f.text_width(line, size) > maxw and len(line) > 1:
                    k = len(line) - 1
                    while k > 1 and f.text_width(line[:k], size) > maxw: k -= 1
                    lines.append(line[:k]); line = line[k:]
            lines.append(line)
        return lines

    def para(self, s, size=10, bold=False, color=(0.11, 0.13, 0.16), indent=0, space=4):
        maxw = self.W - 2 * self.M - indent
        for ln in self.wrap(s, size, maxw, bold):
            self._need(size * 1.32)
            self.text(ln, size, bold, color, x=self.M + indent)
        self.y -= space

    def rule(self, gray=0.78):
        self._need(8)
        self.cur.append("%.2f %.2f %.2f RG 0.6 w %.2f %.2f m %.2f %.2f l S"
                        % (gray, gray, gray, self.M, self.y + 4, self.W - self.M, self.y + 4))
        self.y -= 10

    def table(self, b):
        head, rows = b["head"], b["rows"]
        n = max(len(head), max((len(r) for r in rows), default=1), 1)
        avail = self.W - 2 * self.M
        widths = b.get("widths")
        if widths and len(widths) == n:
            s = float(sum(widths)); ws = [avail * w / s for w in widths]
        else:
            lens = []
            for i in range(n):
                col = [_s(head[i]) if i < len(head) else ""] + [_s(r[i]) if i < len(r) else "" for r in rows]
                lens.append(max(4.0, min(34.0, max(len(c) for c in col))))
            s = float(sum(lens)); ws = [avail * l / s for l in lens]
        size = 8.6 if n <= 7 else 7.6
        pad = 4.0

        def row_cells(cells, bold=False, shade=None):
            wrapped, hmax = [], size * 1.28 + 2 * pad
            for i in range(n):
                txt = _s(cells[i]) if i < len(cells) else ""
                ls = self.wrap(txt, size, ws[i] - 2 * pad, bold)
                wrapped.append(ls)
                hmax = max(hmax, len(ls) * size * 1.28 + 2 * pad)
            return wrapped, hmax

        def draw_row(cells, bold=False, shade=None, header=False):
            wrapped, h = row_cells(cells, bold)
            if self.y - h < self.M + 26:
                self._newpage()
                if head and not header: draw_row(head, True, (0.91, 0.93, 0.96), True)
            top = self.y
            x = self.M
            if shade:
                self.cur.append("%.3f %.3f %.3f rg %.2f %.2f %.2f %.2f re f"
                                % (shade[0], shade[1], shade[2], self.M, top - h, avail, h))
            self.cur.append("0.62 0.66 0.70 RG 0.4 w")
            for i in range(n):
                self.cur.append("%.2f %.2f %.2f %.2f re S" % (x, top - h, ws[i], h))
                ty = top - pad - size
                for ln in wrapped[i]:
                    al = (b.get("aligns") or [])
                    a = al[i] if i < len(al) else None
                    f = self.bold if bold else self.reg
                    tw = f.text_width(ln, size)
                    tx = x + pad
                    if a == "right": tx = x + ws[i] - pad - tw
                    elif a == "center": tx = x + (ws[i] - tw) / 2
                    self.cur.append("BT /%s %.2f Tf 0.11 0.13 0.16 rg 1 0 0 1 %.2f %.2f Tm <%s> Tj ET"
                                    % ("F2" if bold else "F1", size, tx, ty, f.hexstr(ln)))
                    ty -= size * 1.28
                x += ws[i]
            self.y = top - h

        if b.get("title"):
            self.para(b["title"], size=11, bold=True, color=(0.12, 0.22, 0.39), space=3)
        if head: draw_row(head, True, (0.91, 0.93, 0.96), True)
        for r in rows: draw_row(r)
        if b.get("total"): draw_row(b["total"], True, (0.95, 0.96, 0.98))
        self.y -= 8

    def chart(self, b):
        """Векторная диаграмма (столбцы/линия/круг) — без растровых зависимостей."""
        labels, values = b["labels"], b["values"]
        if not values: return
        title = b["title"] or ""
        if title: self.para(title, size=11, bold=True, color=(0.12, 0.22, 0.39), space=3)
        h = 170.0
        self._need(h + 30)
        top, left = self.y, self.M + 34
        wid = self.W - 2 * self.M - 44
        vmax = max(max(values), 0.0001)
        kind = b.get("kind", "bar")
        if kind == "pie":
            total = sum(values) or 1
            cx, cy, r = self.M + 110, top - h / 2, min(h / 2 - 6, 72)
            a0 = 90.0
            import math
            palette = [(0.20, 0.42, 0.72), (0.86, 0.53, 0.16), (0.25, 0.65, 0.45), (0.72, 0.28, 0.32),
                       (0.45, 0.35, 0.68), (0.35, 0.60, 0.75), (0.75, 0.62, 0.25), (0.50, 0.55, 0.60)]
            for i, v in enumerate(values):
                sweep = 360.0 * v / total
                col = palette[i % len(palette)]
                self.cur.append("%.3f %.3f %.3f rg" % col)
                steps = max(2, int(sweep / 5))
                pts = ["%.2f %.2f m" % (cx, cy)]
                for k in range(steps + 1):
                    a = math.radians(a0 - sweep * k / steps)
                    pts.append("%.2f %.2f l" % (cx + r * math.cos(a), cy + r * math.sin(a)))
                self.cur.append(" ".join(pts) + " f")
                a0 -= sweep
            ly = top - 12
            for i, (l, v) in enumerate(zip(labels, values)):
                col = palette[i % len(palette)]
                self.cur.append("%.3f %.3f %.3f rg %.2f %.2f 7 7 re f" % (col[0], col[1], col[2], self.M + 210, ly - 6))
                self.cur.append("BT /F1 8 Tf 0.11 0.13 0.16 rg 1 0 0 1 %.2f %.2f Tm <%s> Tj ET"
                                % (self.M + 222, ly - 5, self.reg.hexstr("%s — %s (%.0f%%)" % (l[:38], _s(v), 100.0 * v / total))))
                ly -= 13
            self.y = top - h - 10
            return
        # оси и сетка
        self.cur.append("0.70 0.74 0.78 RG 0.5 w %.2f %.2f m %.2f %.2f l S" % (left, top - h, left + wid, top - h))
        for k in range(5):
            yy = top - h * k / 4.0
            self.cur.append("0.88 0.90 0.92 RG 0.3 w %.2f %.2f m %.2f %.2f l S" % (left, yy, left + wid, yy))
            val = vmax * k / 4.0
            self.cur.append("BT /F1 7 Tf 0.42 0.45 0.49 rg 1 0 0 1 %.2f %.2f Tm <%s> Tj ET"
                            % (self.M, yy - 2, self.reg.hexstr(_s(round(val, 1)))))
        n = len(values)
        if kind == "line":
            pts = []
            for i, v in enumerate(values):
                x = left + (wid * (i + 0.5) / n)
                y = top - h * (v / vmax)
                pts.append("%.2f %.2f %s" % (x, y, "m" if i == 0 else "l"))
            self.cur.append("0.20 0.42 0.72 RG 1.4 w " + " ".join(pts) + " S")
            for i, v in enumerate(values):
                x = left + (wid * (i + 0.5) / n); y = top - h * (v / vmax)
                self.cur.append("0.20 0.42 0.72 rg " + _circle(x, y, 2.2) + " f")
        else:
            bw = wid / n * 0.62
            for i, v in enumerate(values):
                x = left + wid * (i + 0.5) / n - bw / 2
                bh = h * (v / vmax)
                self.cur.append("0.24 0.47 0.76 rg %.2f %.2f %.2f %.2f re f" % (x, top - h, bw, max(bh, 0.6)))
        step = max(1, int(n / 12))
        for i, l in enumerate(labels):
            if i % step: continue
            x = left + wid * (i + 0.5) / n
            lab = l[:12]
            tw = self.reg.text_width(lab, 7)
            self.cur.append("BT /F1 7 Tf 0.35 0.38 0.42 rg 1 0 0 1 %.2f %.2f Tm <%s> Tj ET"
                            % (x - tw / 2, top - h - 11, self.reg.hexstr(lab)))
        if b.get("unit"):
            self.cur.append("BT /F1 7.5 Tf 0.42 0.45 0.49 rg 1 0 0 1 %.2f %.2f Tm <%s> Tj ET"
                            % (self.M, top + 4, self.reg.hexstr(b["unit"])))
        self.y = top - h - 22

    def build(self):
        d = self.d
        # титульная часть
        self.para(d.title, size=17, bold=True, color=(0.12, 0.22, 0.39), space=4)
        if d.subtitle: self.para(d.subtitle, size=10.5, color=(0.33, 0.37, 0.43), space=6)
        if d.meta:
            self.table({"head": ["Реквизит", "Значение"], "rows": [[k, v] for k, v in d.meta],
                        "widths": [1, 2.4], "aligns": []})
        for b in d.blocks:
            t = b["t"]
            if t == "h1":
                self.y -= 6; self._need(30)
                self.para(b["text"], size=13, bold=True, color=(0.12, 0.22, 0.39), space=3)
            elif t == "h2":
                self.y -= 3; self._need(24)
                self.para(b["text"], size=11, bold=True, color=(0.18, 0.33, 0.59), space=2)
            elif t == "p": self.para(b["text"], size=10)
            elif t == "note": self.para(b["text"], size=8.5, color=(0.36, 0.40, 0.45))
            elif t == "list":
                for i, it in enumerate(b["items"], 1):
                    self.para(("%d. " % i if b["numbered"] else "• ") + it, size=10, indent=10, space=2)
            elif t == "kv":
                self.table({"head": [], "rows": [[k, v] for k, v in b["pairs"]], "widths": [1, 2.4], "aligns": []})
            elif t == "table": self.table(b)
            elif t == "chart": self.chart(b)
            elif t == "sign":
                self.y -= 14
                for role, name in b["items"]:
                    self._need(26)
                    self.para("%s ____________________  %s" % (role, name), size=10, space=10)
            elif t == "pagebreak": self._newpage()
        if self.cur: self.pages.append(self.cur)
        return self._emit()

    def _emit(self):
        total = len(self.pages)
        objs = {}   # номер → bytes
        def add(body):
            n = len(objs) + 1
            objs[n] = body if isinstance(body, bytes) else body.encode("latin-1")
            return n
        # 1: Catalog, 2: Pages (заполняются после)
        add(""); add("")
        font_ids = {}
        for key, f in (("F1", self.reg), ("F2", self.bold)):
            raw = f.subset() if f.used else f.data
            ff = zlib.compress(raw, 9)
            stream = b"<< /Length %d /Filter /FlateDecode /Length1 %d >>\nstream\n" % (len(ff), len(raw)) + ff + b"\nendstream"
            fid = add(stream)
            widths = []
            for g in sorted(f.used):
                adv = f.advances[g] if g < len(f.advances) else f.upem // 2
                widths.append("%d [%d]" % (g, int(round(adv * 1000.0 / f.upem))))
            desc = add("<< /Type /FontDescriptor /FontName /DejaVu%s /Flags 32 /FontBBox [-1021 -463 1794 1233] "
                       "/ItalicAngle 0 /Ascent %d /Descent %d /CapHeight %d /StemV 80 /FontFile2 %d 0 R >>"
                       % ("Bold" if key == "F2" else "", int(f.ascent * 1000 / f.upem),
                          int(f.descent * 1000 / f.upem), int(f.ascent * 1000 / f.upem), fid))
            # ToUnicode: обеспечивает поиск и копирование кириллицы в PDF
            pairs = sorted((g, u) for g, u in f.used.items())
            tu = ("/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CIDSystemInfo "
                  "<< /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def /CMapName /Adobe-Identity-UCS def "
                  "/CMapType 2 def 1 begincodespacerange <0000> <FFFF> endcodespacerange ")
            for i in range(0, len(pairs), 100):
                chunk = pairs[i:i + 100]
                tu += "%d beginbfchar\n" % len(chunk)
                tu += "".join("<%04X> <%04X>\n" % (g, u) for g, u in chunk)
                tu += "endbfchar\n"
            tu += "endcmap CMapName currentdict /CMap defineresource pop end end"
            tub = tu.encode("latin-1")
            tuid = add(b"<< /Length %d >>\nstream\n" % len(tub) + tub + b"\nendstream")
            cid = add("<< /Type /Font /Subtype /CIDFontType2 /BaseFont /DejaVu%s /CIDSystemInfo "
                      "<< /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /FontDescriptor %d 0 R "
                      "/DW %d /W [%s] /CIDToGIDMap /Identity >>"
                      % ("Bold" if key == "F2" else "", desc, int(f.advances[0] * 1000 / f.upem) if f.advances else 600,
                         " ".join(widths)))
            font_ids[key] = add("<< /Type /Font /Subtype /Type0 /BaseFont /DejaVu%s /Encoding /Identity-H "
                                "/DescendantFonts [%d 0 R] /ToUnicode %d 0 R >>"
                                % ("Bold" if key == "F2" else "", cid, tuid))
        page_ids = []
        stamp = self.d.created.strftime("%d.%m.%Y %H:%M")
        for i, content in enumerate(self.pages, 1):
            foot = "%s · сформировано %s" % (self.d.footer or self.d.org, stamp)
            content = content + [
                "0.75 0.79 0.83 RG 0.5 w %.2f %.2f m %.2f %.2f l S" % (self.M, self.M + 16, self.W - self.M, self.M + 16),
                "BT /F1 7.5 Tf 0.45 0.48 0.52 rg 1 0 0 1 %.2f %.2f Tm <%s> Tj ET"
                % (self.M, self.M + 5, self.reg.hexstr(foot)),
            ]
            if self.d.page_numbers:
                pn = "Стр. %d из %d" % (i, total)
                tw = self.reg.text_width(pn, 7.5)
                content.append("BT /F1 7.5 Tf 0.45 0.48 0.52 rg 1 0 0 1 %.2f %.2f Tm <%s> Tj ET"
                               % (self.W - self.M - tw, self.M + 5, self.reg.hexstr(pn)))
            data = zlib.compress("\n".join(content).encode("latin-1"), 9)
            cid = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(data) + data + b"\nendstream")
            pid = add("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] /Resources "
                      "<< /Font << /F1 %d 0 R /F2 %d 0 R >> >> /Contents %d 0 R >>"
                      % (self.W, self.H, font_ids["F1"], font_ids["F2"], cid))
            page_ids.append(pid)
        objs[1] = ("<< /Type /Catalog /Pages 2 0 R /Lang (ru-RU) >>").encode("latin-1")
        objs[2] = ("<< /Type /Pages /Count %d /Kids [%s] >>"
                   % (len(page_ids), " ".join("%d 0 R" % p for p in page_ids))).encode("latin-1")
        u16 = lambda t: "FEFF" + t.encode("utf-16-be").hex().upper()
        info = add("<< /Title <%s> /Author <%s> /Creator <%s> /Producer <%s> /CreationDate (D:%s) >>"
                   % (u16(self.d.title), u16(self.d.org), u16(APP), u16(APP + " · генератор документов"),
                      self.d.created.strftime("%Y%m%d%H%M%S")))
        out = io.BytesIO()
        out.write(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
        offsets = {}
        for n in sorted(objs):
            offsets[n] = out.tell()
            out.write(("%d 0 obj\n" % n).encode("latin-1"))
            out.write(objs[n])
            out.write(b"\nendobj\n")
        xref = out.tell()
        out.write(("xref\n0 %d\n" % (len(objs) + 1)).encode("latin-1"))
        out.write(b"0000000000 65535 f \n")
        for n in sorted(objs):
            out.write(("%010d 00000 n \n" % offsets[n]).encode("latin-1"))
        out.write(("trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
                   % (len(objs) + 1, info, xref)).encode("latin-1"))
        return out.getvalue()

def pdf(d: Document) -> bytes:
    return PDFWriter(d).build()


# ---------------------------------------------------------------- CSV / HTML / JSON

def csv_bytes(d: Document, delimiter=";") -> bytes:
    """Табличная выгрузка (Excel): BOM + разделитель ';' — корректная кириллица."""
    def esc(v):
        s = _s(v)
        return '"%s"' % s.replace('"', '""') if any(c in s for c in delimiter + '"\n') else s
    lines = []
    for sh in d.sheets():
        if sh.get("title"): lines.append(esc(sh["title"]))
        if sh.get("head"): lines.append(delimiter.join(esc(h) for h in sh["head"]))
        for r in sh["rows"]: lines.append(delimiter.join(esc(c) for c in r))
        lines.append("")
    return ("\ufeff" + "\r\n".join(lines)).encode("utf-8")

def html_bytes(d: Document) -> bytes:
    e = lambda s: _x(_s(s))
    parts = ["<!doctype html><html lang=ru><meta charset=utf-8><title>%s</title>" % e(d.title),
             "<style>body{font:14px/1.5 Arial,Helvetica,sans-serif;color:#1b2028;max-width:960px;margin:24px auto;padding:0 16px}"
             "h1{font-size:24px;color:#1f3864}h2{font-size:17px;color:#2e5496;margin-top:22px}"
             "table{border-collapse:collapse;width:100%;margin:10px 0;font-size:13px}"
             "th,td{border:1px solid #9aa5b1;padding:5px 7px;text-align:left;vertical-align:top}"
             "th{background:#e8edf3}.meta{color:#555f6d;font-size:12px}.note{color:#555f6d;font-style:italic;font-size:12px}"
             "@media print{body{margin:0}}</style>",
             "<h1>%s</h1>" % e(d.title)]
    if d.subtitle: parts.append("<p class=meta>%s</p>" % e(d.subtitle))
    if d.meta:
        parts.append("<table>" + "".join("<tr><th>%s</th><td>%s</td></tr>" % (e(k), e(v)) for k, v in d.meta) + "</table>")
    for b in d.blocks:
        t = b["t"]
        if t == "h1": parts.append("<h2>%s</h2>" % e(b["text"]))
        elif t == "h2": parts.append("<h3>%s</h3>" % e(b["text"]))
        elif t == "p": parts.append("<p>%s</p>" % e(b["text"]).replace("\n", "<br>"))
        elif t == "note": parts.append("<p class=note>%s</p>" % e(b["text"]))
        elif t == "list":
            tag = "ol" if b["numbered"] else "ul"
            parts.append("<%s>%s</%s>" % (tag, "".join("<li>%s</li>" % e(i) for i in b["items"]), tag))
        elif t == "kv":
            parts.append("<table>" + "".join("<tr><th>%s</th><td>%s</td></tr>" % (e(k), e(v)) for k, v in b["pairs"]) + "</table>")
        elif t == "table":
            if b["title"]: parts.append("<h3>%s</h3>" % e(b["title"]))
            parts.append("<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>"
                         % ("".join("<th>%s</th>" % e(h) for h in b["head"]),
                            "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % e(c) for c in r) for r in b["rows"])))
        elif t == "chart":
            parts.append("<h3>%s</h3><table><tr><th>Категория</th><th>%s</th></tr>%s</table>"
                         % (e(b["title"]), e(b["unit"] or "Значение"),
                            "".join("<tr><td>%s</td><td>%s</td></tr>" % (e(l), e(v)) for l, v in zip(b["labels"], b["values"]))))
        elif t == "sign":
            parts.append("<table>" + "".join("<tr><th>%s</th><td>____________________ %s</td></tr>" % (e(a), e(b_)) for a, b_ in b["items"]) + "</table>")
    parts.append("<p class=note>%s · сформировано %s</p>" % (e(d.org), d.created.strftime("%d.%m.%Y %H:%M")))
    parts.append("</html>")
    return "".join(parts).encode("utf-8")

MIME = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
    "csv": "text/csv; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "json": "application/json; charset=utf-8",
}

def render(d: Document, fmt: str) -> bytes:
    fmt = (fmt or "").lower()
    if fmt == "docx": return docx(d)
    if fmt == "xlsx": return xlsx(d)
    if fmt == "pdf": return pdf(d)
    if fmt == "csv": return csv_bytes(d)
    if fmt == "html": return html_bytes(d)
    raise ValueError("Неподдерживаемый формат: %s" % fmt)

def available(fmt=None):
    """Доступность форматов (для диагностики M14 и --selftest)."""
    fonts_ok = all(os.path.isfile(os.path.join(FONT_DIR, f)) for f in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"))
    return {"docx": True, "xlsx": True, "csv": True, "html": True, "pdf": fonts_ok}


# ---------------------------------------------------------------- разбор HTML-снимка отчёта (M13)

def blocks_from_html(html):
    """Сохранённый HTML-снимок отчёта → блоки единой модели документа.
       Позволяет выпускать исторические отчёты в DOCX/XLSX/PDF без пересчёта данных
       (ТЗ §24: снимок сохраняет контекст формирования)."""
    from html.parser import HTMLParser

    class P(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.blocks, self.buf, self.tag = [], [], None
            self.table, self.row, self.cell = None, None, None
            self.list_items, self.list_num = None, False
            self.skip = 0

        def flush_para(self):
            t = " ".join("".join(self.buf).split())
            self.buf = []
            if t: self.blocks.append({"t": "p", "text": t})

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if tag in ("script", "style"): self.skip += 1
            elif tag in ("h1", "h2", "h3", "h4"):
                self.flush_para(); self.tag = tag; self.buf = []
            elif tag == "table":
                self.flush_para(); self.table = {"head": [], "rows": []}
            elif tag == "tr": self.row = []
            elif tag in ("td", "th"): self.cell = []; self.cell_head = (tag == "th")
            elif tag in ("ul", "ol"):
                self.flush_para(); self.list_items, self.list_num = [], tag == "ol"
            elif tag == "li": self.buf = []
            elif tag == "br": self.buf.append("\n")
            elif tag in ("p", "div", "section") and not self.table:
                self.flush_para()

        def handle_endtag(self, tag):
            if tag in ("script", "style"): self.skip = max(0, self.skip - 1)
            elif tag in ("h1", "h2", "h3", "h4"):
                t = " ".join("".join(self.buf).split()); self.buf = []
                if t: self.blocks.append({"t": "h1" if tag in ("h1", "h2") else "h2", "text": t})
                self.tag = None
            elif tag in ("td", "th"):
                txt = " ".join("".join(self.cell or []).split())
                if self.row is not None: self.row.append(txt)
                self.cell = None
            elif tag == "tr":
                if self.table is not None and self.row:
                    if not self.table["head"] and any(c for c in self.row) and self.row_is_head:
                        self.table["head"] = self.row
                    else: self.table["rows"].append(self.row)
                self.row = None
            elif tag == "table":
                if self.table and (self.table["head"] or self.table["rows"]):
                    b = {"t": "table", "head": self.table["head"], "rows": self.table["rows"],
                         "widths": None, "aligns": [], "title": "", "total": None}
                    if not b["head"] and b["rows"]:
                        b["head"] = ["Колонка %d" % (i + 1) for i in range(len(b["rows"][0]))]
                    self.blocks.append(b)
                self.table = None
            elif tag == "li":
                t = " ".join("".join(self.buf).split()); self.buf = []
                if t and self.list_items is not None: self.list_items.append(t)
            elif tag in ("ul", "ol"):
                if self.list_items:
                    self.blocks.append({"t": "list", "items": self.list_items, "numbered": self.list_num})
                self.list_items = None
            elif tag in ("p", "div", "section") and not self.table:
                self.flush_para()

        def handle_data(self, data):
            if self.skip: return
            if self.cell is not None:
                self.cell.append(data)
                if self.cell_head: self.row_is_head = True
                return
            self.buf.append(data)

    p = P()
    p.row_is_head = False
    try:
        p.feed(html or "")
        p.flush_para()
    except Exception:
        return [{"t": "p", "text": re.sub(r"<[^>]+>", " ", html or "")}]
    return p.blocks
