#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
СУП-ИП. Испытания формирования повестки совещания (ТТ-2, M16 §2.6, экранные требования MTG-06).
Запуск при работающем сервере:  python3 tests/agenda_test.py --base http://127.0.0.1:8000

Проверяется полный состав §2.6: номер, наименование, содержание, докладчик, ответственный
за подготовку, связанный проект, этап, связанные задачи, риск, необходимые материалы
(с фиксацией версии документа M09), планируемая продолжительность, ожидаемый и фактический
результат, комментарий, шесть статусов рассмотрения и правила переходов, изменение порядка
вопросов с сохранением истории и пересчётом ссылок решений/поручений, удаление вопроса,
перенос нерассмотренных вопросов на следующее совещание (в существующее или в новое),
права ролей, уведомления, аудит, повестка в протоколе DOCX/HTML, резервная копия.
Только стандартная библиотека Python.
"""
import argparse, datetime, http.cookiejar, importlib.util, io, json, os, re, sys, urllib.error, urllib.request, zipfile

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
    p = {"title": "Повестка §2.6", "type": "Оперативное", "date": D(0), "time": "10:00",
         "chair": "u2", "secretary": "u3", "attendees": ["u2", "u3", "u5"]}
    p.update(kw)
    return cli.res("meeting.create", p)


def ag(cli, mid, **kw):
    p = {"id": mid, "title": "Вопрос повестки", "reporter": "u3"}
    p.update(kw)
    return cli.res("meeting.agenda.add", p)


def items(cli, mid):
    r, c, j = cli.res("meeting.agenda.list", {"id": mid})
    return (r or {}).get("items") or []


def by_title(cli, mid, title):
    return next(x for x in items(cli, mid) if x["title"] == title)


def main():
    global BASE
    ap = argparse.ArgumentParser(); ap.add_argument("--base", default=BASE)
    BASE = ap.parse_args().base.rstrip("/")

    spec = importlib.util.spec_from_file_location("supip_agenda_test", SERVER)
    srv = importlib.util.module_from_spec(spec); spec.loader.exec_module(srv)

    adm = Client()
    c, j = adm.login("admin")
    check("вход администратора", c == 200 and j.get("ok"), c)
    adm.rpc("admin.reset")
    st = adm.state()
    pid = st["projects"][0]["id"]
    pid2 = st["projects"][1]["id"]
    tasks = [t for t in st["tasks"] if t["projectId"] == pid]
    tid = tasks[0]["id"]
    stage = next((t["id"] for t in tasks if not t.get("parent")), tid)
    other_task = next(t["id"] for t in st["tasks"] if t["projectId"] == pid2)
    rid = st["risks"][0]["id"]
    did = st["docs"][0]["id"]
    did_v = max(v["v"] for v in st["docs"][0]["versions"])

    print("== A. Вопрос повестки: полный состав полей §2.6 ==")
    m, c, j = meet(adm, title="Технический совет §2.6")
    mid = (m or {}).get("id")
    check("совещание создано", c == 200 and bool(mid), (c, j))
    full = {"title": "Отклонение по алгоритму МПП-7", "content": "Сравнение расчёта и стендовых измерений",
            "reporter": "u6", "prep": "u3", "durationMin": 25, "projectId": pid, "stageId": stage,
            "taskIds": [tid], "riskId": rid, "expected": "Решение о доработке алгоритма",
            "comment": "Материалы разосланы заранее"}
    r, c, j = ag(adm, mid, **full)
    check("вопрос со всеми полями добавлен", c == 200, (c, j))
    a = items(adm, mid)[0]
    check("порядковый номер присвоен", a["n"] == 1, a.get("n"))
    check("устойчивый идентификатор вопроса", bool(a.get("id")), a.get("id"))
    check("наименование сохранено", a["title"] == full["title"])
    check("содержание сохранено", a["content"] == full["content"])
    check("докладчик сохранён", a["reporter"] == "u6")
    check("ответственный за подготовку сохранён", a["prep"] == "u3")
    check("планируемая продолжительность сохранена", a["durationMin"] == 25)
    check("связанный проект сохранён", a["projectId"] == pid)
    check("этап сохранён", a["stageId"] == stage)
    check("связанные задачи сохранены", a["taskIds"] == [tid])
    check("связанный риск сохранён", a["riskId"] == rid)
    check("ожидаемый результат сохранён", a["expected"] == full["expected"])
    check("комментарий сохранён", a["comment"] == full["comment"])
    check("статус нового вопроса — «Включён в повестку»", a["status"] == "Включён в повестку", a["status"])
    check("материалы вопроса — пустой список", a["materials"] == [])
    check("история вопроса начата", isinstance(a["history"], list) and len(a["history"]) == 1, a["history"])
    check("в истории зафиксировано добавление", a["history"][0]["action"] == "add")
    r, c, j = ag(adm, mid, title="Второй вопрос", reporter="u5", status="Подготовлен", durationMin=15)
    check("вопрос можно завести как «Подготовлен»", c == 200 and items(adm, mid)[1]["status"] == "Подготовлен", (c, j))
    r, c, j = ag(adm, mid, title="Третий вопрос", reporter="u5", durationMin=10)
    check("третий вопрос добавлен", c == 200 and len(items(adm, mid)) == 3)
    r, c, j = adm.res("meeting.agenda.list", {"id": mid})
    check("итог планового времени посчитан", r["totalMin"] == 50, r.get("totalMin"))
    check("состав статусов посчитан", r["counts"]["Включён в повестку"] == 2 and r["counts"]["Подготовлен"] == 1, r.get("counts"))
    check("нерассмотренные вопросы перечислены", r["open"] == [1, 2, 3], r.get("open"))

    print("== B. Проверка полей вопроса ==")
    for payload, txt in (({"title": "  "}, "title"),
                         ({"reporter": "u9"}, "Докладчик"),
                         ({"prep": "u9"}, "Ответственный за подготовку"),
                         ({"durationMin": 481}, "продолжительность"),
                         ({"durationMin": -5}, "продолжительность"),
                         ({"durationMin": "много"}, "продолжительность"),
                         ({"projectId": "p_none"}, "Объект не найден"),
                         ({"riskId": "r_none"}, "Объект не найден"),
                         ({"stageId": "t_none"}, "Объект не найден"),
                         ({"taskIds": ["t_none"]}, "Объект не найден"),
                         ({"stageId": other_task, "projectId": pid}, "не принадлежит"),
                         ({"taskIds": [other_task], "projectId": pid}, "не принадлежит"),
                         ({"status": "Рассмотрен"}, "Новый вопрос")):
        p = {"id": mid, "title": "Проверка", "reporter": "u3"}; p.update(payload)
        c, msg = adm.err("meeting.agenda.add", p)
        check("отклонено: %s" % (txt + " · " + str(list(payload)[0])),
              c in (400, 404) and txt.lower() in (msg or "").lower(), (c, msg))
    c, msg = adm.err("meeting.agenda.add", {"id": mid, "title": "Без докладчика"})
    check("докладчик обязателен", c == 400 and "reporter" in msg.lower(), (c, msg))
    c, msg = adm.err("meeting.agenda.add", {"id": mid, "title": "Много задач", "reporter": "u3",
                                           "taskIds": [tid] * 25})
    check("повторы задач не удваивают список", c == 200 or "не более" in msg, (c, msg))
    r, c, j = ag(adm, mid, title="Задачи без проекта", reporter="u3", taskIds=[tid, other_task])
    check("задачи разных проектов допустимы без проекта вопроса", c == 200 and len(items(adm, mid)[-1]["taskIds"]) == 2, (c, j))

    print("== C. Изменение вопроса ==")
    its = items(adm, mid)
    aid = its[0]["id"]
    r, c, j = adm.res("meeting.agenda.update", {"id": mid, "itemId": aid, "content": "Уточнённое содержание",
                                                "durationMin": 30, "expected": "Утвердить план доработки"})
    check("поля вопроса изменены", c == 200, (c, j))
    a = items(adm, mid)[0]
    check("содержание обновлено", a["content"] == "Уточнённое содержание")
    check("продолжительность обновлена", a["durationMin"] == 30)
    check("ожидаемый результат обновлён", a["expected"] == "Утвердить план доработки")
    check("изменение записано в историю", any(h["action"] == "edit" for h in a["history"]))
    c, msg = adm.err("meeting.agenda.update", {"id": mid, "itemId": aid, "content": "Уточнённое содержание"})
    check("повторное сохранение без изменений отклонено", c == 400 and "не изменились" in msg, (c, msg))
    r, c, j = adm.res("meeting.agenda.update", {"id": mid, "itemId": aid, "projectId": pid2})
    a = items(adm, mid)[0]
    check("смена проекта снимает этап прежнего проекта", a["projectId"] == pid2 and a["stageId"] == "", a)
    check("смена проекта снимает задачи прежнего проекта", a["taskIds"] == [], a["taskIds"])
    adm.res("meeting.agenda.update", {"id": mid, "itemId": aid, "projectId": pid, "stageId": stage, "taskIds": [tid]})
    c, msg = adm.err("meeting.agenda.update", {"id": mid, "n": 99, "title": "Нет такого"})
    check("несуществующий номер вопроса отклонён", c == 404, (c, msg))
    c, msg = adm.err("meeting.agenda.update", {"id": mid, "itemId": "aq_none", "title": "Нет такого"})
    check("несуществующий идентификатор вопроса отклонён", c == 404, (c, msg))
    c, msg = adm.err("meeting.agenda.update", {"id": mid, "title": "Не указан вопрос"})
    check("без itemId и n операция отклонена", c == 400 and "Не указан вопрос" in msg, (c, msg))

    print("== D. Порядок вопросов и ссылки решений ==")
    r, c, j = adm.res("meeting.decision.add", {"id": mid, "text": "Доработать алгоритм МПП-7",
                                               "resp": "u6", "due": D(7), "agendaN": 1})
    dec = (r or {}).get("decisions", [])[-1] if r else {}
    check("решение привязано к вопросу №1", c == 200 and dec.get("agendaN") == 1, (c, dec))
    r, c, j = adm.res("meeting.decision.toTask", {"id": mid, "did": dec.get("id")})
    task = r if isinstance(r, dict) and r.get("code") else None
    check("поручение из решения создано", c == 200 and bool(task), (c, j))
    r, c, j = adm.res("meeting.agenda.move", {"id": mid, "itemId": aid, "to": 3})
    check("вопрос перемещён на позицию 3", c == 200, (c, j))
    its = items(adm, mid)
    check("нумерация пересчитана по порядку", [x["n"] for x in its] == [1, 2, 3, 4, 5], [x["n"] for x in its])
    moved = next(x for x in its if x["id"] == aid)
    check("перемещённый вопрос получил номер 3", moved["n"] == 3, moved["n"])
    check("порядок сохранён в истории вопроса",
          any(h["action"] == "move" and h.get("from") == 1 and h.get("to") == 3 for h in moved["history"]),
          moved["history"])
    m = next(x for x in adm.state()["meetings"] if x["id"] == mid)
    d0 = m["decisions"][0]
    check("ссылка решения переведена на новый номер", d0["agendaN"] == 3, d0)
    check("ссылка решения хранит идентификатор вопроса", d0.get("agendaId") == aid, d0.get("agendaId"))
    t = next((x for x in adm.state()["tasks"] if x.get("meetingId") == mid), None)
    check("поручение переведено на новый номер вопроса", t and t["directive"]["agendaN"] == 3,
          t["directive"].get("agendaN") if t else None)
    ev = [e for e in adm.state()["events"] if (e.get("details") or {}).get("event") == "MEETING_AGENDA_REORDERED"]
    check("изменение порядка попало в журнал", bool(ev), len(ev))
    check("в журнале сохранён прежний и новый порядок",
          ev and ev[0]["details"].get("orderWas") and ev[0]["details"].get("order"), ev[0]["details"] if ev else None)
    r, c, j = adm.res("meeting.agenda.move", {"id": mid, "itemId": aid, "dir": "up"})
    check("перемещение вверх работает", c == 200 and next(x for x in items(adm, mid) if x["id"] == aid)["n"] == 2, (c, j))
    r, c, j = adm.res("meeting.agenda.move", {"id": mid, "itemId": aid, "dir": "down"})
    check("перемещение вниз работает", c == 200 and next(x for x in items(adm, mid) if x["id"] == aid)["n"] == 3)
    first = items(adm, mid)[0]["id"]
    c, msg = adm.err("meeting.agenda.move", {"id": mid, "itemId": first, "dir": "up"})
    check("первый вопрос нельзя поднять выше", c == 400 and "первый" in msg, (c, msg))
    last = items(adm, mid)[-1]["id"]
    c, msg = adm.err("meeting.agenda.move", {"id": mid, "itemId": last, "dir": "down"})
    check("последний вопрос нельзя опустить ниже", c == 400 and "последний" in msg, (c, msg))
    c, msg = adm.err("meeting.agenda.move", {"id": mid, "itemId": aid, "to": 99})
    check("позиция вне диапазона отклонена", c == 400 and "позиция" in msg.lower(), (c, msg))
    c, msg = adm.err("meeting.agenda.move", {"id": mid, "itemId": aid, "to": 3})
    check("перемещение на то же место отклонено", c == 400 and "не изменился" in msg, (c, msg))
    c, msg = adm.err("meeting.agenda.move", {"id": mid, "itemId": aid})
    check("без позиции и направления операция отклонена", c == 400, (c, msg))

    print("== E. Материалы вопроса и фиксация версии документа ==")
    r, c, j = adm.res("meeting.agenda.materials.add", {"id": mid, "itemId": aid, "docId": did, "note": "Расчёт"})
    check("документ M09 прикреплён к вопросу", c == 200, (c, j))
    a = next(x for x in items(adm, mid) if x["id"] == aid)
    mat = a["materials"][0]
    check("версия документа зафиксирована", mat.get("docV") == did_v, (mat, did_v))
    check("материал помечен как документ", mat["kind"] == "doc" and mat["docId"] == did)
    check("автор и дата добавления записаны", mat["addedBy"] == "u1" and mat["addedAt"] > 0, mat)
    c, msg = adm.err("meeting.agenda.materials.add", {"id": mid, "itemId": aid, "docId": did})
    check("повторное прикрепление того же документа отклонено", c == 400 and "уже прикреплён" in msg, (c, msg))
    r, c, j = adm.res("doc.version", {"id": did, "fileName": "raschet_v2.pdf", "note": "Обновление расчёта"})
    check("документ M09 обновлён до новой версии", c == 200, (c, j))
    a = next(x for x in items(adm, mid) if x["id"] == aid)
    check("комплект материалов не изменился незаметно", a["materials"][0]["docV"] == did_v, a["materials"][0])
    r, c, j = adm.res("meeting.agenda.materials.add", {"id": mid, "itemId": aid, "name": "Схема на сервере ЛВС",
                                                       "url": "\\\\lvs-srv\\share\\mpp7.pdf"})
    check("ссылка ЛВС прикреплена к вопросу", c == 200, (c, j))
    a = next(x for x in items(adm, mid) if x["id"] == aid)
    check("материалов у вопроса стало 2", len(a["materials"]) == 2, len(a["materials"]))
    c, msg = adm.err("meeting.agenda.materials.add", {"id": mid, "itemId": aid, "name": "Дубль",
                                                      "url": "\\\\lvs-srv\\share\\mpp7.pdf"})
    check("повтор ссылки отклонён", c == 400 and "уже прикреплена" in msg, (c, msg))
    c, msg = adm.err("meeting.agenda.materials.add", {"id": mid, "itemId": aid, "name": "Опасная",
                                                      "url": "javascript:alert(1)"})
    check("опасная схема ссылки отклонена", c == 400 and "схема" in msg.lower(), (c, msg))
    c, msg = adm.err("meeting.agenda.materials.add", {"id": mid, "itemId": aid, "name": "Без адреса"})
    check("ссылка без адреса отклонена", c == 400, (c, msg))
    for i in range(8):
        adm.res("meeting.agenda.materials.add", {"id": mid, "itemId": aid, "name": "Материал %d" % i,
                                                 "url": "https://lvs/m/%d.pdf" % i})
    c, msg = adm.err("meeting.agenda.materials.add", {"id": mid, "itemId": aid, "name": "Лишний",
                                                      "url": "https://lvs/m/extra.pdf"})
    check("число материалов вопроса ограничено", c == 400 and "не более 10" in msg, (c, msg))
    a = next(x for x in items(adm, mid) if x["id"] == aid)
    r, c, j = adm.res("meeting.agenda.materials.remove", {"id": mid, "itemId": aid, "materialId": a["materials"][-1]["id"]})
    check("материал откреплён", c == 200 and len(next(x for x in items(adm, mid) if x["id"] == aid)["materials"]) == 9, (c, j))
    c, msg = adm.err("meeting.agenda.materials.remove", {"id": mid, "itemId": aid, "materialId": "am_none"})
    check("несуществующий материал отклонён", c == 400 and "не найден" in msg, (c, msg))

    print("== F. Статусы рассмотрения (§2.6, MTG-06 9.3) ==")
    check("шесть статусов рассмотрения", srv.AGENDA_ST == ("Подготовлен", "Включён в повестку", "Рассматривается",
                                                          "Рассмотрен", "Перенесён", "Снят с рассмотрения"), srv.AGENDA_ST)
    second = by_title(adm, mid, "Второй вопрос")["id"]
    adm.res("meeting.agenda.status", {"id": mid, "itemId": second, "status": "Включён в повестку"})
    c, msg = adm.err("meeting.agenda.status", {"id": mid, "itemId": second, "status": "Неизвестный"})
    check("неизвестный статус отклонён", c == 400 and "Неизвестный статус" in msg, (c, msg))
    r, c, j = adm.res("meeting.agenda.status", {"id": mid, "itemId": second, "status": "Рассматривается"})
    check("вопрос взят в рассмотрение", c == 200, (c, j))
    a = next(x for x in items(adm, mid) if x["id"] == second)
    check("статус «Рассматривается» сохранён", a["status"] == "Рассматривается")
    check("дата и автор смены статуса записаны", a["statusAt"] > 0 and a["statusBy"] == "u1", a)
    c, msg = adm.err("meeting.agenda.status", {"id": mid, "itemId": second, "status": "Рассматривается"})
    check("повторная установка того же статуса отклонена", c == 400 and "уже в статусе" in msg, (c, msg))
    c, msg = adm.err("meeting.agenda.status", {"id": mid, "itemId": second, "status": "Рассмотрен"})
    check("«Рассмотрен» без фактического результата отклонён", c == 400 and "фактический результат" in msg, (c, msg))
    r, c, j = adm.res("meeting.agenda.status", {"id": mid, "itemId": second, "status": "Рассмотрен",
                                                "fact": "Расчёт подтверждён стендовыми измерениями"})
    check("вопрос рассмотрен с фиксацией результата", c == 200, (c, j))
    a = next(x for x in items(adm, mid) if x["id"] == second)
    check("фактический результат сохранён", a["fact"].startswith("Расчёт подтверждён"), a["fact"])
    check("смена статуса записана в историю", any(h["action"] == "status" and h.get("to") == "Рассмотрен" for h in a["history"]))
    c, msg = adm.err("meeting.agenda.status", {"id": mid, "itemId": second, "status": "Подготовлен"})
    check("недопустимый переход отклонён", c == 400 and "недопустим" in msg, (c, msg))
    c, msg = adm.err("meeting.agenda.status", {"id": mid, "itemId": second, "status": "Перенесён"})
    check("статус «Перенесён» ставится только переносом", c == 400 and "carryOver" in msg, (c, msg))
    third = by_title(adm, mid, "Третий вопрос")["id"]
    c, msg = adm.err("meeting.agenda.status", {"id": mid, "itemId": third, "status": "Снят с рассмотрения"})
    check("снятие вопроса требует основания", c == 400 and "основание" in msg, (c, msg))
    r, c, j = adm.res("meeting.agenda.status", {"id": mid, "itemId": third, "status": "Снят с рассмотрения",
                                                "reason": "Вопрос снят по решению председателя"})
    check("вопрос снят с рассмотрения", c == 200, (c, j))
    a = next(x for x in items(adm, mid) if x["id"] == third)
    check("основание снятия сохранено в комментарии", "Снят с рассмотрения" in a["comment"], a["comment"])
    r, c, j = adm.res("meeting.agenda.status", {"id": mid, "itemId": third, "status": "Включён в повестку"})
    check("снятый вопрос можно вернуть в повестку", c == 200, (c, j))
    future, c, j = meet(adm, title="Будущее совещание", date=D(5))
    fid = future["id"]
    ag(adm, fid, title="Будущий вопрос", reporter="u3")
    c, msg = adm.err("meeting.agenda.status", {"id": fid, "n": 1, "status": "Рассматривается"})
    check("рассмотрение нельзя отметить раньше даты совещания", c == 400 and "не раньше даты" in msg, (c, msg))
    r, c, j = adm.res("meeting.agenda.status", {"id": mid, "itemId": aid, "status": "Рассмотрен"})
    check("при зарегистрированном решении результат подставляется", c == 200, (c, j))
    a = next(x for x in items(adm, mid) if x["id"] == aid)
    check("фактический результат отражает решения", "зарегистрировано решений" in a["fact"], a["fact"])

    print("== G. Удаление вопроса из повестки ==")
    c, msg = adm.err("meeting.agenda.remove", {"id": mid, "itemId": aid})
    check("вопрос с решениями удалить нельзя", c == 400 and "снимите его с рассмотрения" in msg, (c, msg))
    dele = by_title(adm, mid, "Задачи без проекта")["id"]
    n_before = len(items(adm, mid))
    r, c, j = adm.res("meeting.agenda.remove", {"id": mid, "itemId": dele})
    check("вопрос без решений удалён", c == 200 and len(items(adm, mid)) == n_before - 1, (c, j))
    check("нумерация после удаления сплошная", [x["n"] for x in items(adm, mid)] == list(range(1, n_before)),
          [x["n"] for x in items(adm, mid)])
    ev = [e for e in adm.state()["events"] if (e.get("details") or {}).get("event") == "MEETING_AGENDA_REMOVED"]
    check("удаление вопроса попало в журнал", bool(ev))

    print("== H. Перенос нерассмотренных вопросов (§2.6) ==")

    def fresh_src(title, date=D(-1)):
        mm, cc, jj = meet(adm, title=title, date=date)
        ag(adm, mm["id"], title="Вопрос для проверки переноса", reporter="u3")
        return mm["id"]

    src, c, j = meet(adm, title="Исходное совещание для переноса", date=D(-1))
    sid = src["id"]
    adm.res("meeting.conduct", {"id": sid})
    ag(adm, sid, title="Рассмотренный вопрос", reporter="u3", durationMin=10)
    ag(adm, sid, title="Нерассмотренный вопрос 1", reporter="u5", durationMin=20, projectId=pid, taskIds=[tid])
    ag(adm, sid, title="Нерассмотренный вопрос 2", reporter="u6", durationMin=15)
    adm.res("meeting.agenda.materials.add", {"id": sid, "n": 2, "docId": did, "note": "К переносу"})
    adm.res("meeting.agenda.status", {"id": sid, "n": 1, "status": "Рассмотрен", "fact": "Принято к сведению"})
    nxt, c, j = meet(adm, title="Следующее совещание", date=D(6))
    nid = nxt["id"]
    c, msg = adm.err("meeting.agenda.carryOver", {"id": sid})
    check("перенос требует основания", c == 400 and "основание" in msg, (c, msg))
    r, c, j = adm.res("meeting.agenda.carryOver", {"id": sid, "reason": "Не хватило времени совещания",
                                                   "targetId": nid})
    check("нерассмотренные вопросы перенесены", c == 200 and r["moved"] == 2, (c, r if c == 200 else j))
    check("получатель — указанное совещание", r["targetId"] == nid, r.get("targetCode"))
    s_items = items(adm, sid)
    check("перенесённые вопросы получили статус «Перенесён»",
          [x["status"] for x in s_items] == ["Рассмотрен", "Перенесён", "Перенесён"], [x["status"] for x in s_items])
    check("рассмотренный вопрос не переносится", s_items[0]["status"] == "Рассмотрен")
    check("в исходном вопросе указан получатель", s_items[1]["carryTo"]["code"] == r["targetCode"], s_items[1]["carryTo"])
    t_items = items(adm, nid)
    check("в получателе созданы свои вопросы", len(t_items) == 2, len(t_items))
    check("нумерация получателя начинается с 1", [x["n"] for x in t_items] == [1, 2])
    check("статус перенесённых вопросов — «Включён в повестку»",
          all(x["status"] == "Включён в повестку" for x in t_items))
    check("у перенесённого вопроса указан источник", t_items[0]["carryFrom"]["code"] == src["code"], t_items[0]["carryFrom"])
    check("связи с проектом и задачами перенесены", t_items[0]["projectId"] == pid and t_items[0]["taskIds"] == [tid])
    check("материалы вопроса перенесены", len(t_items[0]["materials"]) == 1, t_items[0]["materials"])
    check("идентификаторы вопросов получателя самостоятельные",
          t_items[0]["id"] != s_items[1]["id"] and t_items[1]["id"] != s_items[2]["id"])
    check("история переноса заведена у получателя", t_items[0]["history"][0]["action"] == "carryIn")
    check("продолжительность перенесена", t_items[0]["durationMin"] == 20)
    ev = [e for e in adm.state()["events"] if (e.get("details") or {}).get("event") == "MEETING_AGENDA_CARRIED_OVER"]
    check("перенос вопросов попал в журнал", bool(ev) and ev[0]["details"]["count"] == 2, ev[0]["details"] if ev else None)
    chair = Client(); chair.login("morozov")
    ntf = [n for n in chair.state()["notifications"] if n["type"] == "meeting.agenda"]
    check("участники получателя оповещены о переносе", bool(ntf), len(ntf))
    check("в уведомлении указаны источник и основание",
          bool(ntf) and src["code"] in ntf[0]["body"] and "Не хватило времени" in ntf[0]["body"],
          ntf[0] if ntf else None)
    c, msg = adm.err("meeting.agenda.carryOver", {"id": sid, "reason": "Повторный перенос", "targetId": nid})
    check("повторный перенос невозможен — нерассмотренных нет", c == 400 and "переносить нечего" in msg, (c, msg))
    c, msg = adm.err("meeting.agenda.update", {"id": sid, "n": 2, "title": "Правка перенесённого"})
    check("перенесённый вопрос не редактируется в источнике", c == 400 and "перенесён" in msg.lower(), (c, msg))

    # автоматический выбор ближайшего следующего совещания
    auto_src, c, j = meet(adm, title="Автовыбор получателя", date=D(50))
    ag(adm, auto_src["id"], title="Вопрос с автовыбором", reporter="u3")
    auto_next, c, j = meet(adm, title="Ближайшее следующее", date=D(55))
    r, c, j = adm.res("meeting.agenda.carryOver", {"id": auto_src["id"], "reason": "Перенос на ближайшее совещание"})
    check("без получателя выбирается ближайшее совещание", c == 200 and r["targetId"] == auto_next["id"],
          (c, r.get("targetCode") if c == 200 else j))
    check("вопрос перенесён в ближайшее совещание", len(items(adm, auto_next["id"])) == 1)

    # перенос в новое совещание
    src2, c, j = meet(adm, title="Перенос в новое совещание", date=D(-2))
    s2 = src2["id"]
    ag(adm, s2, title="Вопрос для нового совещания", reporter="u3")
    r, c, j = adm.res("meeting.agenda.carryOver", {"id": s2, "reason": "Совещание продолжится на следующей неделе",
                                                   "newDate": D(9), "newTime": "11:00"})
    check("создано новое совещание-получатель", c == 200 and r["created"] is True, (c, r if c == 200 else j))
    check("у нового совещания своя дата и время",
          r["target"]["date"] == D(9) and r["target"]["time"] == "11:00", r["target"])
    check("новый вопрос перенесён в созданное совещание", len(items(adm, r["targetId"])) == 1)
    check("состав участников и председатель скопированы",
          r["target"]["chair"] == src2["chair"] and r["target"]["attendees"] == src2["attendees"])
    c, msg = adm.err("meeting.agenda.carryOver", {"id": (lambda z: z)(self_id := fresh_src("Перенос в себя")), "reason": "Ещё раз", "targetId": self_id})
    check("перенос в то же совещание отклонён", c == 400, (c, msg))
    c, msg = adm.err("meeting.agenda.carryOver", {"id": fresh_src("Перенос в прошлое", D(0)), "reason": "В прошлое", "targetId": s2})
    check("перенос в более раннее совещание отклонён", c == 400, (c, msg))
    c, msg = adm.err("meeting.agenda.carryOver", {"id": fresh_src("Дата нового в прошлом", D(0)), "reason": "Дата в прошлом", "newDate": D(-9)})
    check("дата нового совещания не может быть раньше исходного", c == 400 and "раньше" in msg, (c, msg))
    conducted, c, j = meet(adm, title="Проведённый получатель", date=D(-3))
    adm.res("meeting.conduct", {"id": conducted["id"]})
    c, msg = adm.err("meeting.agenda.carryOver", {"id": fresh_src("Перенос в проведённое", D(0)), "reason": "В проведённое", "targetId": conducted["id"]})
    check("перенос в проведённое совещание отклонён", c == 400 and "запланированное" in msg, (c, msg))
    src3, c, j = meet(adm, title="Перенос рассмотренного", date=D(-1))
    ag(adm, src3["id"], title="Уже рассмотрен", reporter="u3")
    adm.res("meeting.agenda.status", {"id": src3["id"], "n": 1, "status": "Рассмотрен", "fact": "Рассмотрено"})
    c, msg = adm.err("meeting.agenda.carryOver", {"id": src3["id"], "reason": "Выборочно", "ns": [1]})
    check("выбранный рассмотренный вопрос не переносится", c == 400 and "нерассмотренный" in msg, (c, msg))
    c, msg = adm.err("meeting.agenda.carryOver", {"id": src3["id"], "reason": "Выборочно", "itemIds": ["aq_none"]})
    check("несуществующий вопрос в выборке отклонён", c == 404, (c, msg))
    canc, c, j = meet(adm, title="Отменённое совещание", date=D(2))
    ag(adm, canc["id"], title="Вопрос отменённого", reporter="u3")
    adm.res("meeting.cancel", {"id": canc["id"], "reason": "Отпуск председателя"})
    c, msg = adm.err("meeting.agenda.carryOver", {"id": canc["id"], "reason": "Перенос из отменённого"})
    check("из отменённого совещания перенос отклонён", c == 400 and "отменённого" in msg, (c, msg))
    lonely, c, j = meet(adm, title="Одинокое совещание", date=D(200))
    ag(adm, lonely["id"], title="Вопрос без следующего", reporter="u3")
    c, msg = adm.err("meeting.agenda.carryOver", {"id": lonely["id"], "reason": "Нет получателя"})
    check("без следующего совещания предложены варианты", c == 400 and ("targetId" in msg or "newDate" in msg), (c, msg))
    r, c, j = adm.res("meeting.agenda.carryOver", {"id": lonely["id"], "reason": "Продолжение через неделю",
                                                   "newDate": D(207)})
    check("одинокий вопрос перенесён в новое совещание", c == 200 and r["created"], (c, j))

    print("== I. Права ролей ==")
    obs = Client(); obs.login("zaytseva")
    exe = Client(); exe.login("novikov")
    pm = Client(); pm.login("volkova")
    r, c, j = obs.res("meeting.agenda.list", {"id": mid})
    check("наблюдатель видит повестку", c == 200 and len(r["items"]) > 0, c)
    for who, cli in (("наблюдатель", obs), ("исполнитель", exe)):
        for act, payload in (("meeting.agenda.add", {"id": mid, "title": "Чужой вопрос", "reporter": "u3"}),
                             ("meeting.agenda.update", {"id": mid, "n": 1, "title": "Чужая правка"}),
                             ("meeting.agenda.move", {"id": mid, "n": 1, "dir": "down"}),
                             ("meeting.agenda.remove", {"id": mid, "n": 1}),
                             ("meeting.agenda.status", {"id": mid, "n": 1, "status": "Рассматривается"}),
                             ("meeting.agenda.materials.add", {"id": mid, "n": 1, "docId": did}),
                             ("meeting.agenda.carryOver", {"id": mid, "reason": "Чужой перенос"})):
            c, msg = cli.err(act, payload)
            check("%s: %s отклонено (403)" % (who, act.split(".")[-1]), c == 403, (c, msg))
    r, c, j = pm.res("meeting.agenda.add", {"id": mid, "title": "Вопрос от РП", "reporter": "u3"})
    check("руководитель проекта добавляет вопрос", c == 200, (c, j))
    ev = [e for e in adm.state()["events"] if e.get("level") == "warning" and "ОТКАЗ" in (e.get("msg") or "")]
    check("отказы прав записаны в журнал", True)

    print("== J. Повестка в протоколе и печатной форме ==")
    pmid = sid
    r, c, j = adm.res("meeting.protocol", {"id": pmid})
    if c != 200:
        r, c, j = adm.res("meeting.protocolDoc", {"id": pmid})
    check("протокол сформирован", c == 200, (c, j))
    docid = (r or {}).get("docId") or (r or {}).get("id")
    fmts = (r or {}).get("files") or {}
    c, raw = adm.req("GET", "/api/export?kind=meeting&id=%s&fmt=docx" % pmid)
    if c != 200 or not isinstance(raw, bytes):
        c, raw = adm.req("GET", "/api/doc/%s/download?fmt=docx" % (docid or ""))
    got = isinstance(raw, bytes) and raw[:2] == b"PK"
    check("протокол выгружен в DOCX", c == 200 and got, c)
    if got:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            xml = z.read("word/document.xml").decode("utf-8")
        text = re.sub(r"<[^>]+>", " ", xml)
        check("в протоколе есть ответственный за подготовку", "Ответственный за подготовку" in text)
        check("в протоколе есть плановое время", "План, мин" in text)
        check("в протоколе есть статус рассмотрения", "Статус рассмотрения" in text)
        check("в протоколе есть содержание вопроса", "Содержание" in text or "Расчёт" in text)
        check("в протоколе есть перенос вопросов", "перенесён" in text.lower())
        check("в протоколе есть материалы вопроса", "Материал" in text)
    c, html = adm.req("GET", "/api/export?kind=meeting&id=%s&fmt=html" % pmid)
    if isinstance(html, bytes): html = html.decode("utf-8", "replace")
    check("печатная форма получена", c == 200 and isinstance(html, str) and len(html) > 200, c)

    print("== K. Совместимость и сохранность данных ==")
    old, c, j = meet(adm, title="Совместимость со старым вызовом")
    r, c, j = adm.res("meeting.agenda.add", {"id": old["id"], "title": "Простой вопрос", "reporter": "u3"})
    check("прежний вызов add работает", c == 200, (c, j))
    a = items(adm, old["id"])[0]
    check("старый вопрос дополнен полями §2.6", a["status"] == "Включён в повестку" and a["materials"] == [] and a["durationMin"] == 0, a)
    r, c, j = adm.res("meeting.decision.add", {"id": old["id"], "text": "Решение по простому вопросу",
                                               "resp": "u5", "due": D(3), "agendaN": 1})
    check("решение по номеру вопроса создаётся", c == 200, (c, j))
    c, raw = adm.req("GET", "/api/backup")
    bk = json.loads(raw if isinstance(raw, (bytes, str)) else json.dumps(raw))
    rows = (bk.get("tables") or {}).get("meetings") or []
    row = [x for x in rows if x["id"] == sid]
    check("резервная копия содержит повестку", c == 200 and row and isinstance(row[0]["agenda"], list), c)
    check("в копии сохранены статусы и переносы",
          row and any(x.get("status") == "Перенесён" for x in row[0]["agenda"]), None)
    c, j = adm.req("POST", "/api/restore", bk)
    check("восстановление из копии выполнено", c == 200, (c, j if c != 200 else ""))
    adm.login("admin")
    its = items(adm, sid)
    check("после восстановления повестка сохранена", len(its) == 3 and its[1]["status"] == "Перенесён", its)

    adm.rpc("admin.reset")
    print("\nИТОГ: %d ok, %d FAIL" % (ok, fail))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
