#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
СУП-ИП. Испытания поручений из решений совещаний (ТЗ M16 §2.10, техпроект §4.1).
Запуск при работающем сервере:  python3 tests/directive_test.py --base http://127.0.0.1:8000
Проверяется: создание задачи M02 со всеми полями §2.10 (наименование, описание, проект,
постановщик, исполнитель, соисполнители, дата постановки, срок, приоритет, критерии завершения,
источник, ссылки на совещание/протокол/пункт решения), связь meeting_tasks, несколько задач
из одного решения, запрет повторного создания без подтверждения, двусторонние переходы,
уведомления исполнителю и соисполнителям, права, аудит, миграция схемы и резервная копия.
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

def _st(x):
    if isinstance(x, Client):
        x.rpc("notif.list")
        return x.state or {}
    return x or {}

def meet(st, mid):
    return next((m for m in _st(st)["meetings"] if m["id"] == mid), {})

def task(st, tid):
    return next((t for t in _st(st)["tasks"] if t["id"] == tid), {})

def links(st, mid):
    return [r for r in _st(st).get("meetingTasks", []) if r["meeting_id"] == mid]

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
    check("состояние содержит связи решений с поручениями", "meetingTasks" in adm.state)
    check("демо-набор: 2 связи решение↔задача", len(adm.state["meetingTasks"]) == 2, len(adm.state["meetingTasks"]))
    demo = next((t for t in adm.state["tasks"] if t["code"] == "З-027"), {})
    dr0 = demo.get("directive") or {}
    check("демо-поручение хранит источник", "СОВ-2026-041" in dr0.get("source", ""), dr0.get("source"))
    check("демо-поручение хранит критерии и соисполнителей", bool(dr0.get("criteria")) and dr0.get("coassignees") == ["u3"])
    check("демо-поручение ссылается на утверждённый протокол", (dr0.get("protocolDoc") or {}).get("docCode", "").startswith("ДОК-"))

    exe = Client(); exe.login("novikov")     # u5 — исполнитель
    co = Client(); co.login("kozlova")       # u6 — соисполнитель
    obs = Client(); obs.login("zaytseva")    # u8 — наблюдатель
    pm = Client(); pm.login("volkova")       # u3 — руководитель проектов

    print("== Совещание, повестка и решение ==")
    c, j = adm.rpc("meeting.create", {"title": "Испытания поручений", "type": "Оперативное", "date": D(0),
                                      "time": "10:00", "chair": "u2", "secretary": "u3", "attendees": ["u5", "u6"]})
    m = j.get("result") or {}; mid = m.get("id")
    check("совещание создано", c == 200 and mid, j.get("error"))
    adm.rpc("meeting.agenda.add", {"id": mid, "title": "Ход испытаний стенда", "reporter": "u5"})
    adm.rpc("meeting.agenda.add", {"id": mid, "title": "Отчётность по этапу", "reporter": "u3"})
    c, j = adm.rpc("meeting.decision.add", {"id": mid, "text": "Подготовить отчёт по испытаниям", "resp": "u5",
                                            "due": D(5), "agendaN": 2})
    d = (j.get("result") or {}).get("decisions", [{}])[0]
    did = d.get("id")
    check("решение зафиксировано с пунктом повестки", c == 200 and d.get("agendaN") == 2, j.get("error"))
    check("решение ещё не имеет поручений", d.get("taskIds") == [] and d.get("taskId") is None)
    c, j = adm.rpc("meeting.decision.add", {"id": mid, "text": "Решение с неверным пунктом", "resp": "u5",
                                            "due": D(5), "agendaN": 9})
    check("несуществующий пункт повестки отклоняется", c == 400, c)

    print("== Создание поручения со всеми полями §2.10 ==")
    params = {"id": mid, "did": did, "title": "Отчёт по испытаниям стенда АКИ-12",
              "desc": "Подготовить и согласовать отчёт по результатам испытаний.",
              "projectId": "p1", "assignee": "u5", "coassignees": ["u6", "u7"], "due": D(7),
              "priority": "Критический", "criteria": "Отчёт подписан испытательной группой и передан РП",
              "source": "Решение №1 совещания (испытания)"}
    c, j = adm.rpc("meeting.decision.toTask", params)
    check("поручение создано", c == 200 and j.get("ok"), j.get("error"))
    d = meet(adm, mid)["decisions"][0]
    tid = (d.get("taskIds") or [None])[0]
    t = task(adm, tid)
    dr = t.get("directive") or {}
    check("поручение — задача единой системы M02", t.get("type") == "directive" and t.get("code", "").startswith("З-"), t.get("code"))
    check("наименование перенесено", t.get("title") == params["title"])
    check("описание перенесено", t.get("desc") == params["desc"])
    check("проект указан явно", t.get("projectId") == "p1")
    check("постановщик зафиксирован", t.get("createdBy") == "u1" and dr.get("postedBy") == "u1")
    check("исполнитель назначен", t.get("assignee") == "u5")
    check("соисполнители сохранены", dr.get("coassignees") == ["u6", "u7"], dr.get("coassignees"))
    check("дата постановки зафиксирована", dr.get("postedDate") == D(0) and bool(dr.get("postedAt")))
    check("срок исполнения перенесён", t.get("end") == D(7))
    check("приоритет перенесён", t.get("priority") == "Критический")
    check("критерии завершения сохранены", dr.get("criteria") == params["criteria"])
    check("источник поручения сохранён в карточке задачи", dr.get("source") == params["source"])
    check("ссылка на совещание", dr.get("meetingId") == mid and t.get("meetingId") == mid)
    check("ссылка на пункт решения", dr.get("decisionId") == did and dr.get("decisionN") == 1)
    check("ссылка на пункт повестки", dr.get("agendaN") == 2)
    check("ссылка на протокол присутствует (не утверждён)", "protocolV" in dr and dr.get("protocolDoc") is None)
    check("начальный статус поручения", t.get("status") == "Готова к выполнению")
    check("связь meeting_tasks создана", len(links(adm, mid)) == 1 and links(adm, mid)[0]["task_id"] == tid)
    check("связь хранит решение и автора", links(adm, mid)[0]["decision_id"] == did and links(adm, mid)[0]["by_user"] == "u1")

    print("== Уведомления исполнителю и соисполнителям ==")
    check("исполнитель уведомлён о поручении", len(notifs(exe, "directive.assigned", tid)) == 1)
    check("соисполнитель уведомлён", len(notifs(co, "directive.assigned", tid)) == 1)
    n = notifs(co, "directive.assigned", tid)
    check("уведомление соисполнителю помечено как соисполнение", n and "Соисполнитель" in n[0]["title"], n)

    print("== Несколько задач из одного решения и защита от повторов ==")
    c, j = adm.rpc("meeting.decision.toTask", {"id": mid, "did": did})
    err = j.get("error") or {}
    check("повторное создание без подтверждения отклоняется", c == 409 and err.get("code") == "NEED_CONFIRM", (c, err))
    check("в отказе указано существующее поручение", t["code"] in err.get("message", ""), err.get("message"))
    c, j = adm.rpc("meeting.decision.toTask", {"id": mid, "did": did, "confirm": True,
                                               "assignee": "u6", "due": D(9), "title": "Сводка по замечаниям испытаний"})
    check("с явным подтверждением создаётся второе поручение", c == 200 and j.get("ok"), j.get("error"))
    d = meet(adm, mid)["decisions"][0]
    check("одно решение порождает несколько задач", len(d["taskIds"]) == 2, d["taskIds"])
    tid2 = d["taskIds"][1]
    check("совместимость: taskId указывает первое поручение", d["taskId"] == tid)
    check("связей meeting_tasks стало две", len(links(adm, mid)) == 2)
    check("второе поручение имеет собственного исполнителя", task(adm, tid2).get("assignee") == "u6")

    print("== Правка решения не создаёт поручение повторно ==")
    c, j = adm.rpc("meeting.decision.update", {"id": mid, "did": did, "text": "Подготовить и согласовать отчёт по испытаниям",
                                               "due": D(6), "agendaN": 1})
    d = meet(adm, mid)["decisions"][0]
    check("решение изменено", c == 200 and d["text"].startswith("Подготовить и согласовать"), j.get("error"))
    check("пункт повестки изменён", d["agendaN"] == 1)
    check("поручения сохранены без повторного создания", len(d["taskIds"]) == 2 and len(links(adm, mid)) == 2)
    c, j = adm.rpc("meeting.decision.update", {"id": mid, "did": did, "agendaN": 42})
    check("правка с неверным пунктом повестки отклоняется", c == 400, c)

    print("== Валидация полей поручения ==")
    c, j = adm.rpc("meeting.decision.toTask", {"id": mid, "did": did, "confirm": True, "assignee": "u9"})
    check("несуществующий исполнитель отклоняется", c == 400, c)
    c, j = adm.rpc("meeting.decision.toTask", {"id": mid, "did": did, "confirm": True, "coassignees": ["u9"]})
    check("несуществующий соисполнитель отклоняется", c == 400, c)
    c, j = adm.rpc("meeting.decision.toTask", {"id": mid, "did": did, "confirm": True, "priority": "Срочно"})
    check("недопустимый приоритет отклоняется", c == 400, c)
    c, j = adm.rpc("meeting.decision.toTask", {"id": mid, "did": did, "confirm": True, "due": "2026-13-45"})
    check("некорректная дата срока отклоняется", c == 400, c)
    c, j = adm.rpc("meeting.decision.toTask", {"id": mid, "did": did, "confirm": True, "projectId": "p6"})
    check("закрытый проект для поручения отклоняется", c == 400, c)
    c, j = adm.rpc("meeting.decision.toTask", {"id": mid, "did": did, "confirm": True, "title": "   "})
    check("пустое наименование отклоняется", c == 400, c)
    c, j = adm.rpc("meeting.decision.toTask", {"id": mid, "did": did, "confirm": True, "assignee": "u5",
                                               "coassignees": ["u5", "u6", "u6"]})
    dr = task(adm, meet(adm, mid)["decisions"][0]["taskIds"][-1]).get("directive") or {}
    check("исполнитель и повторы исключаются из соисполнителей", dr.get("coassignees") == ["u6"], dr.get("coassignees"))

    print("== Автовыбор проекта и значения по умолчанию ==")
    c, j = adm.rpc("meeting.decision.add", {"id": mid, "text": "Провести повторные замеры", "resp": "u6", "due": D(4),
                                            "createTask": True})
    d2 = meet(adm, mid)["decisions"][-1]
    t3 = task(adm, d2["taskIds"][0])
    check("поручение создаётся сразу при регистрации решения", c == 200 and bool(d2["taskIds"]), j.get("error"))
    check("проект подобран автоматически", bool(t3.get("projectId")), t3.get("projectId"))
    check("источник сформирован автоматически", "совещания" in (t3.get("directive") or {}).get("source", ""),
          (t3.get("directive") or {}).get("source"))
    check("описание содержит формулировку решения", "Провести повторные замеры" in t3.get("desc", ""))
    check("срок взят из решения", t3.get("end") == D(4))

    print("== Двусторонняя связь и протокол ==")
    adm.rpc("meeting.conduct", {"id": mid})
    adm.rpc("protocol.send", {"id": mid})
    pr = meet(adm, mid)["protocol"]
    chair = Client(); chair.login("morozov")
    for step in pr["route"]:
        cl = {"u3": pm, "u2": chair}.get(step["user"], adm)
        cl.rpc("protocol.review", {"id": mid, "decision": "Согласовано"})
    c, j = adm.rpc("protocol.approve", {"id": mid})
    check("протокол утверждён", c == 200, j.get("error"))
    c, j = adm.rpc("meeting.decision.add", {"id": mid, "text": "Выпустить дополнение к отчёту", "resp": "u5",
                                            "due": D(10), "createTask": True, "agendaN": 1})
    d3 = meet(adm, mid)["decisions"][-1]
    dr3 = task(adm, d3["taskIds"][0]).get("directive") or {}
    check("поручение после утверждения ссылается на редакцию протокола", dr3.get("protocolV") == 1, dr3.get("protocolV"))
    check("ссылка на документ протокола заполнена", (dr3.get("protocolDoc") or {}).get("docCode", "").startswith("ДОК-"), dr3)
    c, blob = adm.req("GET", "/api/files/%s/%d" % (dr3["protocolDoc"]["docId"], dr3["protocolDoc"]["docV"]), raw=True)
    check("протокол из ссылки поручения выгружается", c == 200 and blob[:2] == b"PK", c)

    print("== Права ==")
    c, j = obs.rpc("meeting.decision.toTask", {"id": mid, "did": did, "confirm": True})
    check("наблюдатель не может создавать поручения (403)", c == 403, c)
    c, j = exe.rpc("meeting.decision.toTask", {"id": mid, "did": did, "confirm": True})
    check("исполнитель не может править совещание (403)", c == 403, c)
    c, j = pm.rpc("meeting.decision.update", {"id": mid, "did": did, "text": "Уточнённая формулировка РП"})
    check("руководитель проектов правит решение", c == 200, j.get("error"))

    print("== Удаление задачи снимает связь ==")
    before = len(links(adm, mid))
    c, j = adm.rpc("task.delete", {"id": tid2})
    check("задача удалена", c == 200, j.get("error"))
    check("связь meeting_tasks удалена", len(links(adm, mid)) == before - 1)
    ids = meet(adm, mid)["decisions"][0]["taskIds"]
    check("список поручений решения обновлён", tid2 not in ids and tid in ids, ids)

    print("== Аудит, схема и резервная копия ==")
    adm.rpc("notif.list")
    evs = [e for e in adm.state["events"] if isinstance(e.get("details"), dict)]
    check("событие MEETING_DIRECTIVE_CREATED", any(e["details"].get("event") == "MEETING_DIRECTIVE_CREATED" for e in evs))
    check("событие MEETING_DECISION_UPDATED", any(e["details"].get("event") == "MEETING_DECISION_UPDATED" for e in evs))
    created = [e for e in evs if e["details"].get("event") == "MEETING_DIRECTIVE_CREATED"]
    check("аудит содержит источник и соисполнителей", created and "source" in created[0]["details"] and "coassignees" in created[0]["details"])
    c, j = adm.req("GET", "/api/health")
    check("сервер работает на PostgreSQL", j.get("db") == "PostgreSQL", j)
    c, backup = adm.req("GET", "/api/backup", raw=True)
    backup = json.loads(backup)
    check("копия содержит 16 таблиц", len(backup["tables"]) == 16, len(backup["tables"]))
    check("копия содержит связи решений с поручениями", "meeting_tasks" in backup["tables"] and len(backup["tables"]["meeting_tasks"]) >= 3)
    check("копия хранит блок поручения в задачах", any("directive" in r for r in backup["tables"]["tasks"]))
    c, j = adm.req("POST", "/api/restore", backup)
    check("копия восстанавливается", c == 200 and j.get("ok"), (c, j.get("error")))
    check("после восстановления связи сохранены", len(links(adm, mid)) >= 3, len(links(adm, mid)))
    old = dict(backup); old["tables"] = {k: v for k, v in backup["tables"].items() if k != "meeting_tasks"}
    c, j = adm.req("POST", "/api/restore", old)
    check("копия прежней версии (15 таблиц) восстанавливается", c == 200 and j.get("ok"), (c, j.get("error")))

    print("== Сброс демо-данных ==")
    c, j = adm.rpc("admin.reset")
    check("сброс выполнен", c == 200 and j.get("ok"), j.get("error"))
    check("испытательные совещания удалены", not any(m["title"] == "Испытания поручений" for m in adm.state["meetings"]))
    check("демо-связи восстановлены", len(adm.state["meetingTasks"]) == 2, len(adm.state["meetingTasks"]))

    print("\nИТОГ: %d ok, %d FAIL" % (ok, fail))
    sys.exit(1 if fail else 0)

if __name__ == "__main__":
    main()
