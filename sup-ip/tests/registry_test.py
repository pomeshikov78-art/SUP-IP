#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
СУП-ИП. Испытания реестра поручений и контроля исполнения (ТЗ M16 §2.11, §19.5–19.6).
Запуск при работающем сервере:  python3 tests/registry_test.py --base http://127.0.0.1:8000
Проверяется: состав реестра (номер, совещание, № протокола, решение, исполнитель, соисполнители,
срок, статус, %, дата факта, результат, комментарий, документы), 8 статусов поручений и их
синхронизация со связанной задачей, отдельная приёмка результата руководителем, продление срока
с историей и основанием, подтверждающие документы из M09, фильтры, права, уведомления, аудит.
В начале и в конце испытаний восстанавливается эталонный набор (admin.reset).
Только стандартная библиотека Python.
"""
import argparse, datetime, json, sys, urllib.request, urllib.error, http.cookiejar

BASE = "http://127.0.0.1:8000"
ok = fail = 0

def check(name, cond, extra=""):
    global ok, fail
    if cond: ok += 1; print("  ok  " + name)
    else: fail += 1; print("  FAIL " + name + ("  " + str(extra) if extra else ""))

def D(off):
    return (datetime.date.today() + datetime.timedelta(days=off)).isoformat()

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
            except Exception: return e.code, {}
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

def reg_row(cl, tid):
    r = cl.res("directive.list", {}) or {}
    return next((x for x in r.get("rows", []) if x["id"] == tid), {})

def notifs(cl, type_, obj_id):
    cl.rpc("notif.list")
    return [n for n in cl.state["notifications"] if n["type"] == type_ and n["obj_id"] == obj_id]

def main():
    global BASE
    ap = argparse.ArgumentParser(); ap.add_argument("--base", default=BASE)
    BASE = ap.parse_args().base.rstrip("/")

    adm = Client(); c, j = adm.login("admin")
    check("вход администратора", c == 200 and j.get("ok"), c)
    c, j = adm.rpc("admin.reset")
    check("подготовка эталонного набора", c == 200 and j.get("ok"), j.get("error"))

    exe = Client(); exe.login("novikov")     # u5 — исполнитель
    co = Client(); co.login("kozlova")       # u6 — соисполнитель
    head = Client(); head.login("morozov")   # u2 — руководитель подразделения
    pm = Client(); pm.login("volkova")       # u3 — руководитель проектов (без права утверждения совещаний)
    obs = Client(); obs.login("zaytseva")    # u8 — наблюдатель

    print("== Подготовка поручения ==")
    c, j = adm.rpc("meeting.create", {"title": "Испытания реестра поручений", "type": "Оперативное", "date": D(0),
                                      "time": "09:00", "chair": "u2", "secretary": "u3", "attendees": ["u5", "u6"]})
    mid = (j.get("result") or {}).get("id")
    adm.rpc("meeting.agenda.add", {"id": mid, "title": "Исполнение решений", "reporter": "u3"})
    c, j = adm.rpc("meeting.decision.add", {"id": mid, "text": "Подготовить пакет отчётности по этапу",
                                            "resp": "u5", "due": D(3), "agendaN": 1, "createTask": True,
                                            "coassignees": ["u6"], "criteria": "Пакет отчётности согласован"})
    d = (j.get("result") or {}).get("decisions", [{}])[0]
    tid = (d.get("taskIds") or [None])[0]
    check("поручение создано", c == 200 and tid, j.get("error"))

    print("== Состав реестра (ТЗ §2.11) ==")
    r = adm.res("directive.list", {})
    check("реестр доступен", isinstance(r, dict) and "rows" in r and "byStatus" in r)
    row = reg_row(adm, tid)
    fields = ["code", "meetingCode", "protocolV", "decisionText", "assignee", "coassignees", "due", "status",
              "progress", "factDate", "result", "comment", "docs"]
    check("строка реестра содержит все поля §2.11", all(k in row for k in fields), [k for k in fields if k not in row])
    check("номер поручения", row.get("code", "").startswith("З-"), row.get("code"))
    check("совещание и решение в строке", row.get("meetingId") == mid and row.get("decisionN") == 1)
    check("формулировка решения перенесена", "пакет отчётности" in row.get("decisionText", "").lower(), row.get("decisionText"))
    check("исполнитель и соисполнители", row.get("assignee") == "u5" and row.get("coassignees") == ["u6"])
    check("срок исполнения", row.get("due") == D(3))
    check("исходный статус «Не начато»", row.get("status") == "Не начато", row.get("status"))
    check("процент выполнения 0", row.get("progress") == 0)
    check("дата факта и результат пусты", row.get("factDate") is None and row.get("result") == "")
    check("критерии и источник доступны", row.get("criteria") == "Пакет отчётности согласован" and "совещания" in row.get("source", ""))
    check("сводка по статусам содержит 8 статусов", len(r["byStatus"]) == 9 or len(r["byStatus"]) == 8, len(r["byStatus"]))

    print("== Синхронизация статуса со связанной задачей ==")
    exe.rpc("task.status", {"id": tid, "to": "В работе"})
    check("«В работе» после старта задачи", reg_row(adm, tid)["status"] == "В работе")
    adm.rpc("task.status", {"id": tid, "to": "Заблокирована", "blockReason": "Ожидаем данные лаборатории"})
    check("«Ожидает информации» при блокировке", reg_row(adm, tid)["status"] == "Ожидает информации")
    exe.rpc("task.status", {"id": tid, "to": "В работе"})
    exe.rpc("task.status", {"id": tid, "to": "Приостановлена"})
    check("«Приостановлено»", reg_row(adm, tid)["status"] == "Приостановлено")
    exe.rpc("task.status", {"id": tid, "to": "В работе"})
    exe.rpc("task.status", {"id": tid, "to": "На проверке", "result": "Пакет отчётности подготовлен"})
    row = reg_row(adm, tid)
    check("«На проверке»", row["status"] == "На проверке")
    check("процент выполнения синхронизирован", row["progress"] == 90, row["progress"])
    check("результат задачи виден в реестре", "Пакет отчётности подготовлен" in row["result"])

    print("== Приёмка результата руководителем (отдельный контроль) ==")
    c, j = exe.rpc("directive.accept", {"id": tid, "verdict": "Принято"})
    check("исполнитель не может принимать результат (403)", c == 403, c)
    c, j = pm.rpc("directive.accept", {"id": tid, "verdict": "Принято"})
    check("РП без права утверждения совещаний не принимает (403)", c == 403, c)
    c, j = head.rpc("directive.accept", {"id": tid, "verdict": "Непонятно"})
    check("недопустимое решение приёмки отклоняется", c == 400, c)
    c, j = head.rpc("directive.accept", {"id": tid, "verdict": "На доработку"})
    check("возврат на доработку без комментария отклоняется", c == 400, c)
    c, j = head.rpc("directive.accept", {"id": tid, "verdict": "На доработку", "comment": "Не хватает протокола измерений"})
    row = reg_row(head, tid)
    check("возврат на доработку зафиксирован", c == 200 and row["taskStatus"] == "Возвращена на доработку", j.get("error"))
    check("статус реестра «В работе» после возврата", row["status"] == "В работе", row["status"])
    check("приёмка хранит решение, автора и комментарий",
          (row["acceptance"] or {}).get("verdict") == "На доработку" and (row["acceptance"] or {}).get("by") == "u2")
    check("исполнитель уведомлён о возврате", len(notifs(exe, "directive.rework", tid)) >= 1)
    exe.rpc("task.status", {"id": tid, "to": "В работе"})
    exe.rpc("task.status", {"id": tid, "to": "На проверке", "result": "Пакет отчётности и протокол измерений"})
    c, j = head.rpc("directive.accept", {"id": tid, "verdict": "Принято", "comment": "Решение исполнено полностью"})
    row = reg_row(head, tid)
    check("результат принят руководителем", c == 200 and (row["acceptance"] or {}).get("verdict") == "Принято", j.get("error"))
    check("статус реестра «Выполнено» только после приёмки", row["status"] == "Выполнено", row["status"])
    check("задача закрыта", row["taskStatus"] == "Закрыта" and row["progress"] == 100)
    check("дата фактического исполнения зафиксирована", row["factDate"] == D(0), row["factDate"])
    check("исполнитель и соисполнитель уведомлены о приёмке",
          len(notifs(exe, "directive.accepted", tid)) >= 1 and len(notifs(co, "directive.accepted", tid)) >= 1)
    check("председатель уведомлён о приёмке", len(notifs(head, "directive.accepted", tid)) >= 1)

    print("== Технически закрытая задача ≠ принятое поручение ==")
    c, j = adm.rpc("meeting.decision.add", {"id": mid, "text": "Выпустить сводку по замечаниям", "resp": "u5",
                                            "due": D(4), "createTask": True})
    tid2 = (j.get("result") or {}).get("decisions", [{}])[-1]["taskIds"][0]
    exe.rpc("task.status", {"id": tid2, "to": "В работе"})
    exe.rpc("task.status", {"id": tid2, "to": "На проверке", "result": "Сводка готова"})
    head.rpc("task.status", {"id": tid2, "to": "Принята"})
    head.rpc("task.status", {"id": tid2, "to": "Закрыта"})
    row2 = reg_row(adm, tid2)
    check("задача закрыта, но поручение не принято", row2["taskStatus"] == "Закрыта" and row2["status"] == "Ожидает подтверждения", row2["status"])
    c, j = head.rpc("directive.accept", {"id": tid2, "verdict": "Принято", "comment": "Принято после проверки"})
    check("после приёмки статус «Выполнено»", reg_row(head, tid2)["status"] == "Выполнено")

    print("== Просрочка и отмена ==")
    c, j = adm.rpc("meeting.decision.add", {"id": mid, "text": "Поручение с истёкшим сроком", "resp": "u6",
                                            "due": D(-3), "createTask": True})
    tid3 = (j.get("result") or {}).get("decisions", [{}])[-1]["taskIds"][0]
    row3 = reg_row(adm, tid3)
    check("просрочка вычисляется автоматически", row3["status"] == "Просрочено" and row3["overdueDays"] >= 3, row3)
    adm.rpc("task.status", {"id": tid3, "to": "Отменена"})
    check("отменённая задача → «Отменено»", reg_row(adm, tid3)["status"] == "Отменено")

    print("== Продление срока с историей (ТЗ §19.6) ==")
    c, j = adm.rpc("directive.extend", {"id": tid3, "due": D(10)})
    check("продление без основания отклоняется", c == 400, c)
    c, j = adm.rpc("directive.extend", {"id": tid3, "due": D(-5), "reason": "раньше текущего"})
    check("новый срок должен быть позже текущего", c == 400, c)
    c, j = head.rpc("directive.extend", {"id": tid, "due": D(30), "reason": "Продление по решению председателя"})
    check("срок завершённого поручения не продлевается", c == 400, c)
    c, j = adm.rpc("meeting.decision.add", {"id": mid, "text": "Поручение для продления", "resp": "u5",
                                            "due": D(2), "createTask": True})
    tid4 = (j.get("result") or {}).get("decisions", [{}])[-1]["taskIds"][0]
    c, j = pm.rpc("directive.extend", {"id": tid4, "due": D(12), "reason": "Задержка поставки измерительного оборудования"})
    row4 = reg_row(pm, tid4)
    check("срок продлён", c == 200 and row4["due"] == D(12), j.get("error"))
    check("история продления содержит инициатора и основание",
          len(row4["dueHistory"]) == 1 and row4["dueHistory"][0]["by"] == "u3" and
          "Задержка поставки" in row4["dueHistory"][0]["reason"], row4["dueHistory"])
    check("история хранит прежний и новый срок",
          row4["dueHistory"][0]["from"] == D(2) and row4["dueHistory"][0]["to"] == D(12))
    check("исполнитель уведомлён о продлении", len(notifs(exe, "directive.extended", tid4)) >= 1)
    c, j = pm.rpc("directive.extend", {"id": tid4, "due": D(20), "reason": "Второе продление"})
    check("повторное продление добавляется в историю", len(reg_row(pm, tid4)["dueHistory"]) == 2)

    print("== Карточка поручения: результат, дата факта, комментарий ==")
    c, j = exe.rpc("directive.update", {"id": tid4, "result": "Черновой отчёт готов", "comment": "Ожидаем данные",
                                        "progress": 35})
    row4 = reg_row(exe, tid4)
    check("исполнитель обновляет результат и комментарий", c == 200 and row4["result"] == "Черновой отчёт готов", j.get("error"))
    check("процент выполнения обновлён", row4["progress"] == 35)
    check("комментарий в реестре", row4["comment"] == "Ожидаем данные")
    c, j = exe.rpc("directive.update", {"id": tid4, "progress": 140})
    check("процент вне диапазона отклоняется", c == 400, c)
    c, j = exe.rpc("directive.update", {"id": tid4, "factDate": D(3)})
    check("дата факта в будущем отклоняется", c == 400, c)
    c, j = exe.rpc("directive.update", {"id": tid4, "factDate": D(-1)})
    check("дата фактического исполнения сохраняется", c == 200 and reg_row(exe, tid4)["factDate"] == D(-1))
    c, j = exe.rpc("directive.update", {"id": tid4, "coassignees": ["u6", "u7"]})
    check("соисполнители изменяются", reg_row(exe, tid4)["coassignees"] == ["u6", "u7"])
    c, j = exe.rpc("directive.update", {"id": tid4, "coassignees": ["u9"]})
    check("несуществующий соисполнитель отклоняется", c == 400, c)
    c, j = obs.rpc("directive.update", {"id": tid4, "comment": "наблюдатель"})
    check("наблюдатель не может менять поручение (403)", c == 403, c)
    plain = next(t for t in adm.state["tasks"] if t["type"] == "task")
    c, j = adm.rpc("directive.update", {"id": plain["id"], "comment": "не поручение"})
    check("обычная задача не является поручением", c == 400, c)

    print("== Подтверждающие документы (M09) ==")
    doc = adm.state["docs"][0]
    c, j = exe.rpc("directive.attach", {"id": tid4, "docId": doc["id"]})
    row4 = reg_row(exe, tid4)
    check("документ приложен к поручению", c == 200 and len(row4["docs"]) == 1, j.get("error"))
    check("ссылка хранит код, наименование и версию",
          row4["docs"][0]["code"] == doc["code"] and row4["docs"][0]["v"] >= 1, row4["docs"])
    c, j = exe.rpc("directive.attach", {"id": tid4, "docId": doc["id"]})
    check("повторное приложение отклоняется", c == 400, c)
    c, j = exe.rpc("directive.attach", {"id": tid4, "docId": "d_none"})
    check("несуществующий документ отклоняется", c == 404, c)
    c, j = obs.rpc("directive.attach", {"id": tid4, "docId": doc["id"]})
    check("наблюдатель не может прикладывать документы (403)", c == 403, c)
    c, j = exe.rpc("directive.detach", {"id": tid4, "docId": doc["id"]})
    check("документ откреплён", c == 200 and len(reg_row(exe, tid4)["docs"]) == 0)
    check("документ остался в M09", any(x["id"] == doc["id"] for x in adm.state["docs"]))

    print("== Фильтры реестра ==")
    all_rows = adm.res("directive.list", {})
    check("в реестре все поручения совещаний", all_rows["total"] >= 6, all_rows["total"])
    check("фильтр по совещанию", all(r["meetingId"] == mid for r in adm.res("directive.list", {"meetingId": mid})["rows"]))
    check("фильтр по исполнителю учитывает соисполнителей",
          all(r["assignee"] == "u6" or "u6" in r["coassignees"] for r in adm.res("directive.list", {"assignee": "u6"})["rows"]))
    check("фильтр по статусу", all(r["status"] == "Выполнено" for r in adm.res("directive.list", {"status": "Выполнено"})["rows"]))
    check("фильтр просроченных", all(r["overdueDays"] > 0 for r in adm.res("directive.list", {"overdue": True})["rows"]))
    check("фильтр по периоду срока",
          all(D(10) <= r["due"] <= D(25) for r in adm.res("directive.list", {"from": D(10), "to": D(25)})["rows"]))
    check("поиск по формулировке", adm.res("directive.list", {"q": "сводку"})["total"] >= 1)
    c, j = obs.rpc("directive.list")
    check("наблюдатель видит реестр (право просмотра)", c == 200, c)

    print("== Аудит и резервная копия ==")
    adm.rpc("notif.list")
    evs = [e for e in adm.state["events"] if isinstance(e.get("details"), dict)]
    for name in ("MEETING_DIRECTIVE_UPDATED", "MEETING_DIRECTIVE_EXTENDED", "MEETING_DIRECTIVE_ACCEPTED",
                 "MEETING_DIRECTIVE_REWORK", "MEETING_DIRECTIVE_DOC_ATTACHED"):
        check("событие аудита " + name, any(e["details"].get("event") == name for e in evs))
    c, backup = adm.req("GET", "/api/backup", raw=True)
    backup = json.loads(backup)
    check("копия хранит приёмку и историю продления",
          any((r.get("directive") or {}).get("acceptance") for r in backup["tables"]["tasks"]) and
          any((r.get("directive") or {}).get("dueHistory") for r in backup["tables"]["tasks"]))
    c, j = adm.req("POST", "/api/restore", backup)
    check("копия восстанавливается", c == 200 and j.get("ok"), (c, j.get("error")))
    check("после восстановления приёмка сохранена", (reg_row(adm, tid).get("acceptance") or {}).get("verdict") == "Принято")

    print("== Сброс демо-данных ==")
    c, j = adm.rpc("admin.reset")
    check("сброс выполнен", c == 200 and j.get("ok"), j.get("error"))
    check("испытательное совещание удалено",
          not any(m["title"] == "Испытания реестра поручений" for m in adm.state["meetings"]))

    print("\nИТОГ: %d ok, %d FAIL" % (ok, fail))
    sys.exit(1 if fail else 0)

if __name__ == "__main__":
    main()
