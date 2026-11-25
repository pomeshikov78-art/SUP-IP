#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
СУП-ИП. Испытания реестра решений (MTG-11) и карточки решения (MTG-12).
ТТ-2, экранные требования §14–15, ТЗ M16 §2.10/§2.12.
Запуск при работающем сервере:  python3 tests/decisions_test.py --base http://127.0.0.1:8000
Проверяется: регистрация решения со сквозным номером и всеми полями (тип, статус, основание,
критерии, даты, участники), реестр с фильтрами и сводкой, карточка с пятью вкладками,
переходы статуса по §14.4 (включая автопереход «Изменено» и запрет «Исполнено» без
выполненных обязательных действий), комментарии, связи с проектами/задачами/рисками,
документы M09 с версией и ссылки ЛВС, уведомления, права ролей, аудит, нормализация
записей прежних версий, выгрузки реестра и карточки (DOCX/XLSX/PDF/CSV/HTML),
резервная копия и восстановление.
В начале и в конце испытаний восстанавливается эталонный набор (admin.reset).
Только стандартная библиотека Python.
"""
import argparse, datetime, json, re, sys, urllib.request, urllib.error, http.cookiejar

BASE = "http://127.0.0.1:8000"
ok = fail = 0

def check(name, cond, extra=""):
    global ok, fail
    if cond: ok += 1; print("  ok  " + name)
    else: fail += 1; print("  FAIL " + name + ("  " + str(extra) if extra else ""))

def D(off):
    return (datetime.date.today() + datetime.timedelta(days=off)).isoformat()

YEAR = datetime.date.today().year

class Client:
    def __init__(self):
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.state = None
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
            try: return e.code, json.loads(payload)
            except Exception: return e.code, (payload if raw else {})
    def login(self, login, password="demo"):
        c, j = self.req("POST", "/api/login", {"login": login, "password": password})
        self.state = j.get("state")
        return c, j
    def rpc(self, action, payload=None):
        c, j = self.req("POST", "/api/rpc", {"action": action, "payload": payload or {}})
        if j.get("state"): self.state = j["state"]
        return c, j
    def res(self, action, payload=None):
        return self.rpc(action, payload)[1].get("result")

def lst(cl, **f):
    return cl.res("meeting.decisions.list", f) or {}

def card(cl, did):
    return cl.res("meeting.decisions.get", {"decisionId": did}) or {}

def row(cl, did):
    return next((r for r in lst(cl)["rows"] if r["id"] == did), {})

def notifs(cl, type_, obj_id):
    cl.rpc("notif.list")
    return [n for n in cl.state["notifications"] if n["type"] == type_ and n["obj_id"] == obj_id]

def events(cl, ev):
    return [e for e in cl.state.get("events", [])
            if isinstance(e.get("details"), dict) and ev in (
                [e["details"].get("event")] + (e["details"].get("events") or []))]

def export(cl, kind, fmt, id_=None, params=None):
    p = {"kind": kind, "fmt": fmt}
    if id_: p["id"] = id_
    if params: p["params"] = params
    return cl.req("POST", "/api/export", p, raw=True)

def main():
    global BASE
    ap = argparse.ArgumentParser(); ap.add_argument("--base", default=BASE)
    BASE = ap.parse_args().base.rstrip("/")

    adm = Client(); c, j = adm.login("admin")
    check("вход администратора", c == 200 and j.get("ok"), c)
    c, j = adm.rpc("admin.reset")
    check("подготовка эталонного набора", c == 200 and j.get("ok"), j.get("error"))

    print("== Реестр решений MTG-11 на демо-наборе ==")
    r = lst(adm)
    base_all = r["all"]
    check("реестр доступен", r.get("total", 0) > 0, r.get("total"))
    check("реестр содержит справочники типов и статусов",
          len(r["types"]) == 9 and len(r["statuses"]) == 7, (r["types"], r["statuses"]))
    check("сводка содержит все счётчики",
          all(k in r["stats"] for k in ("total", "active", "done", "cancelled", "overdue", "noTasks", "ready", "tasks")),
          list(r["stats"]))
    check("разбивка по статусам и типам присутствует", len(r["byStatus"]) == 7 and len(r["byType"]) == 9)
    demo_row = r["rows"][0]
    check("строка реестра содержит столбцы §14.2",
          all(k in demo_row for k in ("id", "code", "text", "type", "status", "respName", "due",
                                      "meetingCode", "protocolNumber", "agendaN", "itemTitle",
                                      "tasksCount", "overdue", "canComplete")), sorted(demo_row)[:5])
    check("демо-решения нормализованы: номер и статус",
          all(x["code"] and x["status"] in r["statuses"] for x in r["rows"]))
    check("реестр отсортирован от новых совещаний к старым",
          [x["meetingDate"] for x in r["rows"]] == sorted((x["meetingDate"] for x in r["rows"]), reverse=True))

    exe = Client(); exe.login("novikov")     # u5 — исполнитель
    pm = Client(); pm.login("volkova")       # u3 — руководитель проектов
    hd = Client(); hd.login("morozov")       # u2 — руководитель
    lbd = Client(); lbd.login("lebedev")     # u4 — руководитель проектов
    obs = Client(); obs.login("zaytseva")    # u8 — наблюдатель

    print("== Регистрация решения: проверка входных данных ==")
    c, j = adm.rpc("meeting.create", {"title": "Испытания реестра решений", "type": "Оперативное", "date": D(0),
                                      "time": "11:00", "chair": "u2", "secretary": "u3", "attendees": ["u5", "u6"]})
    m = j.get("result") or {}; mid = m.get("id")
    check("совещание создано", c == 200 and mid, j.get("error"))
    adm.rpc("meeting.agenda.add", {"id": mid, "title": "Испытания стенда", "reporter": "u5"})
    adm.rpc("meeting.agenda.add", {"id": mid, "title": "Ресурсы проекта", "reporter": "u3"})
    agenda = (adm.res("meeting.get", {"id": mid}) or m).get("agenda") or adm.state and \
        next(x for x in adm.state["meetings"] if x["id"] == mid)["agenda"]
    item2 = agenda[1]["id"]
    for name, payload in (
        ("формулировка обязательна", {"resp": "u5", "due": D(5)}),
        ("ответственный обязателен", {"text": "Решение", "due": D(5)}),
        ("срок обязателен", {"text": "Решение", "resp": "u5"}),
        ("некорректная дата срока отклоняется", {"text": "Решение", "resp": "u5", "due": "32.13.2026"}),
        ("неизвестный тип отклоняется", {"text": "Решение", "resp": "u5", "due": D(5), "type": "Несуществующий"}),
        ("неизвестный статус отклоняется", {"text": "Решение", "resp": "u5", "due": D(5), "status": "Черновик"}),
        ("несуществующий ответственный отклоняется", {"text": "Решение", "resp": "u99", "due": D(5)}),
        ("несуществующий пункт повестки отклоняется", {"text": "Решение", "resp": "u5", "due": D(5), "agendaN": 9}),
        ("вступление в силу раньше принятия отклоняется",
         {"text": "Решение", "resp": "u5", "due": D(5), "adoptedAt": D(0), "effectiveFrom": D(-1)}),
    ):
        c, j = adm.rpc("meeting.decision.add", dict(payload, id=mid))
        check(name, c == 400, (c, j.get("error")))
    c, j = adm.rpc("meeting.decision.add", {"id": mid, "text": "Решение", "resp": "u5", "due": D(5), "itemId": "ag_none"})
    check("несуществующий вопрос повестки — 404", c == 404, c)

    print("== Регистрация решения со всеми полями MTG-11 §14.1 ==")
    c, j = adm.rpc("meeting.decision.add", {
        "id": mid, "text": "Провести повторные испытания стенда АКИ-12", "resp": "u5", "due": D(10),
        "type": "Техническое", "status": "Проект", "itemId": item2,
        "basis": "Протокол предыдущего совещания, замечания испытательной группы",
        "criteria": "Отчёт подписан испытательной группой", "adoptedAt": D(0), "effectiveFrom": D(2),
        "participants": ["u6", "u7", "u6"], "projects": ["p1"]})
    m = j.get("result") or {}
    d1 = (m.get("decisions") or [{}])[0]; did1 = d1.get("id")
    check("решение зарегистрировано", c == 200 and did1, j.get("error"))
    check("самостоятельный идентификатор md_", str(did1).startswith("md_"), did1)
    check("сквозной номер формата Р-ГОД-NNN", bool(re.match(r"^Р-%d-\d{3}$" % YEAR, d1.get("code", ""))), d1.get("code"))
    check("тип сохранён", d1["type"] == "Техническое")
    check("начальный статус «Проект»", d1["status"] == "Проект")
    check("вопрос повестки связан (itemId и agendaN)", d1["itemId"] == item2 and d1["agendaN"] == 2)
    check("основание сохранено", d1["basis"].startswith("Протокол предыдущего"))
    check("критерии результата сохранены", d1["criteria"].startswith("Отчёт подписан"))
    check("дата принятия и вступления в силу сохранены", d1["adoptedAt"] == D(0) and d1["effectiveFrom"] == D(2))
    check("участники сохранены без повторов", d1["participants"] == ["u6", "u7"], d1["participants"])
    check("проект связан при регистрации", d1["projects"] == ["p1"])
    check("автор и дата регистрации зафиксированы", d1["createdBy"] == "u1" and bool(d1["createdAt"]))
    check("история содержит регистрацию", any(h["action"] == "register" for h in d1["history"]))
    n1 = int(d1["code"].split("-")[-1])
    c, j = adm.rpc("meeting.decision.add", {"id": mid, "text": "Выделить дополнительные ресурсы проекту",
                                            "resp": "u3", "due": D(-2), "type": "Ресурсное", "agendaN": 2})
    d2 = (j.get("result") or {}).get("decisions", [{}, {}])[1]; did2 = d2.get("id")
    check("второе решение зарегистрировано", c == 200 and did2, j.get("error"))
    check("номер сквозной: следующий по порядку", int(d2["code"].split("-")[-1]) == n1 + 1, d2["code"])
    check("статус по умолчанию «Принято»", d2["status"] == "Принято")
    check("дата принятия по умолчанию — дата совещания", d2["adoptedAt"] == D(0))
    c, j = adm.rpc("meeting.decision.add", {"id": mid, "text": "Информационное решение без поручений",
                                            "resp": "u6", "due": D(15), "type": "Информационное"})
    d3 = (j.get("result") or {}).get("decisions", [{}, {}, {}])[2]; did3 = d3.get("id")
    check("третье решение без повестки", c == 200 and d3["agendaN"] is None and d3["itemId"] == "")

    print("== Фильтры и сводка реестра ==")
    r = lst(adm, meetingId=mid)
    check("отбор по совещанию", r["total"] == 3 and all(x["meetingId"] == mid for x in r["rows"]), r["total"])
    check("общее число не зависит от отбора", r["all"] == base_all + 3, (r["all"], base_all))
    check("отбор по типу", all(x["type"] == "Техническое" for x in lst(adm, type="Техническое")["rows"]))
    check("отбор по статусу", all(x["status"] == "Проект" for x in lst(adm, status="Проект")["rows"]))
    check("отбор по ответственному", {x["id"] for x in lst(adm, resp="u3")["rows"]} >= {did2})
    check("отбор по вопросу повестки", {x["id"] for x in lst(adm, itemId=item2)["rows"]} == {did1, did2})
    check("отбор по проекту", {x["id"] for x in lst(adm, projectId="p1")["rows"]} >= {did1})
    check("отбор по сроку (от/до)", {x["id"] for x in lst(adm, **{"from": D(9), "to": D(11)})["rows"]} == {did1})
    check("отбор по дате принятия", all(x["adoptedAt"] >= D(0) for x in lst(adm, adoptedFrom=D(0))["rows"]))
    check("отбор просроченных", {x["id"] for x in lst(adm, overdue=1)["rows"]} >= {did2}
          and all(x["overdue"] for x in lst(adm, overdue=1)["rows"]))
    check("просрочка считает дни", row(adm, did2)["overdueDays"] == 2, row(adm, did2)["overdueDays"])
    check("отбор без поручений", {x["id"] for x in lst(adm, noTasks=1)["rows"]} >= {did1, did2, did3})
    check("отбор действующих", all(x["status"] in ("Проект", "На согласовании", "Принято", "Действует", "Изменено")
                                   for x in lst(adm, active=1)["rows"]))
    r = lst(adm, q="АКИ-12")
    check("поиск по формулировке", {x["id"] for x in r["rows"]} >= {did1}, r["total"])
    r = lst(adm, q="испытания реестра")
    check("поиск по названию совещания", r["total"] == 3, r["total"])
    c, j = adm.rpc("meeting.decisions.list", {"type": "Несуществующий"})
    check("неизвестный тип в отборе отклоняется", c == 400, c)
    c, j = adm.rpc("meeting.decisions.list", {"status": "Черновик"})
    check("неизвестный статус в отборе отклоняется", c == 400, c)
    r = lst(adm, meetingId=mid)
    check("сводка отбора: всего/действующих/просрочено/без поручений",
          r["stats"]["total"] == 3 and r["stats"]["active"] == 3 and r["stats"]["overdue"] == 1
          and r["stats"]["noTasks"] == 3, r["stats"])

    print("== Карточка решения MTG-12 ==")
    cd = card(adm, did1)
    check("карточка открывается по самостоятельному идентификатору", cd.get("id") == did1)
    check("карточка содержит поля пяти вкладок",
          all(k in cd for k in ("basis", "criteria", "result", "participants", "participantsNames",
                                "taskRows", "projectRows", "linkRows", "riskRows", "docRows",
                                "commentRows", "historyRows", "readiness", "transitions")), sorted(cd)[:5])
    check("имена участников раскрыты", len(cd["participantsNames"]) == 2 and all(cd["participantsNames"]))
    check("связанный проект раскрыт", cd["projectRows"][0]["code"] == "П-2026-01", cd["projectRows"])
    check("карточка ссылается на протокол", bool(cd["protocolNumber"]) and cd["protocolStatus"] == "Подготовка",
          (cd.get("protocolNumber"), cd.get("protocolStatus")))
    check("переходы из «Проект» по §14.4", cd["transitions"] == ["На согласовании", "Принято", "Отменено"], cd["transitions"])
    check("готовность: поручений нет, результат не подтверждён",
          cd["readiness"]["canComplete"] is False and cd["readiness"]["tasks"] == 0)
    c, j = adm.rpc("meeting.decisions.get", {"decisionId": "md_none"})
    check("несуществующее решение — 404", c == 404, c)
    c, j = adm.rpc("meeting.decisions.get", {})
    check("без идентификатора — ошибка", c == 400, c)
    c, j = adm.rpc("meeting.decisions.get", {"id": mid, "decisionId": did2})
    check("карточка с указанием совещания", c == 200 and j["result"]["id"] == did2)

    print("== Переходы статуса (MTG-11 §14.4) ==")
    c, j = adm.rpc("meeting.decisions.status", {"decisionId": did1, "to": "Действует"})
    check("недопустимый переход «Проект»→«Действует» отклоняется", c == 400, c)
    c, j = adm.rpc("meeting.decisions.status", {"decisionId": did1, "to": "Несуществующий"})
    check("неизвестный статус отклоняется", c == 400, c)
    c, j = adm.rpc("meeting.decisions.status", {"decisionId": did1, "to": "Проект"})
    check("переход в текущий статус отклоняется", c == 400, c)
    c, j = adm.rpc("meeting.decisions.status", {"decisionId": did1, "to": "Отменено"})
    check("отмена без основания отклоняется", c == 400, c)
    c, j = adm.rpc("meeting.decisions.status", {"decisionId": did1, "to": "На согласовании"})
    check("«Проект»→«На согласовании»", c == 200 and j["result"]["decision"]["status"] == "На согласовании", j.get("error"))
    c, j = adm.rpc("meeting.decisions.status", {"decisionId": did1, "to": "Принято"})
    check("«На согласовании»→«Принято»", c == 200 and j["result"]["decision"]["status"] == "Принято")
    c, j = adm.rpc("meeting.decisions.status", {"decisionId": did1, "to": "Исполнено"})
    check("«Исполнено» без обязательных действий отклоняется", c == 400 and "недоступен" in j.get("error", {}).get("message", ""), j.get("error"))
    chk = adm.res("meeting.decisions.check", {"decisionId": did1})
    check("проверка готовности объясняет причину", chk["canComplete"] is False and "результат" in chk["reason"], chk["reason"])
    check("проверка готовности возвращает переходы", chk["transitions"] == ["Действует", "Изменено", "Отменено", "Исполнено"], chk["transitions"])
    c, j = adm.rpc("meeting.decisions.status", {"decisionId": did1, "to": "Действует"})
    check("«Принято»→«Действует»", c == 200)
    cd = card(adm, did1)
    check("дата вступления в силу сохранена при переходе", cd["effectiveFrom"] == D(2))
    check("автор и дата смены статуса зафиксированы", cd["statusBy"] == "u1" and bool(cd["statusAt"]))
    check("история содержит переходы", sum(1 for h in cd["historyRows"] if h["action"] == "status") == 3)

    print("== Автопереход «Изменено» при правке существенных условий ==")
    c, j = adm.rpc("meeting.decisions.update", {"decisionId": did1, "basis": "Уточнённое основание"})
    check("правка несущественного поля статус не меняет",
          c == 200 and j["result"]["decision"]["status"] == "Действует", j.get("error"))
    c, j = adm.rpc("meeting.decisions.update", {"decisionId": did1, "due": D(12)})
    cd = j.get("result", {}).get("decision", {})
    check("правка срока переводит в «Изменено»", c == 200 and cd["status"] == "Изменено", cd.get("status"))
    check("автопереход записан в историю с основанием",
          any(h["action"] == "status" and h.get("to") == "Изменено" and "существенные" in h.get("reason", "")
              for h in cd["historyRows"]))
    c, j = adm.rpc("meeting.decisions.update", {"decisionId": did1, "text": "Провести повторные испытания стенда АКИ-12 (ред. 2)",
                                                "criteria": "Отчёт подписан и передан РП"})
    check("правка формулировки и критериев в «Изменено» допустима", c == 200 and j["result"]["decision"]["status"] == "Изменено")
    c, j = adm.rpc("meeting.decisions.update", {"decisionId": did1})
    check("правка без изменений отклоняется", c == 400, c)
    c, j = adm.rpc("meeting.decisions.update", {"decisionId": did1, "text": "x" * 2001})
    check("формулировка длиннее 2000 символов отклоняется", c == 400, c)
    c, j = adm.rpc("meeting.decisions.update", {"decisionId": did1, "text": "   "})
    check("пустая формулировка отклоняется", c == 400, c)
    c, j = adm.rpc("meeting.decisions.update", {"decisionId": did1, "effectiveFrom": D(-5)})
    check("вступление в силу раньше принятия отклоняется при правке", c == 400, c)
    c, j = adm.rpc("meeting.decisions.update", {"decisionId": did1, "participants": ["u99"]})
    check("несуществующий участник отклоняется", c == 400, c)
    c, j = adm.rpc("meeting.decisions.status", {"decisionId": did1, "to": "Действует"})
    check("возврат «Изменено»→«Действует»", c == 200)

    print("== Поручения решения и готовность к «Исполнено» ==")
    c, j = adm.rpc("meeting.decision.toTask", {"id": mid, "did": did1, "title": "Повторные испытания АКИ-12",
                                               "assignee": "u5", "due": D(9), "projectId": "p1",
                                               "criteria": "Отчёт подписан испытательной группой"})
    check("поручение создано из решения", c == 200 and j.get("ok"), j.get("error"))
    tid = card(adm, did1)["taskRows"][0]["id"]
    cd = card(adm, did1)
    check("карточка показывает поручение", cd["tasksCount"] == 1 and cd["taskRows"][0]["assigneeName"] == "Новиков Сергей", cd["taskRows"])
    check("критерии поручения видны в карточке", cd["taskRows"][0]["criteria"].startswith("Отчёт подписан"))
    chk = adm.res("meeting.decisions.check", {"decisionId": did1})
    check("готовность: открытое поручение перечислено", chk["canComplete"] is False and chk["open"][0]["id"] == tid, chk)
    c, j = adm.rpc("meeting.decisions.status", {"decisionId": did1, "to": "Исполнено"})
    check("«Исполнено» с открытым поручением отклоняется", c == 400, c)
    exe.rpc("task.status", {"id": tid, "to": "В работе"})
    exe.rpc("task.status", {"id": tid, "to": "На проверке", "result": "Испытания проведены, отчёт подготовлен"})
    hd.rpc("directive.accept", {"id": tid, "verdict": "Принято", "comment": "Отчёт принят"})
    chk = adm.res("meeting.decisions.check", {"decisionId": did1})
    check("после приёмки поручения решение готово к исполнению", chk["canComplete"] is True and chk["done"] == 1, chk)
    check("причина готовности — все поручения исполнены", "Все поручения исполнены" in chk["reason"], chk["reason"])
    r = lst(adm, ready=1)
    check("отбор готовых к исполнению", {x["id"] for x in r["rows"]} >= {did1}, r["total"])
    c, j = adm.rpc("meeting.decisions.status", {"decisionId": did1, "to": "Исполнено"})
    check("«Действует»→«Исполнено» после выполнения", c == 200 and j["result"]["decision"]["status"] == "Исполнено", j.get("error"))
    cd = card(adm, did1)
    check("терминальный статус: переходов нет", cd["transitions"] == [])
    c, j = adm.rpc("meeting.decisions.update", {"decisionId": did1, "text": "Правка исполненного"})
    check("исполненное решение не редактируется", c == 400, c)
    c, j = adm.rpc("meeting.decisions.status", {"decisionId": did1, "to": "Действует"})
    check("из исполненного переходов нет", c == 400, c)
    c, j = adm.rpc("meeting.decisions.status", {"decisionId": did3, "to": "Отменено", "reason": "Вопрос утратил актуальность"})
    check("отмена с основанием выполняется", c == 200 and j["result"]["decision"]["status"] == "Отменено")
    c, j = adm.rpc("meeting.decisions.update", {"decisionId": did3, "due": D(20)})
    check("отменённое решение не редактируется", c == 400, c)
    c, j = adm.rpc("meeting.decision.add", {"id": mid, "text": "Решение с подтверждённым результатом",
                                            "resp": "u6", "due": D(6), "type": "Организационное"})
    d4 = (j.get("result") or {}).get("decisions", [])[-1]; did4 = d4.get("id")
    c, j = adm.rpc("meeting.decisions.status", {"decisionId": did4, "to": "Исполнено",
                                                "result": "Мероприятие проведено, акт подписан"})
    check("«Исполнено» без поручений, но с результатом", c == 200, j.get("error"))
    cd = card(adm, did4)
    check("результат зафиксирован с автором и датой",
          cd["result"] == "Мероприятие проведено, акт подписан" and cd["resultBy"] == "u1" and bool(cd["resultAt"]))
    check("просрочка не начисляется исполненному решению", row(adm, did1)["overdue"] is False)

    print("== Комментарии ==")
    c, j = adm.rpc("meeting.decisions.comment", {"decisionId": did2, "text": "Уточнить потребность в ресурсах у РП"})
    check("комментарий добавлен", c == 200 and j["result"]["comment"]["by"] == "u1", j.get("error"))
    pm.rpc("meeting.decisions.comment", {"decisionId": did2, "text": "Потребность подтверждена, расчёт приложен"})
    cd = card(adm, did2)
    check("комментарии видны с именами авторов", len(cd["commentRows"]) == 2 and cd["commentRows"][1]["byName"] == "Волкова Елена", cd["commentRows"])
    check("комментарий записан в историю", any(h["action"] == "comment" for h in cd["historyRows"]))
    c, j = adm.rpc("meeting.decisions.comment", {"decisionId": did2, "text": "  "})
    check("пустой комментарий отклоняется", c == 400, c)
    c, j = adm.rpc("meeting.decisions.comment", {"decisionId": did2, "text": "x" * 2001})
    check("комментарий длиннее 2000 символов отклоняется", c == 400, c)

    print("== Связанные объекты: проекты, задачи, риски ==")
    c, j = adm.rpc("meeting.decisions.link", {"decisionId": did2, "kind": "project", "targetId": "p2"})
    check("проект связан", c == 200 and "p2" in [x["id"] for x in j["result"]["decision"]["projectRows"]], j.get("error"))
    c, j = adm.rpc("meeting.decisions.link", {"decisionId": did2, "kind": "project", "targetId": "p2"})
    check("повторная связь отклоняется", c == 400, c)
    c, j = adm.rpc("meeting.decisions.link", {"decisionId": did2, "kind": "task", "targetId": "t1"})
    check("задача связана", c == 200 and j["result"]["decision"]["linkRows"][0]["code"].startswith("З-"), j.get("error"))
    c, j = adm.rpc("meeting.decisions.link", {"decisionId": did2, "kind": "risk", "targetId": "r1"})
    check("риск связан", c == 200 and bool(j["result"]["decision"]["riskRows"]), j.get("error"))
    c, j = adm.rpc("meeting.decisions.link", {"decisionId": did2, "kind": "doc", "targetId": "d1"})
    check("неизвестный вид связи отклоняется", c == 400, c)
    c, j = adm.rpc("meeting.decisions.link", {"decisionId": did2, "kind": "project", "targetId": "p_none"})
    check("несуществующий объект связи — 404", c == 404, c)
    c, j = adm.rpc("meeting.decisions.link", {"decisionId": did2, "kind": "task"})
    check("без объекта связи — ошибка", c == 400, c)
    c, j = adm.rpc("meeting.decisions.unlink", {"decisionId": did2, "kind": "task", "targetId": "t1"})
    check("связь с задачей снята", c == 200 and j["result"]["decision"]["linkRows"] == [])
    c, j = adm.rpc("meeting.decisions.unlink", {"decisionId": did2, "kind": "task", "targetId": "t1"})
    check("повторное снятие — 404", c == 404, c)
    cd = card(adm, did2)
    check("связи записаны в историю", sum(1 for h in cd["historyRows"] if h["action"] in ("link", "unlink")) == 4)

    print("== Документы решения: M09 с версией и ссылки ЛВС ==")
    doc_id = adm.state["docs"][0]["id"]
    c, j = adm.rpc("meeting.decisions.attach", {"decisionId": did2, "docId": doc_id, "note": "Основание решения"})
    check("документ M09 приложен", c == 200 and j["result"]["doc"]["kind"] == "doc", j.get("error"))
    att = j["result"]["doc"]
    check("версия документа зафиксирована", att["docV"] == max(v["v"] for v in adm.state["docs"][0]["versions"]) if adm.state["docs"][0].get("versions") else att["docV"] == 1)
    check("имя и автор приложения сохранены", att["name"].startswith("Д-") and att["addedBy"] == "u1", att)
    c, j = adm.rpc("meeting.decisions.attach", {"decisionId": did2, "docId": doc_id})
    check("повторное приложение той же версии отклоняется", c == 400, c)
    c, j = adm.rpc("meeting.decisions.attach", {"decisionId": did2, "docId": doc_id, "docV": 99})
    check("несуществующая версия — 404", c == 404, c)
    c, j = adm.rpc("meeting.decisions.attach", {"decisionId": did2, "docId": "d_none"})
    check("несуществующий документ — 404", c == 404, c)
    c, j = adm.rpc("meeting.decisions.attach", {"decisionId": did2, "name": "Регламент испытаний",
                                                "url": "\\\\lvs-srv\\docs\\reglament.pdf", "note": "Файловый ресурс ЛВС"})
    check("ссылка ЛВС приложена", c == 200 and j["result"]["doc"]["kind"] == "link", j.get("error"))
    link_att = j["result"]["doc"]
    c, j = adm.rpc("meeting.decisions.attach", {"decisionId": did2, "name": "Скрипт", "url": "javascript:alert(1)"})
    check("схема javascript: отклоняется", c == 400, c)
    c, j = adm.rpc("meeting.decisions.attach", {"decisionId": did2, "name": "Дубль", "url": "\\\\lvs-srv\\docs\\reglament.pdf"})
    check("повторная ссылка отклоняется", c == 400, c)
    c, j = adm.rpc("meeting.decisions.attach", {"decisionId": did2, "url": "\\\\srv\\x"})
    check("ссылка без имени отклоняется", c == 400, c)
    cd = card(adm, did2)
    check("карточка показывает документы с кодом M09", cd["docsCount"] == 2 and cd["docRows"][0]["docCode"].startswith("Д-"), cd["docRows"])
    check("автор приложения раскрыт", cd["docRows"][0]["authorName"] == "Соколов Артём", cd["docRows"][0])
    c, j = adm.rpc("meeting.decisions.detach", {"decisionId": did2, "attachId": link_att["id"]})
    check("ссылка откреплена", c == 200 and j["result"]["decision"]["docsCount"] == 1)
    c, j = adm.rpc("meeting.decisions.detach", {"decisionId": did2, "attachId": "dd_none"})
    check("несуществующее приложение — 404", c == 404, c)

    print("== Уведомления ответственному и участникам ==")
    check("ответственный уведомлён о регистрации", len(notifs(exe, "decision.registered", did1)) == 1)
    koz = Client(); koz.login("kozlova")   # u6 — участник
    orl = Client(); orl.login("orlov")     # u7 — участник
    check("участник u6 уведомлён о регистрации", len(notifs(koz, "decision.registered", did1)) == 1)
    check("участник u7 уведомлён о регистрации", len(notifs(orl, "decision.registered", did1)) == 1)
    check("уведомление о регистрации содержит номер и тип",
          "Р-" in notifs(exe, "decision.registered", did1)[0]["title"] and "Техническое" in notifs(exe, "decision.registered", did1)[0]["title"])
    ns = notifs(exe, "decision.status", did1)
    check("ответственный уведомлён о сменах статуса", len(ns) >= 3, len(ns))
    check("уведомление о исполнении содержит статус", any("Исполнено" in n["title"] for n in ns))
    check("посторонний не уведомляется", len(notifs(obs, "decision.registered", did1)) == 0)

    print("== Права ролей ==")
    c, j = exe.rpc("meeting.decisions.list")
    check("exec: реестр доступен", c == 200)
    c, j = exe.rpc("meeting.decisions.get", {"decisionId": did2})
    check("exec: карточка доступна", c == 200)
    c, j = exe.rpc("meeting.decisions.update", {"decisionId": did2, "due": D(8)})
    check("exec: правка чужого решения запрещена", c == 403, c)
    c, j = exe.rpc("meeting.decisions.status", {"decisionId": did2, "to": "Отменено", "reason": "x"})
    check("exec: смена статуса запрещена", c == 403, c)
    c, j = obs.rpc("meeting.decisions.comment", {"decisionId": did2, "text": "Комментарий наблюдателя"})
    check("наблюдатель: комментарий запрещён", c == 403, c)
    c, j = obs.rpc("meeting.decisions.attach", {"decisionId": did2, "name": "x", "url": "\\\\srv\\y"})
    check("наблюдатель: приложение документа запрещено", c == 403, c)
    c, j = pm.rpc("meeting.decisions.update", {"decisionId": did2, "basis": "Уточнение РП"})
    check("руководитель проектов: правка разрешена", c == 200, j.get("error"))
    c, j = lbd.rpc("meeting.decisions.comment", {"decisionId": did2, "text": "Принято к сведению"})
    check("второй руководитель проектов: комментарий разрешён", c == 200, j.get("error"))
    c, b = obs.req("POST", "/api/export", {"kind": "decisions", "fmt": "csv"}, raw=True)
    check("наблюдатель: выгрузка реестра запрещена", c == 403, c)

    print("== Аудит событий ==")
    adm.rpc("notif.list")
    for ev, label in (("MEETING_DECISION_REGISTERED", "регистрация"),
                      ("MEETING_DECISION_UPDATED", "правка"),
                      ("MEETING_DECISION_CHANGED", "изменение"),
                      ("MEETING_DECISION_COMPLETED", "исполнение"),
                      ("MEETING_DECISION_CANCELLED", "отмена"),
                      ("MEETING_DECISION_COMMENT", "комментарий"),
                      ("MEETING_DECISION_LINKED", "связь"),
                      ("MEETING_DECISION_UNLINKED", "снятие связи"),
                      ("MEETING_DECISION_DOC_ATTACHED", "приложение документа"),
                      ("MEETING_DECISION_DOC_DETACHED", "открепление документа")):
        check("аудит: событие %s (%s)" % (ev, label), bool(events(adm, ev)))
    reg_ev = events(adm, "MEETING_DECISION_REGISTERED")
    check("аудит регистрации содержит тип и статус",
          any(e["details"].get("type") == "Техническое" and e["details"].get("status") == "Проект" for e in reg_ev))

    print("== Выгрузки реестра и карточки ==")
    for fmt, sig in (("csv", b"\xef\xbb\xbf"), ("html", b"<"), ("docx", b"PK"), ("xlsx", b"PK"), ("pdf", b"%PDF")):
        c, b = export(adm, "decisions", fmt)
        check("реестр решений: %s" % fmt.upper(), c == 200 and len(b) > 200 and b.startswith(sig), (c, len(b) if isinstance(b, bytes) else b))
    c, b = export(adm, "decisions", "csv", params={"meetingId": mid})
    check("выгрузка реестра с отбором: только решения совещания",
          c == 200 and m["code"].encode("utf-8") in b and "-Р1".encode("utf-8") not in b, c)
    for fmt, sig in (("html", b"<"), ("docx", b"PK"), ("xlsx", b"PK"), ("pdf", b"%PDF"), ("csv", b"\xef\xbb\xbf")):
        c, b = export(adm, "decision", fmt, id_=did2)
        check("карточка решения: %s" % fmt.upper(), c == 200 and len(b) > 100 and b.startswith(sig), (c, len(b) if isinstance(b, bytes) else b))
    c, b = export(adm, "decision", "html", id_=did1)
    check("карточка содержит поручение и историю", "Повторные испытания".encode("utf-8") in b and "История".encode("utf-8") in b, c)
    c, j = export(adm, "decision", "html", id_="md_none")
    check("выгрузка несуществующей карточки — 404", c == 404, c)

    print("== Нормализация записей прежних версий ==")
    c, backup = adm.req("GET", "/api/backup", raw=True)
    backup = json.loads(backup)
    check("копия содержит 17 таблиц", len(backup["tables"]) == 17, len(backup["tables"]))
    old = json.loads(json.dumps(backup))
    for mrow in old["tables"]["meetings"]:
        for dd in mrow.get("decisions") or []:
            for k in ("code", "type", "status", "adoptedAt", "effectiveFrom", "basis", "criteria",
                      "participants", "projects", "tasks", "risks", "docs", "comments", "history",
                      "taskIds", "itemId", "result"):
                dd.pop(k, None)
    c, j = adm.req("POST", "/api/restore", old)
    check("копия с решениями прежнего формата восстанавливается", c == 200 and j.get("ok"), (c, j.get("error")))
    r = lst(adm)
    check("старые решения нормализованы: номер из кода совещания",
          any(re.match(r"^СОВ-\d{4}-\d{3}-Р\d+$", x["code"]) for x in r["rows"]),
          [x["code"] for x in r["rows"]][:6])
    check("старые решения получают статус «Принято» и тип «Иное»",
          all(x["status"] in r["statuses"] and x["type"] in r["types"] for x in r["rows"]))
    legacy = next(x for x in r["rows"] if "-Р" in x["code"] and not x["code"].startswith("Р-"))
    check("дата принятия старой записи — дата совещания", legacy["adoptedAt"] == legacy["meetingDate"], legacy)
    c, j = adm.req("POST", "/api/restore", backup)
    check("полная копия восстанавливается", c == 200 and j.get("ok"), (c, j.get("error")))
    check("после восстановления карточка решения сохранена", card(adm, did2)["docsCount"] == 1)
    c, j = adm.req("GET", "/api/health")
    check("сервер работает на PostgreSQL", j.get("db") == "PostgreSQL", j)

    print("== Сброс демо-данных ==")
    c, j = adm.rpc("admin.reset")
    check("сброс выполнен", c == 200 and j.get("ok"), j.get("error"))
    check("испытательные решения удалены", lst(adm)["all"] == base_all, (lst(adm)["all"], base_all))
    check("испытательное совещание удалено", not any(m["title"] == "Испытания реестра решений" for m in adm.state["meetings"]))

    print("\nИТОГ: %d ok, %d FAIL" % (ok, fail))
    sys.exit(1 if fail else 0)

if __name__ == "__main__":
    main()