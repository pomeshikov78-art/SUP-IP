#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
СУП-ИП. Испытания формирования протокола и библиотеки шаблонов (ТЗ M16 §2.8, MTG-09/MTG-10).
Запуск при работающем сервере:  python3 tests/templates_test.py --base http://127.0.0.1:8000
Проверяется: 6 базовых шаблонов (§2.8.1), каталог и конструктор шаблонов (блоки, переменные
meeting.*, версии, копирование, признак «по умолчанию», архив и восстановление версии),
реквизиты протокола (§2.8.2), редактор черновика (автозаполнение, ручная правка, добавление
и удаление разделов, приложения, заключение), рассмотрение вопросов (обсуждение, замечания,
проблемы), предпросмотр, выпуск DOCX/PDF в M09, неизменность ранее выпущенных протоколов,
права, аудит, журнал и сохранность данных в резервной копии.
В начале и в конце испытаний восстанавливается эталонный набор (admin.reset).
Только стандартная библиотека Python.
"""
import argparse, datetime, http.cookiejar, importlib.util, io, json, os, sys, urllib.error, urllib.request, zipfile

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

    def req(self, method, path, data=None, raw=False):
        body = json.dumps(data).encode("utf-8") if data is not None else None
        r = urllib.request.Request(BASE + path, data=body, method=method,
                                   headers={"Content-Type": "application/json"} if body else {})
        try:
            resp = self.op.open(r, timeout=120)
            payload = resp.read()
            return resp.getcode(), (payload if raw else json.loads(payload))
        except urllib.error.HTTPError as e:
            payload = e.read()
            if raw: return e.code, payload
            try: return e.code, json.loads(payload)
            except Exception: return e.code, {}

    def login(self, login, password="demo"):
        c, j = self.req("POST", "/api/login", {"login": login, "password": password})
        return c, j

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
    p = {"title": "Совещание §2.8", "type": "Техническое", "date": D(0), "time": "11:00",
         "chair": "u2", "secretary": "u3", "attendees": ["u2", "u3", "u5", "u6"]}
    p.update(kw)
    return cli.res("meeting.create", p)


def tpls(cli, **kw):
    r, c, j = cli.res("template.list", kw)
    return (r or {}).get("items") or []


def tpl(cli, tid):
    r, c, j = cli.res("template.get", {"id": tid})
    return r or {}


def draft(cli, mid):
    r, c, j = cli.res("meeting.protocol.draft.get", {"id": mid})
    return r or {}


def secs(cli, mid):
    return draft(cli, mid).get("sections") or []


def sec(cli, mid, key):
    return next((s for s in secs(cli, mid) if s["key"] == key), None)


def preview(cli, mid, html=False):
    r, c, j = cli.res("meeting.protocol.preview", {"id": mid, "html": html})
    return r or {}


def docx_text(cli, mid):
    c, raw = cli.req("GET", "/api/export?kind=meeting&id=%s&fmt=docx" % mid, raw=True)
    if c == 200 and isinstance(raw, bytes) and raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            return z.read("word/document.xml").decode("utf-8")
    return ""


def main():
    global BASE
    ap = argparse.ArgumentParser(); ap.add_argument("--base", default=BASE)
    BASE = ap.parse_args().base.rstrip("/")

    spec = importlib.util.spec_from_file_location("supip_tpl_test", SERVER)
    srv = importlib.util.module_from_spec(spec); spec.loader.exec_module(srv)

    adm = Client()
    c, j = adm.login("admin")
    check("вход администратора", c == 200 and j.get("ok"), c)
    adm.rpc("admin.reset")

    print("== A. Библиотека базовых шаблонов (§2.8.1) ==")
    items = tpls(adm)
    check("в библиотеке 6 базовых шаблонов", len(items) == 6, len(items))
    names = [x["name"] for x in items]
    for n in ("Протокол рабочего совещания", "Протокол технического совещания", "Протокол оперативного совещания",
              "Протокол совещания по проекту", "Протокол научно-технического совещания",
              "Протокол совещания по контролю исполнения поручений"):
        check("базовый шаблон: %s" % n.replace("Протокол ", ""), n in names, names)
    check("коды шаблонов ШАБ-001…ШАБ-006", [x["code"] for x in items] == ["ШАБ-%03d" % i for i in range(1, 7)],
          [x["code"] for x in items])
    check("все базовые шаблоны действуют", all(x["status"] == "Действует" for x in items))
    check("все базовые шаблоны версии 1", all(x["version"] == 1 for x in items))
    check("ровно один шаблон по умолчанию", sum(1 for x in items if x["isDefault"]) == 1,
          [x["code"] for x in items if x["isDefault"]])
    check("у шаблонов указаны автор и дата изменения", all(x["author"] and x["updatedAt"] for x in items))
    check("каталог содержит число блоков", all(x["blocksCount"] >= 10 for x in items),
          [x["blocksCount"] for x in items])
    r, c, j = adm.res("template.list")
    check("справочник видов шаблонов передан", len((r or {}).get("kinds") or []) >= 7)
    check("справочник типов блоков передан (13 типов)", len((r or {}).get("blockTypes") or {}) == 13,
          len((r or {}).get("blockTypes") or {}))
    vs = (r or {}).get("variables") or []
    check("переменные meeting.* переданы (15 шт.)", len(vs) == 15, len(vs))
    for v in ("meeting.number", "meeting.date", "meeting.title", "meeting.location", "meeting.chairperson",
              "meeting.secretary", "meeting.organizer", "meeting.participants", "meeting.agenda",
              "meeting.decisions", "meeting.assignments", "meeting.attachments"):
        check("переменная %s" % v, v in vs)
    t1 = tpl(adm, "tpl1")
    types = [b["type"] for b in t1["blocks"]]
    for t in ("title", "requisites", "participants", "agenda", "discussion", "decisions", "tasks",
              "attachments", "signatures", "text", "footer", "numbering"):
        check("в базовом шаблоне есть блок «%s»" % t, t in types, types)
    req_blocks = [b["type"] for b in t1["blocks"] if b["required"]]
    check("обязательные разделы §2.8.2 помечены",
          set(req_blocks) == {"requisites", "participants", "agenda", "decisions", "signatures"}, req_blocks)
    check("история версий пуста у нового шаблона", t1["history"] == [], t1["history"])
    for tid in ("tpl2", "tpl3", "tpl4", "tpl5", "tpl6"):
        t = tpl(adm, tid)
        check("%s: обязательные разделы присутствуют" % t["code"],
              {"requisites", "participants", "agenda", "decisions", "signatures"} <=
              {b["type"] for b in t["blocks"] if b["required"]})
    check("шаблон технического совещания применяется к техническому совещанию",
          "Техническое" in tpl(adm, "tpl2")["meetTypes"], tpl(adm, "tpl2")["meetTypes"])
    check("у шаблона НТС есть блок выводов",
          any(b["title"] == "9. Научно-технические выводы" for b in tpl(adm, "tpl5")["blocks"]))
    check("у шаблона контроля поручений есть сводка просрочек",
          any("просроченным" in b["title"] for b in tpl(adm, "tpl6")["blocks"]))

    print("== B. Каталог: фильтры и выбор шаблона для совещания ==")
    check("фильтр по виду шаблона", [x["code"] for x in tpls(adm, kind="Оперативное совещание")] == ["ШАБ-003"],
          [x["code"] for x in tpls(adm, kind="Оперативное совещание")])
    check("фильтр по виду совещания", "ШАБ-002" in [x["code"] for x in tpls(adm, meetType="Техническое")])
    check("фильтр по статусу «Действует»", len(tpls(adm, status="Действует")) == 6)
    check("фильтр по статусу «Архивный» пуст", tpls(adm, status="Архивный") == [])
    c, msg = adm.err("template.list", {"status": "Неизвестный"})
    check("неизвестный статус шаблона отклонён", c == 400, (c, msg))
    c, msg = adm.err("template.get", {"id": "tpl_none"})
    check("несуществующий шаблон: 404", c == 404, (c, msg))
    m, c, j = meet(adm, title="Технический совет §2.8")
    mid = (m or {}).get("id")
    check("совещание создано", c == 200 and bool(mid), (c, j))
    d = draft(adm, mid)
    check("для технического совещания выбран ШАБ-002", d["template"]["code"] == "ШАБ-002", d["template"])
    m2, c, j = meet(adm, title="Оперативное совещание §2.8", type="Оперативное")
    mid2 = (m2 or {}).get("id")
    check("для оперативного совещания выбран ШАБ-001", draft(adm, mid2)["template"]["code"] == "ШАБ-001",
          draft(adm, mid2)["template"])
    m3, c, j = meet(adm, title="Совещание с заказчиком §2.8", type="Совещание с заказчиком")
    mid3 = (m3 or {}).get("id")
    check("для совещания с заказчиком выбран ШАБ-004", draft(adm, mid3)["template"]["code"] == "ШАБ-004",
          draft(adm, mid3)["template"])

    print("== C. Реквизиты протокола (§2.8.2 п.1) ==")
    rq = d["requisites"]
    check("организация заполнена автоматически", rq["org"] == "Инженерное подразделение", rq["org"])
    check("номер протокола заполнен автоматически", rq["number"].startswith("П-"), rq["number"])
    check("дата протокола равна дате совещания", rq["date"] == D(0), rq["date"])
    check("организатор по умолчанию — секретарь", rq["organizer"] == "u3", rq["organizer"])
    check("формат проведения по умолчанию очный", rq["format"] == "Очное", rq["format"])
    r, c, j = adm.res("meeting.requisites", {"id": mid, "org": "НИИ «Радиолиния»", "department": "Отдел 12",
                                             "place": "Каб. 214, корп. 3", "format": "Смешанное",
                                             "organizer": "u4", "number": "П-2026-118", "date": D(1)})
    check("реквизиты сохранены", c == 200, (c, j))
    rq = (r or {}).get("requisites") or {}
    check("организация изменена", rq["org"] == "НИИ «Радиолиния»", rq)
    check("подразделение сохранено", rq["department"] == "Отдел 12", rq)
    check("место проведения сохранено", rq["place"] == "Каб. 214, корп. 3", rq)
    check("формат проведения сохранён", rq["format"] == "Смешанное", rq)
    check("организатор изменён", rq["organizer"] == "u4", rq)
    check("номер протокола изменён", rq["number"] == "П-2026-118", rq)
    check("дата протокола изменена", rq["date"] == D(1), rq)
    for payload, frag in (({"format": "Заочное"}, "формат"), ({"date": "2026-13-45"}, "дата"),
                          ({"organizer": "u_none"}, "Организатор"), ({"org": "   "}, "организации"),
                          ({"number": "  "}, "номер"), ({"templateId": "tpl_none"}, "не найден")):
        p = {"id": mid}; p.update(payload)
        c, msg = adm.err("meeting.requisites", p)
        check("реквизиты: отклонено «%s»" % list(payload)[0], c in (400, 404) and frag.lower() in msg.lower(), (c, msg))
    c, msg = adm.err("meeting.requisites", {"id": mid, "number": draft(adm, mid2)["requisites"]["number"]})
    check("номер протокола не может повторяться", c == 400 and "уже занят" in msg, (c, msg))
    r, c, j = adm.res("meeting.requisites", {"id": mid, "templateId": "tpl5"})
    check("шаблон выбран вручную", c == 200 and draft(adm, mid)["template"]["code"] == "ШАБ-005",
          draft(adm, mid)["template"])
    adm.res("meeting.requisites", {"id": mid, "templateId": "tpl2"})

    print("== D. Конструктор шаблонов (MTG-10) ==")
    blocks = [
        {"type": "title", "title": "Протокол", "text": "Протокол № {{protocol.number}} от {{protocol.date}}"},
        {"type": "requisites", "title": "1. Общие сведения"},
        {"type": "participants", "title": "2. Присутствовали"},
        {"type": "agenda", "title": "3. Повестка дня"},
        {"type": "decisions", "title": "4. Решения"},
        {"type": "text", "title": "5. Особые отметки", "text": "Организация: {{organization}}. Поручений: {{meeting.assignments}}."},
        {"type": "signatures", "title": "6. Подписи"},
        {"type": "footer", "title": "Колонтитул", "text": "{{organization}} · {{meeting.number}}"},
        {"type": "numbering", "title": "Нумерация страниц"},
    ]
    t, c, j = adm.res("template.create", {"name": "Шаблон испытаний §2.8", "kind": "Пользовательский",
                                          "meetTypes": ["Приёмочное"], "blocks": blocks,
                                          "note": "Создан испытаниями"})
    check("шаблон создан", c == 200 and bool((t or {}).get("id")), (c, j))
    tid = (t or {}).get("id")
    check("новому шаблону присвоен код ШАБ-007", (t or {}).get("code") == "ШАБ-007", (t or {}).get("code"))
    check("новый шаблон версии 1 и действует", t["version"] == 1 and t["status"] == "Действует", t)
    check("блоки нормализованы (идентификаторы присвоены)", all(b.get("id") for b in t["blocks"]))
    check("обязательность блоков проставлена", [b["required"] for b in t["blocks"] if b["type"] == "requisites"] == [True])
    check("необязательный текстовый блок помечен", [b["required"] for b in t["blocks"] if b["type"] == "text"] == [False])
    for payload, frag in (({"name": "  "}, "обязательное"),
                          ({"name": "Шаблон испытаний §2.8", "blocks": blocks}, "уже существует"),
                          ({"name": "Шаблон X", "kind": "Неизвестный"}, "вид шаблона"),
                          ({"name": "Шаблон X", "blocks": [{"type": "unknown"}]}, "тип блока"),
                          ({"name": "Шаблон X", "blocks": [{"type": "text", "text": "{{unknown.var}}"}]}, "переменная"),
                          ({"name": "Шаблон X", "blocks": [{"type": "requisites"}]}, "обязательные разделы"),
                          ({"name": "Шаблон X", "blocks": []}, "хотя бы один"),
                          ({"name": "Шаблон X", "blocks": blocks + [{"type": "footer"}]}, "только один"),
                          ({"name": "Шаблон X", "blocks": blocks * 5}, "не более")):
        c, msg = adm.err("template.create", payload)
        check("создание отклонено: %s" % frag, c == 400 and frag in msg, (c, msg))
    dup = json.loads(json.dumps(blocks))
    dup[1]["id"] = dup[0]["id"] = "same"
    c, msg = adm.err("template.create", {"name": "Шаблон X", "blocks": dup})
    check("повторяющийся идентификатор блока отклонён", c == 400 and "Повторяющийся" in msg, (c, msg))
    cp, c, j = adm.res("template.copy", {"id": tid, "name": "Копия шаблона испытаний"})
    check("шаблон скопирован", c == 200 and cp["code"] == "ШАБ-008", (c, j))
    check("копия имеет версию 1", cp["version"] == 1, cp["version"])
    check("копия повторяет состав блоков", [b["type"] for b in cp["blocks"]] == [b["type"] for b in t["blocks"]])
    check("копия не является шаблоном по умолчанию", cp["isDefault"] is False)
    c, msg = adm.err("template.copy", {"id": tid, "name": "Копия шаблона испытаний"})
    check("копия с существующим названием отклонена", c == 400 and "уже существует" in msg, (c, msg))
    cp2, c, j = adm.res("template.copy", {"id": "tpl2"})
    check("копирование базового шаблона", c == 200 and "(копия)" in cp2["name"], cp2.get("name"))

    print("== E. Версии шаблона, признак «по умолчанию», архив ==")
    nb = json.loads(json.dumps(blocks))
    nb[4]["title"] = "4. Принятые решения (ред. 2)"
    nb.insert(5, {"type": "tasks", "title": "5. Контроль исполнения"})
    t2, c, j = adm.res("template.update", {"id": tid, "blocks": nb, "note": "Добавлен контроль исполнения"})
    check("шаблон обновлён", c == 200, (c, j))
    check("версия шаблона увеличена", t2["version"] == 2, t2["version"])
    check("история версий сохранена", len(t2["history"]) == 1 and t2["history"][0]["v"] == 1, t2["history"])
    check("в истории указаны автор и примечание", t2["history"][0]["by"] == "u1" and "контроль" in t2["history"][0]["note"])
    check("новый состав блоков применён", any(b["type"] == "tasks" for b in t2["blocks"]))
    t3, c, j = adm.res("template.update", {"id": tid, "name": "Шаблон испытаний §2.8 (ред. 3)"})
    check("версия 3 после изменения названия", t3["version"] == 3, t3["version"])
    rb, c, j = adm.res("template.rollback", {"id": tid, "v": 1})
    check("восстановление версии 1 выполнено", c == 200, (c, j))
    check("откат создал версию 4", rb["version"] == 4, rb["version"])
    check("состав блоков возвращён к версии 1", not any(b["type"] == "tasks" for b in rb["blocks"]))
    check("название возвращено к версии 1", rb["name"] == "Шаблон испытаний §2.8", rb["name"])
    c, msg = adm.err("template.rollback", {"id": tid, "v": 99})
    check("несуществующая версия: 404", c == 404, (c, msg))
    c, msg = adm.err("template.rollback", {"id": "tpl1"})
    check("откат без истории отклонён", c == 400 and "предыдущих версий" in msg, (c, msg))
    r, c, j = adm.res("template.default", {"id": tid})
    check("шаблон назначен по умолчанию", c == 200 and r["isDefault"], (c, j))
    check("признак «по умолчанию» единственный", sum(1 for x in tpls(adm) if x["isDefault"]) == 1,
          [x["code"] for x in tpls(adm) if x["isDefault"]])
    check("прежний шаблон по умолчанию снят", tpl(adm, "tpl1")["isDefault"] is False)
    c, msg = adm.err("template.archive", {"id": tid})
    check("шаблон по умолчанию не архивируется", c == 400 and "по умолчанию" in msg, (c, msg))
    adm.res("template.default", {"id": "tpl1"})
    adm.res("meeting.requisites", {"id": mid3, "templateId": cp["id"]})
    c, msg = adm.err("template.archive", {"id": cp["id"]})
    check("архивирование используемого шаблона требует подтверждения", c == 409 and "Подтвердите" in msg, (c, msg))
    r, c, j = adm.res("template.archive", {"id": cp["id"], "confirm": True})
    check("архивирование с подтверждением выполнено", c == 200 and r["status"] == "Архивный", (c, j))
    check("совещание с архивным шаблоном откатилось к шаблону по виду",
          draft(adm, mid3)["template"]["code"] == "ШАБ-004", draft(adm, mid3)["template"])
    c, msg = adm.err("meeting.requisites", {"id": mid, "templateId": cp["id"]})
    check("архивный шаблон нельзя выбрать", c == 400 and "архивный" in msg.lower(), (c, msg))
    c, msg = adm.err("template.update", {"id": cp["id"], "name": "Правка архивного"})
    check("архивный шаблон не редактируется", c == 400 and "Архивный" in msg, (c, msg))
    c, msg = adm.err("template.default", {"id": cp["id"]})
    check("архивный шаблон не назначается по умолчанию", c == 400, (c, msg))
    r, c, j = adm.res("template.restore", {"id": cp["id"]})
    check("шаблон восстановлен из архива", c == 200 and r["status"] == "Действует", (c, j))
    c, msg = adm.err("template.restore", {"id": cp["id"]})
    check("повторное восстановление отклонено", c == 400 and "не в архиве" in msg, (c, msg))
    pv, c, j = adm.res("template.preview", {"id": tid})
    check("предпросмотр шаблона на демо-данных", c == 200 and "Протокол №" in pv["plain"], (c, pv.get("plain", "")[:80]))
    check("переменные подставлены в предпросмотре", "НИИ" not in pv["plain"] and "{{" not in pv["plain"], pv["plain"][:200])
    check("колонтитул подставлен", "Инженерное подразделение" in pv["footer"], pv["footer"])
    pv2, c, j = adm.res("template.preview", {"id": "tpl2", "meetingId": mid})
    check("предпросмотр шаблона на данных совещания", c == 200 and "П-2026-118" in pv2["plain"], pv2["plain"][:120])

    print("== F. Рассмотрение вопросов (§2.8.2 п.4) ==")
    adm.res("meeting.agenda.add", {"id": mid, "title": "Модернизация приёмопередатчика", "reporter": "u5",
                                   "content": "Рассмотрение результатов замены приёмопередатчика РРЛ-240."})
    adm.res("meeting.agenda.add", {"id": mid, "title": "Пусконаладка стенда", "reporter": "u6"})
    r, c, j = adm.res("meeting.agenda.discussion", {"id": mid, "n": 1,
                                                   "discussion": "Докладчик доложил о завершении замены, измерения в норме.",
                                                   "remarks": "Предложено оформить акт до конца недели.",
                                                   "problems": "Не хватает ЗИП для второго канала."})
    check("рассмотрение вопроса зафиксировано", c == 200, (c, j))
    it = (r or {}).get("item") or {}
    check("обсуждение сохранено", "завершении замены" in it.get("discussion", ""), it.get("discussion"))
    check("замечания и предложения сохранены", "оформить акт" in it.get("remarks", ""), it.get("remarks"))
    check("выявленные проблемы сохранены", "ЗИП" in it.get("problems", ""), it.get("problems"))
    check("в истории вопроса отмечено рассмотрение",
          any(h.get("action") == "discussion" for h in it.get("history") or []), it.get("history"))
    for payload, frag in (({"id": mid}, "вопрос повестки"),
                          ({"id": mid, "n": 99}, "не найден"),
                          ({"id": mid, "n": 2}, "хотя бы одно")):
        c, msg = adm.err("meeting.agenda.discussion", payload)
        check("обсуждение: отклонено (%s)" % frag, c in (400, 404) and frag in msg, (c, msg))
    r, c, j = adm.res("meeting.agenda.discussion", {"id": mid, "n": 2, "discussion": "Стенд готов к испытаниям."})
    check("обсуждение второго вопроса", c == 200, (c, j))

    print("== G. Редактор черновика протокола (§2.8.3) ==")
    d = draft(adm, mid)
    check("черновик содержит разделы шаблона", len(d["sections"]) >= 10, len(d["sections"]))
    check("статус протокола — подготовка", d["status"] == "Подготовка", d["status"])
    check("черновик доступен для правки", d["editable"] is True)
    check("реквизиты переданы в черновик", d["requisites"]["number"] == "П-2026-118", d["requisites"])
    ctx = d["context"]
    check("контекст: председатель", ctx["meeting.chairperson"] == "Морозов Дмитрий", ctx["meeting.chairperson"])
    check("контекст: секретарь", ctx["meeting.secretary"] == "Волкова Елена", ctx["meeting.secretary"])
    check("контекст: организатор из реквизитов", ctx["meeting.organizer"] == "Лебедев Игорь", ctx["meeting.organizer"])
    check("контекст: место проведения", ctx["meeting.location"] == "Каб. 214, корп. 3", ctx["meeting.location"])
    check("контекст: повестка", "Модернизация приёмопередатчика" in ctx["meeting.agenda"], ctx["meeting.agenda"])
    check("контекст: участники", "Новиков Сергей" in ctx["meeting.participants"], ctx["meeting.participants"])
    keys = {s["type"]: s["key"] for s in d["sections"] if s["type"] != "text"}
    r, c, j = adm.res("meeting.protocol.draft.save", {"id": mid, "sections": [
        {"key": keys["discussion"], "title": "4. Рассмотрение вопросов и замечания",
         "body": "Дополнено секретарём: замечания приняты к исполнению."},
        {"key": keys["attachments"], "body": "Приложения приведены в описи."}],
        "conclusion": "Совещание признало работу удовлетворительной."})
    check("черновик сохранён", c == 200, (c, j))
    check("статус протокола стал «Черновик»", (r or {}).get("status") == "Черновик", (r or {}).get("status"))
    s = sec(adm, mid, keys["discussion"])
    check("заголовок раздела изменён вручную", s["title"] == "4. Рассмотрение вопросов и замечания", s["title"])
    check("ручной текст раздела сохранён", "Дополнено секретарём" in s["body"], s["body"])
    check("автозаполнение раздела сохранено", s["auto"] is True)
    check("заключение сохранено", draft(adm, mid)["conclusion"].startswith("Совещание признало"), draft(adm, mid)["conclusion"])
    check("дата и автор правки зафиксированы", draft(adm, mid)["updatedBy"] == "u1" and draft(adm, mid)["updatedAt"])
    tkey = next(s["key"] for s in secs(adm, mid) if s["type"] == "text")
    r, c, j = adm.res("meeting.protocol.draft.save", {"id": mid, "sections": [{"key": tkey, "auto": False,
                                                                              "body": "Особая отметка секретаря."}]})
    check("автозаполнение необязательного раздела отключено", c == 200 and sec(adm, mid, tkey)["auto"] is False, (c, j))
    c, msg = adm.err("meeting.protocol.draft.save", {"id": mid, "sections": [{"key": keys["requisites"], "auto": False, "body": "x"}]})
    check("обязательный раздел нельзя отключить от данных", c == 400 and "обязательный" in msg.lower(), (c, msg))
    c, msg = adm.err("meeting.protocol.draft.save", {"id": mid, "sections": [{"key": keys["attachments"], "auto": False, "body": ""}]})
    check("раздел без автозаполнения требует текста", c == 400 and "требует текста" in msg, (c, msg))
    for payload, frag in (({"sections": [{"key": "ps_none"}]}, "не найден"),
                          ({"sections": [{"title": "Без ключа"}]}, "ключ раздела"),
                          ({"sections": [{"key": tkey, "title": "   "}]}, "наименование раздела"),
                          ({"sections": [{"key": tkey, "body": "{{bad.var}}"}]}, "переменная")):
        p = {"id": mid}; p.update(payload)
        c, msg = adm.err("meeting.protocol.draft.save", p)
        check("черновик: отклонено (%s)" % frag, c in (400, 404) and frag in msg, (c, msg))
    r, c, j = adm.res("meeting.protocol.section.add", {"id": mid, "title": "9. Особые решения",
                                                       "body": "Пункт добавлен вручную секретарём."})
    check("раздел добавлен", c == 200 and bool((r or {}).get("key")), (c, j))
    newkey = (r or {}).get("key")
    check("добавленный раздел в черновике", sec(adm, mid, newkey) is not None)
    check("добавленный раздел помечен как пользовательский", sec(adm, mid, newkey)["custom"] is True)
    c, msg = adm.err("meeting.protocol.section.add", {"id": mid, "title": "9. Особые решения"})
    check("повторное наименование раздела отклонено", c == 400 and "уже есть" in msg, (c, msg))
    c, msg = adm.err("meeting.protocol.section.add", {"id": mid})
    check("раздел без наименования отклонён", c == 400, (c, msg))
    c, msg = adm.err("meeting.protocol.section.remove", {"id": mid, "key": keys["decisions"]})
    check("обязательный раздел не удаляется", c == 400 and "обязателен" in msg, (c, msg))
    c, msg = adm.err("meeting.protocol.section.remove", {"id": mid, "key": keys["attachments"]})
    check("раздел шаблона не удаляется из черновика", c == 400 and "задан шаблоном" in msg, (c, msg))
    c, msg = adm.err("meeting.protocol.section.remove", {"id": mid, "key": "ps_none"})
    check("удаление несуществующего раздела: 404", c == 404, (c, msg))
    r, c, j = adm.res("meeting.protocol.section.remove", {"id": mid, "key": newkey})
    check("пользовательский раздел удалён", c == 200 and sec(adm, mid, newkey) is None, (c, j))

    print("== H. Приложения к протоколу (§2.8.2 п.8) ==")
    st = adm.state()
    doc = next((x for x in st["docs"] if x.get("versions")), None)
    check("в M09 есть документ с версией", bool(doc))
    r, c, j = adm.res("meeting.protocol.attach", {"id": mid, "docId": doc["id"], "note": "Основание для решения"})
    check("документ M09 приложен", c == 200, (c, j))
    att = (r or {}).get("attachment") or {}
    check("версия документа зафиксирована", att.get("docV") == max(v["v"] for v in doc["versions"]), att)
    check("код документа сохранён в приложении", att.get("docCode") == doc["code"], att)
    check("примечание приложения сохранено", att.get("note") == "Основание для решения", att)
    c, msg = adm.err("meeting.protocol.attach", {"id": mid, "docId": doc["id"]})
    check("повторное приложение той же версии отклонено", c == 400 and "уже приложен" in msg, (c, msg))
    c, msg = adm.err("meeting.protocol.attach", {"id": mid, "docId": doc["id"], "docV": 99})
    check("несуществующая версия документа: 404", c == 404, (c, msg))
    c, msg = adm.err("meeting.protocol.attach", {"id": mid, "docId": "d_none"})
    check("несуществующий документ: 404", c == 404, (c, msg))
    r, c, j = adm.res("meeting.protocol.attach", {"id": mid, "name": "Опись материалов", "url": "file://share/opis.pdf",
                                                  "note": "Внешний материал"})
    check("приложение-ссылка добавлено", c == 200, (c, j))
    link_id = (r or {}).get("attachment", {}).get("id")
    c, msg = adm.err("meeting.protocol.attach", {"id": mid, "name": "Опись материалов"})
    check("повторное наименование приложения отклонено", c == 400 and "уже есть" in msg, (c, msg))
    c, msg = adm.err("meeting.protocol.attach", {"id": mid})
    check("приложение без наименования отклонено", c == 400, (c, msg))
    check("приложений в черновике: 2", len(draft(adm, mid)["attachments"]) == 2, len(draft(adm, mid)["attachments"]))
    r, c, j = adm.res("meeting.protocol.detach", {"id": mid, "attId": link_id})
    check("приложение удалено", c == 200 and len((r or {}).get("attachments") or []) == 1, (c, j))
    c, msg = adm.err("meeting.protocol.detach", {"id": mid, "attId": "pa_none"})
    check("удаление несуществующего приложения: 404", c == 404, (c, msg))

    print("== I. Решения, поручения и предпросмотр протокола ==")
    adm.res("meeting.decision.add", {"id": mid, "text": "Оформить акт замены приёмопередатчика", "resp": "u5",
                                     "due": D(5), "agendaN": 1, "createTask": True,
                                     "criteria": "Акт подписан и зарегистрирован в M09",
                                     "coassignees": ["u6"]})
    adm.res("meeting.decision.add", {"id": mid, "text": "Провести пусконаладку стенда", "resp": "u6", "due": D(7),
                                     "agendaN": 2})
    pv = preview(adm, mid)
    plain = pv.get("plain", "")
    check("предпросмотр содержит организацию", "НИИ «Радиолиния»" in plain, plain[:120])
    check("предпросмотр содержит номер и дату протокола", "П-2026-118" in plain, plain[:120])
    check("предпросмотр содержит место и формат", "Каб. 214" in plain and "Смешанное" in plain)
    check("предпросмотр содержит участников", "Новиков Сергей" in plain)
    check("предпросмотр содержит повестку", "Модернизация приёмопередатчика" in plain)
    check("предпросмотр содержит обсуждение", "завершении замены" in plain)
    check("предпросмотр содержит замечания", "оформить акт" in plain)
    check("предпросмотр содержит проблемы", "ЗИП" in plain)
    check("предпросмотр содержит решения", "Оформить акт замены" in plain)
    check("предпросмотр содержит поручения", "Поручения" in plain or "ЗАД-" in plain or "%" in plain)
    check("предпросмотр содержит подписи", "Председатель" in plain and "Секретарь" in plain)
    check("предпросмотр содержит приложения", doc["title"][:12] in plain, plain[-300:])
    check("предпросмотр содержит ручную правку", "Дополнено секретарём" in plain)
    check("предпросмотр содержит заключение", "удовлетворительной" in plain)
    check("контрольная сумма предпросмотра рассчитана", len(pv.get("checksum") or "") == 16, pv.get("checksum"))
    pvh = preview(adm, mid, html=True)
    check("предпросмотр HTML построен", "<table" in (pvh.get("html") or ""), (pvh.get("html") or "")[:80])
    check("в HTML есть реквизиты протокола", "Номер протокола" in pvh["html"])

    print("== J. Выпуск протокола: DOCX/PDF в M09 (§2.8.3) ==")
    adm.res("meeting.conduct", {"id": mid})
    r, c, j = adm.res("meeting.protocol", {"id": mid, "formats": ["docx", "pdf"]})
    check("протокол сформирован", c == 200, (c, j))
    check("DOCX и PDF зарегистрированы", [f["fmt"] for f in (r or {}).get("files") or []] == ["docx", "pdf"],
          (r or {}).get("files"))
    check("в ответе указан применённый шаблон", (r or {}).get("template", {}).get("code") == "ШАБ-002",
          (r or {}).get("template"))
    check("в ответе указаны реквизиты", (r or {}).get("requisites", {}).get("number") == "П-2026-118")
    txt = docx_text(adm, mid)
    check("DOCX содержит разделы шаблона", "1. Общие сведения" in txt and "2. Присутствовали" in txt)
    check("DOCX содержит реквизиты организации", "НИИ «Радиолиния»" in txt and "Отдел 12" in txt)
    check("DOCX содержит номер и дату протокола", "П-2026-118" in txt and "Номер протокола" in txt)
    check("DOCX содержит место и формат", "Каб. 214" in txt and "Смешанное" in txt)
    check("DOCX содержит организатора", "Лебедев Игорь" in txt)
    check("DOCX содержит изменённый заголовок раздела", "4. Рассмотрение вопросов и замечания" in txt)
    check("DOCX содержит обсуждение, замечания и проблемы",
          "Обсуждение:" in txt and "Основные замечания и предложения:" in txt and "Выявленные проблемы:" in txt)
    check("DOCX содержит соисполнителей и критерий завершения",
          "Соисполнители" in txt and "Критерий завершения" in txt and "Акт подписан" in txt)
    check("DOCX содержит статус контроля поручений", "Статус контроля" in txt)
    check("DOCX содержит приложения к протоколу", "Приложения к протоколу" in txt and "Основание для решения" in txt)
    check("DOCX содержит заключение", "Заключение" in txt and "удовлетворительной" in txt)
    check("DOCX содержит ручную правку раздела", "Дополнено секретарём" in txt)
    check("DOCX содержит подписи председателя и секретаря",
          "Председатель совещания" in txt and "Секретарь совещания" in txt)
    c, raw = adm.req("GET", "/api/export?kind=meeting&id=%s&fmt=pdf" % mid, raw=True)
    check("протокол PDF выгружен", c == 200 and isinstance(raw, bytes) and raw[:4] == b"%PDF", c)
    c, msg = adm.err("meeting.protocol", {"id": mid, "formats": ["exe"]})
    check("недопустимый формат протокола отклонён", c == 400 and "формат" in msg.lower(), (c, msg))

    print("== K. Неизменность выпущенных протоколов при изменении шаблона ==")
    adm.res("protocol.route", {"id": mid, "route": [{"user": "u3", "role": "Секретарь"},
                                                    {"user": "u2", "role": "Председатель"}]})
    adm.res("protocol.send", {"id": mid})
    adm.res("protocol.review", {"id": mid, "decision": "Согласовано"})
    adm.res("protocol.review", {"id": mid, "decision": "Согласовано"})
    r, c, j = adm.res("protocol.approve", {"id": mid, "note": "Утверждено испытаниями"})
    check("протокол утверждён", c == 200, (c, j))
    st = adm.state()
    mm = next(x for x in st["meetings"] if x["id"] == mid)
    ver = (mm["protocol"]["versions"] or [])[-1]
    check("в версии сохранён снимок шаблона", ver.get("template", {}).get("code") == "ШАБ-002", ver.get("template"))
    check("в версии сохранена версия шаблона", ver.get("template", {}).get("version") == 1, ver.get("template"))
    check("в версии сохранены реквизиты", ver.get("requisites", {}).get("number") == "П-2026-118")
    check("в версии сохранён состав разделов", len(ver.get("sections") or []) >= 10, len(ver.get("sections") or []))
    check("в версии сохранены приложения", len(ver.get("attachments") or []) == 1, ver.get("attachments"))
    check("в версии сохранена контрольная сумма (64 символа)", len(ver.get("checksum") or "") == 64, ver.get("checksum"))
    check("в версии сохранено заключение", "удовлетворительной" in (ver.get("conclusion") or ""))
    t2b, c, j = adm.res("template.update", {"id": "tpl2", "name": "Протокол технического совещания (ред. 2)",
                                            "note": "Изменение после утверждения"})
    check("шаблон изменён после утверждения протокола", c == 200 and t2b["version"] == 2, (c, j))
    st = adm.state()
    mm = next(x for x in st["meetings"] if x["id"] == mid)
    ver = (mm["protocol"]["versions"] or [])[-1]
    check("снимок утверждённой версии не изменился", ver["template"]["version"] == 1 and
          ver["template"]["name"] == "Протокол технического совещания", ver["template"])
    check("контрольная сумма утверждённой версии прежняя", len(ver["checksum"]) == 64)
    c, msg = adm.err("meeting.protocol.draft.save", {"id": mid, "conclusion": "Правка утверждённого"})
    check("утверждённый протокол не редактируется", c == 409 and "новую редакцию" in msg, (c, msg))
    c, msg = adm.err("meeting.requisites", {"id": mid, "place": "Другой кабинет"})
    check("реквизиты утверждённого протокола не изменяются", c == 409, (c, msg))
    c, msg = adm.err("meeting.protocol.section.add", {"id": mid, "title": "После утверждения"})
    check("раздел в утверждённый протокол не добавляется", c == 409, (c, msg))
    c, msg = adm.err("meeting.protocol.attach", {"id": mid, "name": "Позднее приложение"})
    check("приложение в утверждённый протокол не добавляется", c == 409, (c, msg))
    r, c, j = adm.res("protocol.newVersion", {"id": mid, "note": "Уточнение формулировок"})
    check("создана новая редакция", c == 200, (c, j))
    check("новая редакция доступна для правки", draft(adm, mid)["editable"] is True)
    r, c, j = adm.res("meeting.protocol.draft.save", {"id": mid, "conclusion": "Уточнённое заключение."})
    check("правка новой редакции сохранена", c == 200, (c, j))
    st = adm.state()
    mm = next(x for x in st["meetings"] if x["id"] == mid)
    check("история редакций сохранена", len(mm["protocol"]["versions"]) == 1 and
          mm["protocol"]["versions"][0]["conclusion"].startswith("Совещание признало"),
          len(mm["protocol"]["versions"]))
    check("новая редакция использует новую версию шаблона",
          draft(adm, mid)["template"]["version"] == 2, draft(adm, mid)["template"])

    print("== L. Права доступа (§2.14) ==")
    pm = Client(); pm.login("volkova")
    ex = Client(); ex.login("novikov")
    ob = Client(); ob.login("zaytseva")
    check("руководитель проектов видит каталог шаблонов", len(tpls(pm)) >= 6)
    check("руководитель проектов открывает шаблон", tpl(pm, "tpl1").get("code") == "ШАБ-001")
    check("руководитель проектов меняет реквизиты", pm.res("meeting.requisites", {"id": mid2, "place": "Каб. 101"})[1] == 200)
    check("руководитель проектов правит черновик",
          pm.res("meeting.protocol.draft.save", {"id": mid2, "conclusion": "Черновик РП."})[1] == 200)
    for act, payload in (("template.create", {"name": "Шаблон РП", "blocks": blocks}),
                         ("template.update", {"id": "tpl1", "name": "Правка РП"}),
                         ("template.copy", {"id": "tpl1"}),
                         ("template.default", {"id": "tpl3"}),
                         ("template.archive", {"id": "tpl3"}),
                         ("template.restore", {"id": "tpl3"}),
                         ("template.rollback", {"id": "tpl3"})):
        c, msg = pm.err(act, payload)
        check("РП отказано: %s" % act, c == 403, (c, msg))
    for act, payload in (("template.create", {"name": "Шаблон исп.", "blocks": blocks}),
                         ("meeting.requisites", {"id": mid2, "place": "Каб. 999"}),
                         ("meeting.protocol.draft.save", {"id": mid2, "conclusion": "Правка исполнителя"}),
                         ("meeting.protocol.section.add", {"id": mid2, "title": "Раздел исполнителя"}),
                         ("meeting.protocol.attach", {"id": mid2, "name": "Приложение исполнителя"}),
                         ("meeting.agenda.discussion", {"id": mid, "n": 1, "discussion": "Правка исполнителя"})):
        c, msg = ex.err(act, payload)
        check("исполнителю отказано: %s" % act.split(".")[-1], c == 403, (c, msg))
    check("наблюдатель видит каталог шаблонов", len(tpls(ob)) >= 6)
    check("наблюдатель открывает предпросмотр", bool(preview(ob, mid2).get("plain")))
    c, msg = ob.err("meeting.protocol.draft.save", {"id": mid2, "conclusion": "Правка наблюдателя"})
    check("наблюдателю отказано в правке черновика", c == 403, (c, msg))
    c, msg = ob.err("template.create", {"name": "Шаблон наблюдателя", "blocks": blocks})
    check("наблюдателю отказано в создании шаблона", c == 403, (c, msg))

    print("== M. Аудит, журнал и сохранность данных ==")
    st = adm.state()
    acts = [e["action"] for e in st["events"]]
    for a in ("Создан шаблон протокола", "Скопирован шаблон протокола", "Изменён шаблон протокола",
              "Изменён шаблон протокола по умолчанию", "Шаблон протокола переведён в архив",
              "Шаблон протокола восстановлен из архива", "Восстановлена версия шаблона протокола",
              "Изменены реквизиты протокола", "Сохранён черновик протокола", "Добавлен раздел протокола",
              "Удалён раздел протокола", "Добавлено приложение к протоколу", "Удалено приложение протокола",
              "Зафиксировано обсуждение вопроса"):
        check("событие M15: %s" % a, a in acts, a)
    ev = next((e for e in st["events"] if e["action"] == "Создан шаблон протокола"), {})
    check("в событии указан код шаблона и тип события",
          (ev.get("details") or {}).get("event") == "PROTOCOL_TEMPLATE_CREATED", ev.get("details"))
    c, raw = adm.req("GET", "/api/logs/supip.log?tail=400", raw=True)
    logtxt = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    check("журнал содержит операции с шаблонами", "шаблон" in logtxt.lower(), logtxt[:120])
    c, raw = adm.req("GET", "/api/backup", raw=True)
    bk = json.loads(raw)
    check("резервная копия содержит таблицу шаблонов", "templates" in (bk.get("tables") or {}), list(bk.get("tables") or {}))
    check("в копии 6+ шаблонов", len(bk["tables"]["templates"]) >= 6, len(bk["tables"]["templates"]))
    row = next(x for x in bk["tables"]["meetings"] if x["id"] == mid)
    check("в копии сохранены реквизиты совещания", (row.get("requisites") or {}).get("number") == "П-2026-118",
          row.get("requisites"))
    check("в копии сохранён черновик протокола", "sections" in (row.get("protocol_draft") or {}),
          list((row.get("protocol_draft") or {})))
    c, j = adm.req("POST", "/api/restore", bk)
    check("восстановление из копии выполнено", c == 200, (c, j if c != 200 else ""))
    adm.login("admin")
    check("после восстановления шаблоны на месте", len(tpls(adm)) >= 6)
    check("после восстановления реквизиты сохранены", draft(adm, mid)["requisites"]["number"] == "П-2026-118")
    check("после восстановления черновик сохранён",
          any("Дополнено секретарём" in (s.get("body") or "") for s in secs(adm, mid)))
    bk2 = json.loads(json.dumps(bk))
    bk2["tables"].pop("templates", None)
    for r2 in bk2["tables"]["meetings"]:
        r2.pop("requisites", None); r2.pop("protocol_draft", None)
    c, j = adm.req("POST", "/api/restore", bk2)
    check("копия прежней версии (без шаблонов) восстановлена", c == 200, (c, j if c != 200 else ""))
    adm.login("admin")
    d = draft(adm, mid)
    check("реквизиты восстановлены значениями по умолчанию", d["requisites"]["number"].startswith("П-"), d["requisites"])
    check("черновик восстановлен по шаблону", len(d["sections"]) >= 10, len(d["sections"]))
    check("библиотека шаблонов воссоздана", len(tpls(adm)) >= 6, len(tpls(adm)))
    check("версия схемы БД — 8", srv.SCHEMA_VERSION == 8, srv.SCHEMA_VERSION)
    check("миграция 7 создаёт таблицу шаблонов",
          any("CREATE TABLE IF NOT EXISTS templates" in s for s in srv.MIGRATIONS[7]))
    check("миграция 7 добавляет реквизиты и черновик",
          any("requisites" in s for s in srv.MIGRATIONS[7]) and any("protocol_draft" in s for s in srv.MIGRATIONS[7]))

    adm.rpc("admin.reset")
    print("\nИТОГ: %d ok, %d FAIL" % (ok, fail))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
