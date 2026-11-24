#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
СУП-ИП. Испытания выгрузки документов (ТЗ §25 «Форматы документов», §2.8 M16).
Запуск при работающем сервере:  python3 tests/export_test.py --base http://127.0.0.1:8000
Проверяется: DOCX/XLSX/PDF/CSV/HTML по всем видам документов, серверная проверка прав
(отказ 403 + событие SECURITY), регистрация протокола совещания в M09 и скачивание
обоих представлений версии (DOCX + PDF), аудит выгрузок в M15.
Только стандартная библиотека Python.
"""
import argparse, io, json, re, sys, urllib.request, urllib.error, http.cookiejar, zipfile

BASE = "http://127.0.0.1:8000"
ok = fail = 0

def check(name, cond, extra=""):
    global ok, fail
    if cond: ok += 1; print("  ok  " + name)
    else: fail += 1; print("  FAIL " + name + ("  " + str(extra) if extra else ""))

class Client:
    def __init__(self):
        self.cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))
    def post(self, path, data):
        r = urllib.request.Request(BASE + path, data=json.dumps(data).encode("utf-8"),
                                   headers={"Content-Type": "application/json"})
        try:
            resp = self.op.open(r, timeout=120)
            return resp.getcode(), json.load(resp)
        except urllib.error.HTTPError as e:
            try: return e.code, json.load(e)
            except Exception: return e.code, {}
    def raw(self, path):
        try:
            resp = self.op.open(BASE + path, timeout=180)
            return resp.getcode(), resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)
    def raw_post(self, path, data):
        r = urllib.request.Request(BASE + path, data=json.dumps(data).encode("utf-8"),
                                   headers={"Content-Type": "application/json"})
        try:
            resp = self.op.open(r, timeout=180)
            return resp.getcode(), resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)
    def login(self, login, password="demo"):
        c, j = self.post("/api/login", {"login": login, "password": password})
        return c, j
    def rpc(self, action, payload=None):
        return self.post("/api/rpc", {"action": action, "payload": payload or {}})

def zip_names(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z: return z.namelist()

def main():
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    a = ap.parse_args()
    BASE = a.base.rstrip("/")

    admin = Client()
    c, j = admin.login("admin")
    check("вход администратора", c == 200 and j.get("ok"), c)
    st = j["state"]
    pid = st["projects"][0]["id"]
    mid = st["meetings"][0]["id"]
    cid = st["calcs"][0]["id"]

    print("== Доступность форматов ==")
    code, data, _ = admin.raw("/api/formats")
    fmts = json.loads(data)["formats"]
    check("PDF доступен (встроенные шрифты)", fmts.get("pdf") is True, fmts)
    check("DOCX и XLSX доступны", fmts.get("docx") and fmts.get("xlsx"))

    print("== Выгрузка документов по видам (DOCX / XLSX / PDF) ==")
    kinds = [("portfolio", ""), ("project", pid), ("tasks", ""), ("gantt", pid), ("resources", ""),
             ("time", ""), ("risks", ""), ("calc", cid), ("meeting", mid), ("journal", "")]
    sizes = {}
    for kind, id_ in kinds:
        for fmt in ("docx", "xlsx", "pdf"):
            code, data, hdr = admin.raw("/api/export?kind=%s&id=%s&fmt=%s" % (kind, id_, fmt))
            sig = data[:2] == b"PK" if fmt in ("docx", "xlsx") else data[:4] == b"%PDF"
            check("%s → %s" % (kind, fmt.upper()), code == 200 and sig and len(data) > 1500,
                  "%s %d байт" % (code, len(data)))
            sizes[(kind, fmt)] = data
            disp = hdr.get("Content-Disposition", "")
            if fmt == "docx":
                check("%s: имя файла в заголовке" % kind, "filename*=UTF-8''" in disp and disp.endswith(".docx"), disp)

    print("== Регрессия: график работ с зависимостями и поручение без срока ==")
    dep_proj = next((p["id"] for p in st["projects"]
                     if any(t["projectId"] == p["id"] and t["deps"] for t in st["tasks"])), None)
    check("проект с зависимостями найден", bool(dep_proj), dep_proj)
    for fmt in ("docx", "xlsx", "pdf"):
        code, data, hdr = admin.raw("/api/export?kind=gantt&id=%s&fmt=%s" % (dep_proj, fmt))
        sig = data[:2] == b"PK" if fmt in ("docx", "xlsx") else data[:4] == b"%PDF"
        check("график работ с зависимостями → %s" % fmt.upper(), code == 200 and sig and len(data) > 1500,
              "%s %d байт" % (code, len(data)))
    code, data, hdr = admin.raw("/api/export?kind=gantt&fmt=csv")
    check("график работ всех проектов → CSV", code == 200 and len(data) > 200, "%s %d байт" % (code, len(data)))

    print("== Структура DOCX (ТЗ §25.1) ==")
    names = zip_names(sizes[("meeting", "docx")])
    check("DOCX: стили заголовков", "word/styles.xml" in names)
    check("DOCX: автоматическая нумерация", "word/numbering.xml" in names)
    check("DOCX: колонтитул", "word/header1.xml" in names)
    check("DOCX: номера страниц в подвале", "word/footer1.xml" in names)
    with zipfile.ZipFile(io.BytesIO(sizes[("meeting", "docx")])) as z:
        ftr = z.read("word/footer1.xml").decode("utf-8")
        doc = z.read("word/document.xml").decode("utf-8")
        sty = z.read("word/styles.xml").decode("utf-8")
        num = z.read("word/numbering.xml").decode("utf-8")
    check("DOCX: поля PAGE и NUMPAGES", "PAGE" in ftr and "NUMPAGES" in ftr)
    check("DOCX: оглавление (поле TOC)", "TOC" in doc)
    check("DOCX: таблица с повторяющейся шапкой", "<w:tblHeader/>" in doc)
    check("DOCX: кириллица в тексте", "Протокол совещания" in doc)
    check("DOCX: стили Heading1/Heading2", "Heading1" in sty and "Heading2" in sty)
    check("DOCX: нумерованный список decimal", 'w:numFmt w:val="decimal"' in num)

    print("== Структура XLSX (ТЗ §25.3) ==")
    with zipfile.ZipFile(io.BytesIO(sizes[("portfolio", "xlsx")])) as z:
        names = z.namelist()
        sheets = [n for n in names if n.startswith("xl/worksheets/sheet")]
        s1 = z.read(sheets[0]).decode("utf-8")
        styles = z.read("xl/styles.xml").decode("utf-8")
    check("XLSX: несколько листов", len(sheets) >= 2, sheets)
    check("XLSX: автофильтр", "<autoFilter" in s1)
    check("XLSX: закрепление шапки", 'state="frozen"' in s1)
    check("XLSX: форматы дат и чисел", "dd.mm.yyyy" in styles and "#,##0.00" in styles)
    check("XLSX: источник и дата формирования", "Источник:" in s1 and "сформировано" in s1)
    check("XLSX: итоговые формулы", "<f>SUM(" in s1)
    with zipfile.ZipFile(io.BytesIO(sizes[("portfolio", "xlsx")])) as z:
        all_sheets = "".join(z.read(n).decode("utf-8") for n in z.namelist() if n.startswith("xl/worksheets/sheet"))
    check("XLSX: пустые значения не подменяются нулями", re.search(r'<c r="[A-Z]+\d+" s="6"/>', all_sheets) is not None)

    print("== Структура PDF (ТЗ §25.2) ==")
    pdf = sizes[("meeting", "pdf")]
    check("PDF: версия 1.7", pdf.startswith(b"%PDF-1.7"))
    check("PDF: встроенный шрифт TrueType", b"/FontFile2" in pdf and b"/CIDFontType2" in pdf)
    check("PDF: кириллица (Identity-H + ToUnicode)", b"/Identity-H" in pdf and b"/ToUnicode" in pdf)
    check("PDF: несколько страниц с нумерацией", pdf.count(b"/Type /Page ") >= 1 and b"/Count" in pdf)
    import zlib
    streams = []
    for m in re.finditer(rb"/Filter /FlateDecode >>\nstream\n", pdf):
        start = m.end()
        end = pdf.find(b"\nendstream", start)
        try: streams.append(zlib.decompress(pdf[start:end]))
        except Exception: pass
    content = b"\n".join(s for s in streams if b" Tj" in s or b" re" in s)
    check("PDF: таблица (границы ячеек)", b" re S" in content)
    pf = sizes[("portfolio", "pdf")]
    pst = []
    for m in re.finditer(rb"/Filter /FlateDecode >>\nstream\n", pf):
        start = m.end(); end = pf.find(b"\nendstream", start)
        try: pst.append(zlib.decompress(pf[start:end]))
        except Exception: pass
    pcontent = b"\n".join(x for x in pst if b" Tj" in x)
    check("PDF: векторная диаграмма (столбцы)", b" re f" in pcontent)
    check("PDF: векторная круговая диаграмма (сегменты)", b" l" in pcontent and b" m" in pcontent and pcontent.count(b" f") > 3)
    check("PDF: текст кириллицей в контенте", b" Tj" in content and b"/F1" in content)
    check("PDF: метаданные документа", b"/Title <FEFF" in pdf)

    print("== Прочие форматы ==")
    code, data, _ = admin.raw("/api/export?kind=tasks&fmt=csv")
    check("CSV: BOM и разделитель ';'", code == 200 and data[:3] == b"\xef\xbb\xbf" and b";" in data)
    code, data, _ = admin.raw("/api/export?kind=tasks&fmt=html")
    check("HTML: автономный документ", code == 200 and b"<!doctype html>" in data and b"http" not in data.split(b"<style>")[0])
    code, data, _ = admin.raw("/api/export?kind=tasks&fmt=exe")
    check("недопустимый формат → 400", code == 400, code)
    code, data, _ = admin.raw("/api/export?kind=unknown&fmt=pdf")
    check("неизвестный вид документа → 404", code == 404, code)

    print("== Панель M17 → PDF/XLSX (данные виджетов) ==")
    widgets = [{"title": "Активные проекты", "type": "kpi", "value": 4},
               {"title": "Прогресс", "type": "bar", "labels": ["РЛ-240", "МП-12"], "values": [62, 35], "unit": "%"},
               {"title": "Задачи", "type": "html", "html": "<table><tr><th>Код</th><th>Задача</th></tr><tr><td>З-001</td><td>Расчёт</td></tr></table>"}]
    for fmt in ("pdf", "xlsx", "docx"):
        code, data, _ = admin.raw_post("/api/export", {"kind": "dashboard", "fmt": fmt, "params": {"widgets": widgets, "title": "Панель руководителя"}})
        sig = data[:4] == b"%PDF" if fmt == "pdf" else data[:2] == b"PK"
        check("панель M17 → %s" % fmt.upper(), code == 200 and sig, code)

    print("== Протокол совещания → M09 (ТЗ §2.8, §2.12) ==")
    c, j = admin.rpc("meeting.protocol", {"id": mid, "formats": ["docx", "pdf"]})
    res = j.get("result") or {}
    check("протокол сформирован", c == 200 and j.get("ok") and res.get("docId"), j.get("error"))
    did, ver = res.get("docId"), res.get("version")
    check("две формы представления (DOCX + PDF)", len(res.get("files") or []) == 2)
    st = j["state"]
    doc = next((x for x in st["docs"] if x["id"] == did), None)
    check("документ зарегистрирован в M09", doc is not None and doc["type"] == "Протокол", doc)
    check("версия сохранена в хранилище", doc and any(v["v"] == ver and v["stored"] for v in doc["versions"]))
    code, data, hdr = admin.raw("/api/files/%s/%d" % (did, ver))
    check("скачивание DOCX версии", code == 200 and data[:2] == b"PK", code)
    code, data, hdr = admin.raw("/api/files/%s/%d?fmt=pdf" % (did, ver))
    check("скачивание PDF той же версии", code == 200 and data[:4] == b"%PDF", code)
    c, j = admin.rpc("meeting.protocol", {"id": mid, "formats": ["docx", "pdf"]})
    check("повторное формирование → новая версия", j["result"]["version"] == ver + 1, j["result"])

    print("== Права доступа и аудит ==")
    obs = Client()
    c, j = obs.login("zaytseva")
    check("вход наблюдателя", c == 200)
    code, data, _ = obs.raw("/api/export?kind=journal&fmt=xlsx")
    check("наблюдателю журнал запрещён (403)", code == 403, code)
    code, data, _ = obs.raw("/api/export?kind=calc&id=%s&fmt=pdf" % cid)
    check("наблюдателю расчёты запрещены (403)", code == 403, code)
    code, data, _ = obs.raw("/api/export?kind=portfolio&fmt=pdf")
    check("наблюдателю выгрузка портфеля запрещена матрицей прав (403)", code == 403, code)
    pm = Client()
    c, j = pm.login("volkova")
    check("вход руководителя проекта", c == 200)
    code, data, _ = pm.raw("/api/export?kind=portfolio&fmt=pdf")
    check("руководителю проекта выгрузка портфеля разрешена", code == 200 and data[:4] == b"%PDF", code)
    code, data, _ = pm.raw("/api/export?kind=journal&fmt=xlsx")
    check("руководителю проекта журнал запрещён (403)", code == 403, code)
    code, data, _ = admin.raw("/api/export?kind=tasks&fmt=xlsx")
    c, j = admin.rpc("client.log", {"cat": "INTEGRATION", "module": "tasks", "action": "проверка"})
    events = j["state"]["events"]
    check("отказ зафиксирован как SECURITY", any(e["cat"] == "SECURITY" and "Отказано в выгрузке" in e["action"] for e in events))
    check("выгрузка зафиксирована как BUSINESS", any(e["cat"] == "BUSINESS" and "Выгружен документ" in e["action"] for e in events))
    check("протокол зафиксирован в M15", any("Сформирован протокол совещания" in e["action"] for e in events))
    anon = Client()
    code, data, _ = anon.raw("/api/export?kind=portfolio&fmt=pdf")
    check("без сессии → 401", code == 401, code)

    print("\nИТОГ: %d ok, %d FAIL" % (ok, fail))
    sys.exit(1 if fail else 0)

if __name__ == "__main__":
    main()
