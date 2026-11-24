#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
СУП-ИП. Испытания центра уведомлений и напоминаний (ТЗ M12 §8, M16 §2.11.1).
Запуск при работающем сервере:  python3 tests/notif_test.py --base http://127.0.0.1:8000
Проверяется: доставка приглашений и напоминаний, настраиваемые интервалы, контроль сроков
задач и поручений, очереди утверждения, защита от повторной доставки, персональные
настройки типов, прочтение, изоляция пользователей, права и аудит, резервная копия.
В начале и в конце испытаний восстанавливается эталонный набор (admin.reset).
Только стандартная библиотека Python.
"""
import argparse, datetime, json, sys, time, urllib.request, urllib.error, http.cookiejar

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
        self.cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))
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
        c, j = self.rpc(action, payload)
        return j.get("result")

def main():
    global BASE
    ap = argparse.ArgumentParser(); ap.add_argument("--base", default=BASE)
    BASE = ap.parse_args().base.rstrip("/")

    adm = Client(); c, j = adm.login("admin")
    check("вход администратора", c == 200 and j.get("ok"), c)
    # эталонный набор в начале и в конце: испытания не зависят от прежнего состояния БД
    c, j = adm.rpc("admin.reset")
    check("подготовка эталонного набора", c == 200 and j.get("ok"), j.get("error"))
    st = adm.state
    check("состояние содержит центр уведомлений", "notifications" in st and "notifUnread" in st and "reminders" in st)

    print("== Совещание → приглашения и типовые напоминания ==")
    c, j = adm.rpc("meeting.create", {"title": "Испытания уведомлений", "type": "Оперативное", "date": D(1),
                                      "time": "10:00", "chair": "u2", "secretary": "u3", "attendees": ["u4", "u5"]})
    m = j.get("result") or {}
    check("совещание создано", c == 200 and m.get("id"), j.get("error"))
    rem = [r for r in adm.state["reminders"] if r["owner_id"] == m["id"]]
    check("напоминания созданы для 4 участников × 2 интервала", len(rem) == 8, len(rem))
    check("типовые интервалы: за 1 день и за 1 час", sorted({r["offset_min"] for r in rem}) == [60, 1440])
    check("напоминания ещё не доставлены", all(r["sent_at"] is None for r in rem))

    u5 = Client(); u5.login("novikov")
    inv = [n for n in u5.state["notifications"] if n["type"] == "meeting.invite" and n["obj_id"] == m["id"]]
    check("участник получил приглашение", len(inv) == 1, u5.state["notifications"][:2])
    check("уведомление содержит канал и статус доставки", inv and inv[0]["channel"] == "web" and inv[0]["status"] == "sent")
    check("дата прочтения пуста", inv and inv[0]["read_at"] is None)
    check("счётчик непрочитанных > 0", u5.state["notifUnread"] >= 1)
    chair = Client(); chair.login("morozov")
    check("председатель получил приглашение", any(n["type"] == "meeting.invite" for n in chair.state["notifications"]))
    check("создатель не получает приглашение самому себе",
          not any(n["type"] == "meeting.invite" and n["obj_id"] == m["id"] for n in adm.state["notifications"]))

    print("== Настройка интервалов напоминаний ==")
    r = adm.res("reminder.set", {"ownerType": "meeting", "ownerId": m["id"], "offsets": [60, 4320, 10080, 20160]})
    check("интервалы перенастроены", r and r["count"] == 16, r)
    check("набор интервалов соответствует запросу",
          sorted({x["offset_min"] for x in r["reminders"]}) == [60, 4320, 10080, 20160])
    lst = adm.res("reminder.list", {"ownerType": "meeting", "ownerId": m["id"]})
    check("reminder.list возвращает набор", len(lst) == 16)
    c, j = u5.rpc("reminder.set", {"ownerType": "meeting", "ownerId": m["id"], "offsets": [60]})
    check("исполнитель без права правки совещания → 403", c == 403, c)
    rid = lst[0]["id"]
    c, j = adm.rpc("reminder.remove", {"id": rid})
    check("напоминание удалено", c == 200 and len(adm.res("reminder.list", {"ownerType": "meeting", "ownerId": m["id"]})) == 15)

    print("== Доставка по расписанию и защита от повторов (ТЗ §8.4) ==")
    # напоминание «за 14 дней» для завтрашнего совещания уже наступило → должно быть доставлено
    c, j = adm.rpc("notif.tick")
    check("цикл планировщика доступен администратору", c == 200 and j.get("ok"), j.get("error"))
    delivered = (j.get("result") or {}).get("delivered", 0)
    check("уведомления доставлены", delivered > 0, delivered)
    u5.rpc("notif.list")
    before = len(u5.state["notifications"])
    got = [n for n in u5.state["notifications"] if n["type"] == "meeting.reminder" and n["obj_id"] == m["id"]]
    check("напоминание о совещании доставлено", len(got) >= 1, [n["type"] for n in u5.state["notifications"]][:6])
    adm.rpc("notif.tick"); adm.rpc("notif.tick")
    u5.rpc("notif.list")
    after = len([n for n in u5.state["notifications"] if n["type"] == "meeting.reminder" and n["obj_id"] == m["id"]])
    check("повторные циклы не дублируют уведомление", after == len(got), (len(got), after))
    sent = [r for r in adm.state["reminders"] if r["owner_id"] == m["id"] and r["sent_at"]]
    check("отметка о доставке напоминания сохранена", len(sent) >= 1)
    c, j = u5.rpc("notif.tick")
    check("планировщик недоступен не-администратору (403)", c == 403, c)

    print("== Контроль сроков задач и поручений (M16 §2.11.1) ==")
    proj = adm.state["projects"][0]["id"]
    c, j = adm.rpc("task.create", {"projectId": proj, "title": "Просроченная работа (испытание)", "assignee": "u5",
                                   "start": D(-10), "end": D(-2), "planHours": 8, "priority": "Высокий",
                                   "initialStatus": "Готова к выполнению"})
    t_over = j.get("result") or {}
    check("задача с истёкшим сроком создана", c == 200 and t_over.get("id"), j.get("error"))
    check("исполнитель уведомлён о назначении", any(n["type"] == "task.assigned" and n["obj_id"] == t_over["id"]
                                                for n in u5.rpc("notif.list")[1]["state"]["notifications"]))
    c, j = adm.rpc("task.create", {"projectId": proj, "title": "Срок завтра (испытание)", "assignee": "u5",
                                   "start": D(-1), "end": D(1), "planHours": 4, "priority": "Средний",
                                   "initialStatus": "Готова к выполнению"})
    t_due = j.get("result") or {}
    adm.rpc("notif.tick")
    u5.rpc("notif.list")
    types = {n["type"] for n in u5.state["notifications"]}
    check("уведомление о просрочке задачи", "task.overdue" in types, types)
    check("уведомление о приближающемся сроке", "task.due" in types, types)

    c, j = adm.rpc("meeting.decision.add", {"id": m["id"], "text": "Подготовить протокол испытаний", "resp": "u5",
                                            "due": D(3), "createTask": True})
    dec = (j.get("result") or {}).get("decisions", [])
    dir_task = dec[-1].get("taskId") if dec else None
    check("поручение создано из решения", bool(dir_task))
    u5.rpc("notif.list")
    check("исполнитель уведомлён о поручении", any(n["type"] == "directive.assigned" and n["obj_id"] == dir_task
                                              for n in u5.state["notifications"]))

    print("== Перенос совещания и новые участники (ТЗ §2.5, M12 §8.2) ==")
    fire_was = max(r["fire_at"] for r in adm.state["reminders"] if r["owner_id"] == m["id"])
    c, j = adm.rpc("meeting.update", {"id": m["id"], "date": D(2), "time": "12:30", "attendees": ["u4", "u5", "u6"]})
    check("совещание перенесено", c == 200 and j.get("ok"), j.get("error"))
    u5.rpc("notif.list")
    check("участники оповещены о переносе", any(n["type"] == "meeting.reminder" and "перенесено" in n["title"]
                                            and n["obj_id"] == m["id"] for n in u5.state["notifications"]))
    u6 = Client(); u6.login("kozlova")
    check("новый участник получил приглашение", any(n["type"] == "meeting.invite" and n["obj_id"] == m["id"]
                                                 for n in u6.state["notifications"]))
    rem2 = [r for r in adm.state["reminders"] if r["owner_id"] == m["id"]]
    check("напоминания пересобраны для нового состава (5 × 4)", len(rem2) == 20, len(rem2))
    base = int(time.mktime(datetime.datetime.strptime(D(2) + " 12:30", "%Y-%m-%d %H:%M").timetuple())) * 1000
    check("время срабатывания пересчитано от новой даты",
          all(r["fire_at"] == base - r["offset_min"] * 60000 for r in rem2) and max(r["fire_at"] for r in rem2) != fire_was)

    print("== Утверждение протокола → оповещение участников ==")
    c, j = adm.rpc("meeting.create", {"title": "Утверждение протокола (испытание)", "type": "Техническое", "date": D(0),
                                      "time": "09:00", "chair": "u2", "secretary": "u3", "attendees": ["u5"]})
    m2 = j.get("result") or {}
    c, j = adm.rpc("meeting.conduct", {"id": m2["id"]})
    check("совещание проведено", c == 200 and j.get("ok"), j.get("error"))
    c, j = adm.rpc("meeting.approve", {"id": m2["id"]})
    check("протокол утверждён", c == 200 and j.get("ok"), j.get("error"))
    u5.rpc("notif.list")
    check("участник оповещён об утверждении протокола",
          any(n["type"] == "protocol.approved" and n["obj_id"] == m2["id"] for n in u5.state["notifications"]))

    print("== Событие календаря → напоминания участникам ==")
    c, j = adm.rpc("calendar.create", {"title": "Испытание напоминаний календаря", "date": D(0), "time": "09:00",
                                       "participants": ["u5", "u6"], "note": "Переговорная №2"})
    ev = j.get("result") or {}
    check("событие создано", c == 200 and ev.get("id"), j.get("error"))
    evr = [r for r in adm.state["reminders"] if r["owner_id"] == ev["id"]]
    check("напоминания события: 2 участника × 2 интервала", len(evr) == 4, len(evr))
    adm.rpc("notif.tick"); u5.rpc("notif.list")
    check("напоминание о событии доставлено", any(n["type"] == "event.reminder" and n["obj_id"] == ev["id"]
                                              for n in u5.state["notifications"]))

    print("== Очереди утверждения и результат рассмотрения ==")
    head = Client(); head.login("morozov")
    adm.rpc("notif.tick"); head.rpc("notif.list")
    htypes = {n["type"] for n in head.state["notifications"]}
    check("руководитель уведомлён об очереди трудозатрат", "time.approval" in htypes, htypes)
    check("руководитель уведомлён о проверке расчётов", "calc.verify" in htypes, htypes)
    # отправка недели исполнителем → уведомление утверждающему; утверждение → уведомление автору
    task_u5 = next(t["id"] for t in u5.state["tasks"] if t["assignee"] == "u5" and t["status"] != "Завершена")
    c, j = u5.rpc("time.create", {"date": D(0), "hours": 3, "taskId": task_u5, "act": "Совещания"})
    check("черновик трудозатрат создан", c == 200 and j.get("ok"), j.get("error"))
    mon = (datetime.date.today() - datetime.timedelta(days=datetime.date.today().weekday())).isoformat()
    c, j = u5.rpc("time.sendWeek", {"mon": mon})
    check("неделя отправлена на утверждение", c == 200 and j.get("ok"), j.get("error"))
    head.rpc("notif.list")
    check("утверждающий уведомлён об отправке недели",
          any(n["type"] == "time.approval" and "неделю" in n["body"] for n in head.state["notifications"]),
          [n["body"] for n in head.state["notifications"] if n["type"] == "time.approval"])
    sent_e = next(e for e in head.state["time"] if e["userId"] == "u5" and e["status"] == "Отправлена")
    c, j = head.rpc("time.status", {"id": sent_e["id"], "to": "Утверждена"})
    check("запись утверждена", c == 200 and j.get("ok"), j.get("error"))
    u5.rpc("notif.list")
    check("автор уведомлён о результате рассмотрения",
          any(n["type"] == "time.result" and n["obj_id"] == sent_e["id"] for n in u5.state["notifications"]))

    print("== Согласование документа → утверждающие и автор ==")
    c, j = u5.rpc("doc.create", {"title": "Документ испытаний уведомлений", "type": "Отчёт", "projectId": proj,
                                 "fileName": "ispitanie.txt", "fileSize": 128})
    doc = j.get("result") or {}
    check("документ создан исполнителем", c == 200 and doc.get("id"), j.get("error"))
    c, j = adm.rpc("doc.status", {"id": doc["id"], "to": "На согласовании"})
    check("документ направлен на согласование", c == 200 and j.get("ok"), j.get("error"))
    head.rpc("notif.list")
    check("утверждающий уведомлён о согласовании",
          any(n["type"] == "doc.approval" and n["obj_id"] == doc["id"] for n in head.state["notifications"]))
    c, j = head.rpc("doc.status", {"id": doc["id"], "to": "Утверждён"})
    check("документ утверждён", c == 200 and j.get("ok"), j.get("error"))
    u5.rpc("notif.list")
    check("автор уведомлён об утверждении",
          any(n["type"] == "doc.approved" and n["obj_id"] == doc["id"] for n in u5.state["notifications"]))

    print("== Персональные настройки типов уведомлений ==")
    prefs = u5.res("notif.prefs", {"prefs": {"task.overdue": False}})
    check("настройка сохранена", prefs.get("task.overdue") is False, prefs)
    n_before = len([n for n in u5.state["notifications"] if n["type"] == "task.overdue"])
    c, j = adm.rpc("task.create", {"projectId": proj, "title": "Ещё одна просрочка (испытание)", "assignee": "u5",
                                   "start": D(-9), "end": D(-3), "planHours": 2, "priority": "Низкий",
                                   "initialStatus": "Готова к выполнению"})
    adm.rpc("notif.tick"); u5.rpc("notif.list")
    n_after = len([n for n in u5.state["notifications"] if n["type"] == "task.overdue"])
    check("отключённый тип не доставляется", n_after == n_before, (n_before, n_after))
    u5.res("notif.prefs", {"prefs": {"task.overdue": True}})

    print("== Прочтение и изоляция пользователей ==")
    unread_before = u5.state["notifUnread"]
    r = u5.res("notif.read", {"all": True})
    check("прочитаны все", r and r["unread"] == 0, r)
    check("счётчик непрочитанных обнулён", u5.state["notifUnread"] == 0)
    check("дата прочтения проставлена", all(n["read_at"] for n in u5.state["notifications"]))
    obs = Client(); obs.login("zaytseva")
    check("чужие уведомления не видны", all(n["id"] not in {x["id"] for x in u5.state["notifications"]}
                                        for n in obs.state["notifications"]))
    c, j = obs.rpc("notif.read", {"ids": [u5.state["notifications"][0]["id"]]})
    check("нельзя отметить чужое уведомление", (j.get("result") or {}).get("read", 0) == 0, j.get("result"))

    print("== Аудит и резервная копия ==")
    evs = adm.rpc("notif.list")[1]["state"]["events"]
    check("доставка напоминания зафиксирована (calendar.reminder.sent)",
          any(e["details"].get("event") == "calendar.reminder.sent" for e in evs if isinstance(e.get("details"), dict)))
    check("настройка напоминаний зафиксирована (calendar.reminder.created)",
          any(e["details"].get("event") == "calendar.reminder.created" for e in evs if isinstance(e.get("details"), dict)))
    c, backup = adm.req("GET", "/api/backup", raw=True)
    backup = json.loads(backup)
    check("копия содержит 15 таблиц", len(backup["tables"]) == 15, len(backup["tables"]))
    check("уведомления и напоминания в копии", "notifications" in backup["tables"] and "reminders" in backup["tables"])
    old = dict(backup); old["tables"] = {k: v for k, v in backup["tables"].items() if k not in ("notifications", "reminders")}
    c, j = adm.req("POST", "/api/restore", old)
    check("копия прежней версии (13 таблиц) восстанавливается", c == 200 and j.get("ok"), (c, j.get("error")))

    print("== Сброс демо-данных ==")
    c, j = adm.rpc("admin.reset")
    check("сброс выполнен", c == 200 and j.get("ok"), j.get("error"))
    check("центр уведомлений очищен при сбросе", len(adm.state["notifications"]) <= 1 and len(adm.state["reminders"]) == 0,
          (len(adm.state["notifications"]), len(adm.state["reminders"])))

    print("\nИТОГ: %d ok, %d FAIL" % (ok, fail))
    sys.exit(1 if fail else 0)

if __name__ == "__main__":
    main()
