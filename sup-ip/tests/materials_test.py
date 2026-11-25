#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
СУП-ИП. Испытания материалов совещания (ТТ-2, M16, экранные требования MTG-07 §10).
Запуск при работающем сервере:  python3 tests/materials_test.py --base http://127.0.0.1:8000

Проверяется: таблица материалов §10.1 (ID, название, тип, автор, дата добавления, версия,
связанный вопрос, статус, ссылка на M09), десять типов §10.2, функции §10.3 (добавить из M09
с фиксацией версии, загрузить новый файл, связать с вопросом, просмотр, скачивание, проверка
актуальности версии), неизменность комплекта при обновлении исходного документа, фильтры,
права ролей, уведомления, аудит, лимит комплекта, резервная копия.
Только стандартная библиотека Python.
"""
import argparse, datetime, http.cookiejar, importlib.util, io, json, os, sys, urllib.error, urllib.request

BASE = "http://127.0.0.1:8000"
HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(os.path.dirname(HERE), "server.py")
ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print("  ok  " + name)
    else:
        fail += 1; print("  FAIL " + name + ("  " + str(extra) if extra else ""))


def D(n):
    return (datetime.date.today() + datetime.timedelta(days=n)).isoformat()


class Client:
    def __init__(self):
        self.cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))

    def req(self, method, path, data=None):
        body = json.dumps(data).encode("utf-8") if data is not None else None
        r = urllib.request.Request(BASE + path, data=body, method=method,
                                   headers={"Content-Type": "application/json"} if body else {})
        try:
            resp = self.op.open(r, timeout=90)
            payload = resp.read()
            ct = resp.headers.get("Content-Type", "")
            return resp.getcode(), (json.loads(payload) if "json" in ct else payload)
        except urllib.error.HTTPError as e:
            payload = e.read()
            try:
                return e.code, json.loads(payload)
            except Exception:
                return e.code, payload

    def raw(self, method, path, body=b"", ctype="application/octet-stream"):
        r = urllib.request.Request(BASE + path, data=body, method=method, headers={"Content-Type": ctype})
        try:
            resp = self.op.open(r, timeout=90)
            payload = resp.read()
            ct = resp.headers.get("Content-Type", "")
            return resp.getcode(), (json.loads(payload) if "json" in ct else payload)
        except urllib.error.HTTPError as e:
            payload = e.read()
            try:
                return e.code, json.loads(payload)
            except Exception:
                return e.code, payload

    def login(self, login, password="demo"):
        return self.req("POST", "/api/login", {"login": login, "password": password})

    def rpc(self, action, payload=None):
        return self.req("POST", "/api/rpc", {"action": action, "payload": payload or {}})

    def res(self, action, payload=None):
        c, j = self.rpc(action, payload)
        return (j.get("result") if c == 200 else None), c, j

    def err(self, action, payload=None):
        c, j = self.rpc(action, payload)
        return c, ((j.get("error") or {}).get("message") if isinstance(j, dict) else "")

    def state(self):
        c, j = self.req("GET", "/api/state")
        return j.get("state") or j.get("result") or j


def meet(cli, **kw):
    p = {"title": "Материалы MTG-07", "type": "Техническое", "date": D(0), "time": "10:00",
         "chair": "u2", "secretary": "u3", "attendees": ["u2", "u3", "u5"]}
    p.update(kw)
    return cli.res("meeting.create", p)


def lst(cli, mid, **kw):
    r, c, j = cli.res("meeting.materials.list", dict(id=mid, **kw))
    return r or {}


def mats(cli, mid, **kw):
    return lst(cli, mid, **kw).get("items") or []


def one(cli, mid, name):
    return next((x for x in mats(cli, mid) if x["name"] == name), None)


def events(cli, code):
    st = cli.state()
    return [e for e in st["events"] if (e.get("details") or {}).get("event") == code]


def main():
    global BASE
    ap = argparse.ArgumentParser(); ap.add_argument("--base", default=BASE)
    BASE = ap.parse_args().base.rstrip("/")
    spec = importlib.util.spec_from_file_location("supip_materials_test", SERVER)
    srv = importlib.util.module_from_spec(spec); spec.loader.exec_module(srv)

    adm = Client()
    c, j = adm.login("admin")
    check("вход администратора", c == 200 and j.get("ok"), c)
    adm.rpc("admin.reset")
    st = adm.state()
    doc = st["docs"][0]
    did = doc["id"]
    dv = max(v["v"] for v in doc["versions"])
    doc2 = st["docs"][1]

    r, c, j = meet(adm)
    mid = (r or {}).get("id")
    check("совещание создано", c == 200 and mid, (c, j))
    for i, t in enumerate(("Рассмотрение доклада", "Приёмка расчёта", "План работ"), 1):
        adm.rpc("meeting.agenda.add", {"id": mid, "title": t, "reporter": "u5", "durationMin": 15})
    ag = (adm.res("meeting.agenda.list", {"id": mid})[0] or {}).get("items") or []
    check("повестка из трёх вопросов", len(ag) == 3, len(ag))
    a1, a2 = ag[0]["id"], ag[1]["id"]

    print("== A. Таблица материалов и справочники (§10.1, §10.2) ==")
    L = lst(adm, mid)
    check("пустой комплект материалов", L.get("items") == [], L.get("items"))
    check("справочник типов — 10 видов §10.2", len(L.get("types") or []) == 10, L.get("types"))
    for t in ("Доклад", "Презентация", "Технический отчёт", "Расчёт", "Проект решения", "Справка",
              "Протокол предыдущего совещания", "Фотография", "Схема", "Иной документ"):
        check("тип материала «%s»" % t, t in (L.get("types") or []))
    check("статусы материалов", L.get("statuses") == ["Актуален", "Требует обновления", "Устарел"], L.get("statuses"))
    check("источники материалов", set(L.get("kinds") or {}) == {"doc", "file", "link"}, L.get("kinds"))
    check("сводка комплекта", (L.get("stats") or {}).get("total") == 0, L.get("stats"))
    check("список вопросов для связывания", len(L.get("agenda") or []) == 3, L.get("agenda"))

    print("== B. Добавление из M09 с фиксацией версии (§10.3) ==")
    r, c, j = adm.res("meeting.materials.add", {"id": mid, "docId": did, "type": "Доклад",
                                                "author": "u5", "itemId": a1, "note": "К вопросу 1"})
    check("материал из M09 добавлен", c == 200, (c, j))
    x = one(adm, mid, "%s · %s" % (doc["code"], doc["title"]))
    check("материал найден в таблице", bool(x))
    check("идентификатор материала присвоен", bool(x and x["id"]))
    check("тип материала сохранён", x and x["type"] == "Доклад", x and x["type"])
    check("автор материала сохранён", x and x["author"] == "u5", x and x["author"])
    check("имя автора разрешается", x and x["authorName"] and "Новиков" in x["authorName"], x and x["authorName"])
    check("дата добавления зафиксирована", x and isinstance(x["addedAt"], int) and x["addedAt"] > 0)
    check("версия документа зафиксирована", x and x["docV"] == dv, (x and x["docV"], dv))
    check("ссылка на M09 в таблице", x and x["docCode"] == doc["code"], x and x["docCode"])
    check("связанный вопрос сохранён", x and x["itemId"] == a1 and x["itemN"] == 1, x and (x["itemId"], x["itemN"]))
    check("наименование вопроса в таблице", x and x["itemTitle"] == "Рассмотрение доклада")
    check("статус «Актуален»", x and x["status"] == "Актуален")
    check("материал актуален", x and x["actual"] is True)
    check("история материала начата", x and len(x["history"]) >= 1)
    c, msg = adm.err("meeting.materials.add", {"id": mid, "docId": did})
    check("повторное добавление того же документа отклонено", c == 400 and "уже прикреплён" in msg, (c, msg))
    c, msg = adm.err("meeting.materials.add", {"id": mid, "docId": doc2["id"], "docV": 99})
    check("несуществующая версия документа отклонена", c == 404, (c, msg))
    c, msg = adm.err("meeting.materials.add", {"id": mid, "docId": doc2["id"], "type": "Неизвестный"})
    check("неизвестный тип материала отклонён", c == 400 and "тип материала" in msg, (c, msg))
    c, msg = adm.err("meeting.materials.add", {"id": mid, "docId": doc2["id"], "author": "u_none"})
    check("неизвестный автор отклонён", c == 400 and "Автор" in msg, (c, msg))
    c, msg = adm.err("meeting.materials.add", {"id": mid, "docId": doc2["id"], "itemId": "aq_none"})
    check("неизвестный вопрос отклонён", c == 404, (c, msg))
    r, c, j = adm.res("meeting.materials.add", {"id": mid, "docId": doc2["id"], "docV": 1, "type": "Расчёт"})
    check("явная версия документа фиксируется", c == 200)
    x2 = one(adm, mid, "%s · %s" % (doc2["code"], doc2["title"]))
    check("зафиксирована версия 1", x2 and x2["docV"] == 1, x2 and x2["docV"])

    print("== C. Загрузка нового файла (§10.3) ==")
    r, c, j = adm.res("meeting.materials.add", {"id": mid, "fileName": "doklad.pdf",
                                                "name": "Доклад PDF", "type": "Презентация", "author": "u3"})
    check("материал-файл создан", c == 200, (c, j))
    fm = one(adm, mid, "Доклад PDF")
    check("источник «Загруженный файл»", fm and fm["kind"] == "file", fm and fm["kind"])
    check("файл ещё не загружен", fm and fm["stored"] is False)
    check("неактуален до загрузки файла", fm and fm["actual"] is False)
    check("имя файла сохранено", fm and fm["fileName"] == "doklad.pdf")
    body = b"%PDF-1.4 material MTG-07\n" + b"x" * 2048
    c, j = adm.raw("POST", "/api/mfiles/%s/%s/1?name=doklad.pdf" % (mid, fm["id"]), body)
    check("файл материала загружен на сервер", c == 200 and j.get("ok"), (c, j))
    fm = one(adm, mid, "Доклад PDF")
    check("признак загрузки установлен", fm and fm["stored"] is True)
    check("размер файла зафиксирован", fm and fm["size"] == len(body), fm and fm["size"])
    check("материал стал актуальным", fm and fm["actual"] is True)
    c, data = adm.raw("GET", "/api/mfiles/%s/%s/1" % (mid, fm["id"]))
    check("файл скачивается", c == 200 and isinstance(data, bytes), c)
    check("содержимое файла совпадает", data == body, len(data) if isinstance(data, bytes) else data)
    c, j = adm.raw("GET", "/api/mfiles/%s/%s/9" % (mid, fm["id"]))
    check("несуществующая версия материала — 404", c == 404, c)
    c, j = adm.raw("POST", "/api/mfiles/%s/%s/1" % (mid, fm["id"]), b"")
    check("пустой файл отклонён", c == 400, (c, j))
    c, j = adm.raw("POST", "/api/mfiles/%s/%s/1" % (mid, x["id"]), b"data")
    check("загрузка в материал из M09 отклонена", c == 400, (c, j))
    c, j = adm.raw("GET", "/api/mfiles/%s/%s/1" % (mid, "mm_none"))
    check("несуществующий материал — 404", c == 404, c)

    print("== D. Ссылка ЛВС (§10.3) ==")
    r, c, j = adm.res("meeting.materials.add", {"id": mid, "name": "Регламент ЛВС",
                                                "url": "file://srv/share/reglament.pdf", "type": "Справка"})
    check("ссылка добавлена", c == 200, (c, j))
    lm = one(adm, mid, "Регламент ЛВС")
    check("источник «Ссылка ЛВС»", lm and lm["kind"] == "link")
    check("адрес ссылки сохранён", lm and lm["url"] == "file://srv/share/reglament.pdf")
    check("ссылка актуальна", lm and lm["actual"] is True)
    c, msg = adm.err("meeting.materials.add", {"id": mid, "name": "Дубль", "url": "file://srv/share/reglament.pdf"})
    check("повторная ссылка отклонена", c == 400 and "уже прикреплена" in msg, (c, msg))
    c, msg = adm.err("meeting.materials.add", {"id": mid, "name": "X", "url": "javascript:alert(1)"})
    check("схема javascript отклонена", c == 400 and "схема" in msg.lower(), (c, msg))
    c, msg = adm.err("meeting.materials.add", {"id": mid, "name": "", "url": "file://srv/a"})
    check("пустое название отклонено", c == 400, (c, msg))

    print("== E. Реквизиты материала (§10.1) ==")
    r, c, j = adm.res("meeting.materials.update", {"id": mid, "materialId": lm["id"], "name": "Регламент (ред. 2)",
                                                   "type": "Иной документ", "author": "u2",
                                                   "status": "Устарел", "note": "Заменён"})
    check("реквизиты материала изменены", c == 200, (c, j))
    lm2 = one(adm, mid, "Регламент (ред. 2)")
    check("название изменено", bool(lm2))
    check("тип изменён", lm2 and lm2["type"] == "Иной документ")
    check("автор изменён", lm2 and lm2["author"] == "u2")
    check("статус «Устарел» установлен вручную", lm2 and lm2["status"] == "Устарел")
    check("примечание сохранено", lm2 and lm2["note"] == "Заменён")
    c, msg = adm.err("meeting.materials.update", {"id": mid, "materialId": lm["id"], "name": "Регламент (ред. 2)",
                                                  "type": "Иной документ", "author": "u2",
                                                  "status": "Устарел", "note": "Заменён"})
    check("повторное изменение без изменений отклонено", c == 400 and "не изменились" in msg, (c, msg))
    c, msg = adm.err("meeting.materials.update", {"id": mid, "materialId": lm["id"], "type": "Докладчик"})
    check("неизвестный тип при изменении отклонён", c == 400, (c, msg))
    c, msg = adm.err("meeting.materials.update", {"id": mid, "materialId": lm["id"], "status": "Готов"})
    check("неизвестный статус отклонён", c == 400, (c, msg))
    c, msg = adm.err("meeting.materials.update", {"id": mid, "materialId": lm["id"], "name": "  "})
    check("пустое название при изменении отклонено", c == 400, (c, msg))
    c, msg = adm.err("meeting.materials.update", {"id": mid, "materialId": "mm_none", "name": "X"})
    check("несуществующий материал — 404", c == 404, (c, msg))
    c, msg = adm.err("meeting.materials.update", {"id": mid})
    check("без идентификатора материала — отказ", c == 400, (c, msg))

    print("== F. Связывание с вопросом повестки (§10.3) ==")
    r, c, j = adm.res("meeting.materials.link", {"id": mid, "materialId": lm["id"], "itemId": a2})
    check("материал связан с вопросом 2", c == 200, (c, j))
    lm3 = one(adm, mid, "Регламент (ред. 2)")
    check("связь сохранена", lm3 and lm3["itemId"] == a2 and lm3["itemN"] == 2)
    c, msg = adm.err("meeting.materials.link", {"id": mid, "materialId": lm["id"], "itemId": a2})
    check("повторное связывание с тем же вопросом отклонено", c == 400, (c, msg))
    c, msg = adm.err("meeting.materials.link", {"id": mid, "materialId": lm["id"], "itemId": "aq_none"})
    check("связывание с несуществующим вопросом — 404", c == 404, (c, msg))
    r, c, j = adm.res("meeting.materials.link", {"id": mid, "materialId": lm["id"], "itemId": ""})
    check("связь снимается", c == 200 and (r or {}).get("material", {}).get("itemId") == "", (c, j))
    c, msg = adm.err("meeting.materials.link", {"id": mid, "materialId": lm["id"], "itemId": ""})
    check("повторное снятие связи отклонено", c == 400, (c, msg))
    adm.rpc("meeting.materials.link", {"id": mid, "materialId": fm["id"], "itemId": a1})
    check("файл связан с вопросом 1", one(adm, mid, "Доклад PDF")["itemN"] == 1)

    print("== G. Актуальность версии при обновлении M09 (§10.3) ==")
    before = one(adm, mid, "%s · %s" % (doc["code"], doc["title"]))
    r, c, j = adm.res("doc.version", {"id": did, "fileName": "novaya-redakciya.docx", "note": "Правки"})
    check("новая версия документа M09 зарегистрирована", c == 200, (c, j))
    newv = max(v["v"] for v in (r or {}).get("versions", [{"v": dv}]))
    after = one(adm, mid, "%s · %s" % (doc["code"], doc["title"]))
    check("зафиксированная версия НЕ изменилась", after and after["docV"] == before["docV"], (after and after["docV"], before["docV"]))
    check("материал помечен неактуальным", after and after["actual"] is False)
    check("в таблице видна новая версия M09", after and after["latestV"] == newv, after and after["latestV"])
    r, c, j = adm.res("meeting.materials.check", {"id": mid})
    check("проверка актуальности выполнена", c == 200, (c, j))
    check("отчёт содержит все материалы", len((r or {}).get("items") or []) == 4, len((r or {}).get("items") or []))
    need = len([y for y in mats(adm, mid) if not y["actual"]])
    check("число требующих обновления", (r or {}).get("outdated") == need, ((r or {}).get("outdated"), need))
    after = one(adm, mid, "%s · %s" % (doc["code"], doc["title"]))
    check("статус «Требует обновления» сохранён", after and after["status"] == "Требует обновления", after and after["status"])
    check("ручной статус «Устарел» не сброшен", one(adm, mid, "Регламент (ред. 2)")["status"] == "Устарел")
    c, msg = adm.err("meeting.materials.refresh", {"id": mid, "materialId": fm["id"]})
    check("обновление версии для файла отклонено", c == 400 and "M09" in msg, (c, msg))
    c, msg = adm.err("meeting.materials.refresh", {"id": mid, "materialId": after["id"], "version": 99})
    check("обновление на несуществующую версию — 404", c == 404, (c, msg))
    r, c, j = adm.res("meeting.materials.refresh", {"id": mid, "materialId": after["id"]})
    check("версия материала обновлена явно", c == 200, (c, j))
    after2 = one(adm, mid, "%s · %s" % (doc["code"], doc["title"]))
    check("зафиксирована новая версия", after2 and after2["docV"] == newv, after2 and after2["docV"])
    check("статус снова «Актуален»", after2 and after2["status"] == "Актуален")
    check("версия материала увеличена", after2 and after2["v"] == 2, after2 and after2["v"])
    check("история фиксирует переход версий", after2 and any(h["action"] == "refresh" for h in after2["history"]))
    c, msg = adm.err("meeting.materials.refresh", {"id": mid, "materialId": after2["id"]})
    check("повторное обновление на ту же версию отклонено", c == 400 and "уже зафиксирован" in msg, (c, msg))
    r, c, j = adm.res("meeting.materials.refresh", {"id": mid, "materialId": after2["id"], "version": 1})
    check("возврат к прежней версии документа возможен", c == 200, (c, j))
    check("зафиксирована версия 1", one(adm, mid, "%s · %s" % (doc["code"], doc["title"]))["docV"] == 1)
    adm.rpc("meeting.materials.refresh", {"id": mid, "materialId": after2["id"]})

    print("== H. Фильтры таблицы материалов ==")
    check("фильтр по типу", len(mats(adm, mid, type="Презентация")) == 1)
    check("фильтр по статусу", len(mats(adm, mid, status="Устарел")) == 1)
    check("фильтр по источнику doc", len(mats(adm, mid, kind="doc")) == 2)
    check("фильтр по источнику file", len(mats(adm, mid, kind="file")) == 1)
    check("фильтр по источнику link", len(mats(adm, mid, kind="link")) == 1)
    check("фильтр по вопросу", len(mats(adm, mid, itemId=a1)) == 2)
    check("фильтр «без вопроса»", len(mats(adm, mid, unlinked=True)) == 2)
    check("поиск по названию", len(mats(adm, mid, q="регламент")) == 1)
    check("поиск по примечанию", len(mats(adm, mid, q="К вопросу 1")) == 1)
    c, msg = adm.err("meeting.materials.list", {"id": mid, "type": "Неизвестный"})
    check("фильтр с неизвестным типом отклонён", c == 400, (c, msg))
    c, msg = adm.err("meeting.materials.list", {"id": mid, "kind": "ftp"})
    check("фильтр с неизвестным источником отклонён", c == 400, (c, msg))
    S = lst(adm, mid).get("stats") or {}
    check("сводка: всего 4", S.get("total") == 4, S)
    check("сводка: документы 2", S.get("docs") == 2, S)
    check("сводка: файлы 1", S.get("files") == 1, S)
    check("сводка: ссылки 1", S.get("links") == 1, S)
    check("сводка: связаны с вопросами 2", S.get("linked") == 2, S)

    print("== I. Права ролей ==")
    for login, role in (("volkova", "руководитель проектов"), ("novikov", "исполнитель"), ("zaytseva", "наблюдатель")):
        cli = Client(); cli.login(login)
        c, j = cli.rpc("meeting.materials.list", {"id": mid})
        check("%s: просмотр материалов разрешён" % role, c == 200, c)
    exe = Client(); exe.login("novikov")
    for act, payload in (("meeting.materials.add", {"id": mid, "name": "X", "url": "file://srv/x"}),
                         ("meeting.materials.update", {"id": mid, "materialId": lm["id"], "name": "Y"}),
                         ("meeting.materials.link", {"id": mid, "materialId": lm["id"], "itemId": a1}),
                         ("meeting.materials.refresh", {"id": mid, "materialId": after2["id"], "version": 1}),
                         ("meeting.materials.remove", {"id": mid, "materialId": lm["id"]})):
        c, j = exe.rpc(act, payload)
        check("исполнитель: отказ в %s" % act.split(".")[-1], c == 403, (c, j))
    obs = Client(); obs.login("zaytseva")
    c, j = obs.rpc("meeting.materials.add", {"id": mid, "name": "X", "url": "file://srv/y"})
    check("наблюдатель: отказ в добавлении", c == 403, c)
    c, data = obs.raw("GET", "/api/mfiles/%s/%s/1" % (mid, fm["id"]))
    check("наблюдатель: скачивание материала разрешено", c == 200 and isinstance(data, bytes), c)
    c, j = obs.raw("POST", "/api/mfiles/%s/%s/1" % (mid, fm["id"]), b"hack")
    check("наблюдатель: загрузка файла запрещена", c == 403, c)
    pm = Client(); pm.login("volkova")
    c, j = pm.rpc("meeting.materials.add", {"id": mid, "name": "Материал РП", "url": "file://srv/pm", "type": "Схема"})
    check("руководитель проектов: материал добавлен", c == 200, (c, j))

    print("== K. Аудит и уведомления ==")
    for ev in ("MEETING_MATERIAL_ADDED", "MEETING_MATERIAL_UPDATED", "MEETING_MATERIAL_LINKED",
               "MEETING_MATERIAL_VERSION_CHECKED", "MEETING_MATERIAL_VERSION_REFRESHED",
               "MEETING_MATERIAL_FILE_UPLOADED"):
        check("событие %s зарегистрировано" % ev, len(events(adm, ev)) >= 1, len(events(adm, ev)))
    st = adm.state()
    check("отказ прав записан в журнал", any(e.get("cat") == "SECURITY" and "Отказано" in (e.get("action") or "")
                                            for e in st["events"]))
    u5 = Client(); u5.login("novikov")
    n5 = [x for x in u5.state()["notifications"] if x["type"] == "meeting.materials"]
    check("участник оповещён о материалах", len(n5) >= 1, len(n5))
    check("уведомление содержит код совещания", any("СОВ-" in (x.get("body") or "") for x in n5))

    print("== L. Лимит комплекта и удаление ==")
    r, c, j = adm.res("meeting.materials.remove", {"id": mid, "materialId": lm["id"]})
    check("материал откреплён", c == 200, (c, j))
    check("в таблице осталось 4", len(mats(adm, mid)) == 4, len(mats(adm, mid)))
    c, msg = adm.err("meeting.materials.remove", {"id": mid, "materialId": "mm_none"})
    check("удаление несуществующего материала — отказ", c == 400, (c, msg))
    check("событие удаления зарегистрировано", len(events(adm, "MEETING_MATERIAL_REMOVED")) >= 1)
    r, c, j = meet(adm, title="Лимит материалов")
    mid2 = (r or {}).get("id")
    for i in range(srv.MEET_MATERIALS_MAX):
        adm.rpc("meeting.materials.add", {"id": mid2, "name": "Материал %d" % i, "url": "file://srv/m%d" % i})
    check("комплект заполнен до предела", len(mats(adm, mid2)) == srv.MEET_MATERIALS_MAX, len(mats(adm, mid2)))
    c, msg = adm.err("meeting.materials.add", {"id": mid2, "name": "Лишний", "url": "file://srv/extra"})
    check("превышение лимита комплекта отклонено", c == 400 and "не более" in msg, (c, msg))

    print("== M. Резервная копия ==")
    c, raw = adm.req("GET", "/api/backup")
    bk = json.loads(raw if isinstance(raw, (bytes, str)) else json.dumps(raw))
    row = [x for x in (bk.get("tables") or {}).get("meetings", []) if x["id"] == mid]
    check("копия содержит материалы совещания", c == 200 and row and len(row[0]["materials"]) == 4, c)
    check("в копии сохранены тип и вопрос", row and all("type" in x and "itemId" in x for x in row[0]["materials"]))
    check("в копии сохранена зафиксированная версия", row and any(x.get("docV") for x in row[0]["materials"]))
    c, j = adm.req("POST", "/api/restore", bk)
    check("восстановление из копии выполнено", c == 200, (c, j))
    adm.login("admin")
    back = mats(adm, mid)
    check("после восстановления материалы сохранены", len(back) == 4, len(back))
    check("после восстановления связи с вопросами сохранены", len([x for x in back if x.get("itemId")]) == 2)
    c, data = adm.raw("GET", "/api/mfiles/%s/%s/1" % (mid, fm["id"]))
    check("файл материала доступен после восстановления", c == 200 and data == body, c)

    adm.rpc("admin.reset")
    print("\nИТОГ: %d ok, %d FAIL" % (ok, fail))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
