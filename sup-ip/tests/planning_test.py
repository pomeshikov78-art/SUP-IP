#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
СУП-ИП. Испытания планирования совещаний (ТТ-2, M16 §2.5).
Запуск при работающем сервере:  python3 tests/planning_test.py --base http://127.0.0.1:8000

Проверяется полный состав §2.5: создание совещания, дата и время, повестка, участники,
председатель и секретарь, предварительные материалы, регистрация связанных проектов,
изменение параметров, перенос с основанием, отмена и возобновление, отображение в общем
календаре, регулярные совещания с повторением по расписанию (каждое проведённое мероприятие —
самостоятельная карточка и отдельный протокол), права ролей, уведомления, напоминания,
протокол DOCX, резервная копия и миграция схемы 4→5.
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


def wd(s):
    return datetime.date.fromisoformat(s).isoweekday()


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
    p = {"title": "Планирование §2.5", "type": "Оперативное", "date": D(1), "time": "10:00",
         "chair": "u2", "secretary": "u3", "attendees": ["u2", "u3", "u5"]}
    p.update(kw)
    return cli.res("meeting.create", p)


def main():
    global BASE
    ap = argparse.ArgumentParser(); ap.add_argument("--base", default=BASE)
    BASE = ap.parse_args().base.rstrip("/")

    srv = importlib.util.module_from_spec(importlib.util.spec_from_file_location("supip_plan_test", SERVER))
    importlib.util.spec_from_file_location("supip_plan_test", SERVER).loader.exec_module(srv)

    adm = Client()
    c, j = adm.login("admin")
    check("вход администратора", c == 200 and j.get("ok"), c)
    adm.rpc("admin.reset")

    print("== Расписание повторений: расчёт дат (модуль) ==")
    r = srv.rec_validate({"freq": "weekly", "interval": 1, "weekdays": [1, 3]}, "2026-11-25")
    check("еженедельно Пн/Ср: правило принято", r["freq"] == "weekly" and r["weekdays"] == [1, 3], r)
    check("следующая дата после Ср — Пн", srv.rec_next_date("2026-11-25", r) == "2026-11-30",
          srv.rec_next_date("2026-11-25", r))
    check("следующая дата после Пн — Ср", srv.rec_next_date("2026-11-30", r) == "2026-12-02",
          srv.rec_next_date("2026-11-30", r))
    r2 = srv.rec_validate({"freq": "weekly", "interval": 2, "weekdays": [1]}, "2026-11-30")
    check("каждые 2 недели: шаг 14 дней", srv.rec_next_date("2026-11-30", r2) == "2026-12-14",
          srv.rec_next_date("2026-11-30", r2))
    rd = srv.rec_validate({"freq": "daily", "interval": 3}, "2026-11-25")
    check("каждые 3 дня", srv.rec_next_date("2026-11-25", rd) == "2026-11-28")
    rm = srv.rec_validate({"freq": "monthly", "monthDay": 31}, "2027-01-31")
    check("ежемесячно 31 → февраль укорочен", srv.rec_next_date("2027-01-31", rm) == "2027-02-28",
          srv.rec_next_date("2027-01-31", rm))
    check("ежемесячно 31 → март", srv.rec_next_date("2027-02-28", rm) == "2027-03-31")
    check("ежемесячно 31 → апрель 30", srv.rec_next_date("2027-03-31", rm) == "2027-04-30")
    rm2 = srv.rec_validate({"freq": "monthly", "interval": 2, "monthDay": 15}, "2026-11-15")
    check("каждые 2 месяца", srv.rec_next_date("2026-11-15", rm2) == "2027-01-15")
    check("переход через год", srv.rec_next_date("2026-12-15", rm2) == "2027-02-15")
    ru = srv.rec_validate({"freq": "daily", "until": "2026-12-05"}, "2026-12-01")
    dates = srv.rec_dates("2026-12-01", ru, set(), 30)
    check("окончание серии ограничивает даты", dates == ["2026-12-02", "2026-12-03", "2026-12-04", "2026-12-05"], dates)
    rs = srv.rec_validate({"freq": "daily"}, "2026-12-01")
    rs["skipped"] = [{"date": "2026-12-03", "reason": "праздник"}]
    dates = srv.rec_dates("2026-12-01", rs, {"2026-12-02"}, 3)
    check("пропущенные и существующие даты не повторяются", dates == ["2026-12-04", "2026-12-05", "2026-12-06"], dates)
    check("горизонт ограничивает создание", srv.rec_dates("2026-12-01", srv.rec_validate({"freq": "daily"}, "2026-12-01"), set(), 30, "2026-12-04") == ["2026-12-02", "2026-12-03", "2026-12-04"])
    check("без повторения дат нет", srv.rec_dates("2026-12-01", srv.rec_validate({}, "2026-12-01"), set(), 5) == [])
    check("описание расписания читаемо", "Еженедельно" in srv.rec_text(r) and "Пн" in srv.rec_text(r), srv.rec_text(r))
    check("описание без повторения", srv.rec_text(srv.rec_validate({}, "2026-12-01")) == "Без повторения")
    for bad, txt in (({"freq": "hourly"}, "периодичность"), ({"freq": "daily", "interval": 99}, "Интервал"),
                     ({"freq": "monthly", "monthDay": 40}, "День месяца"), ({"freq": "daily", "count": 500}, "Число совещаний"),
                     ({"freq": "daily", "until": "2020-01-01"}, "раньше")):
        try:
            srv.rec_validate(bad, "2026-12-01"); good = False; msg = "принято"
        except srv.RpcError as e:
            good = txt in str(e); msg = str(e)
        check("правило отклонено: %s" % bad, good, msg)

    print("== Создание совещания (§2.5) ==")
    m, c, j = meet(adm, title="Штаб по портфелю", projects=["p1", "p3"])
    check("совещание создано", c == 200 and m and m["code"].startswith("СОВ-"), c)
    mid = m["id"]
    check("дата и время сохранены", m["date"] == D(1) and m["time"] == "10:00")
    check("председатель и секретарь назначены", m["chair"] == "u2" and m["secretary"] == "u3")
    check("участники выбраны", set(m["attendees"]) == {"u2", "u3", "u5"})
    check("статус «Запланировано»", m["status"] == "Запланировано")
    check("связанные проекты зарегистрированы при создании", m["projects"] == ["p1", "p3"], m["projects"])
    check("материалы изначально пусты", m["materials"] == [])
    check("история планирования пуста", m["planLog"] == [])
    check("повторение по умолчанию отсутствует", m["recurrence"]["freq"] == "none")
    st = adm.state()
    sm = [x for x in st["meetings"] if x["id"] == mid][0]
    check("состояние содержит новые поля §2.5", all(k in sm for k in ("materials", "projects", "recurrence", "planLog", "seriesId")), list(sm.keys()))
    c, e = adm.err("meeting.create", {"title": "x", "type": "Оперативное", "date": D(1), "time": "10:00",
                                      "chair": "u2", "secretary": "u3", "projects": ["p_none"]})
    check("несуществующий проект отклонён", c in (400, 404), "%s %s" % (c, e))
    c, e = adm.err("meeting.create", {"title": "x", "type": "Оперативное", "date": "2026-13-45", "time": "10:00",
                                      "chair": "u2", "secretary": "u3"})
    check("некорректная дата отклонена", c == 400, "%s %s" % (c, e))

    print("== Связанные проекты ==")
    m2, c, j = adm.res("meeting.projects.set", {"id": mid, "projects": ["p2", "p4"]})
    check("список проектов изменён", c == 200 and m2["projects"] == ["p2", "p4"], c)
    check("изменение проектов в истории планирования", any(x["action"] == "projects" for x in m2["planLog"]))
    m2, c, j = adm.res("meeting.projects.set", {"id": mid, "projects": ["p1", "p1", "p2"]})
    check("дубли проектов исключаются", m2["projects"] == ["p1", "p2"], m2["projects"])
    c, e = adm.err("meeting.projects.set", {"id": mid, "projects": ["p_none"]})
    check("несуществующий проект отклонён", c in (400, 404), "%s %s" % (c, e))
    m2, c, j = adm.res("meeting.projects.set", {"id": mid, "projects": []})
    check("список проектов можно очистить", c == 200 and m2["projects"] == [])
    adm.res("meeting.projects.set", {"id": mid, "projects": ["p1", "p3"]})

    print("== Предварительные материалы ==")
    m2, c, j = adm.res("meeting.materials.add", {"id": mid, "docId": "d1", "note": "К вопросу 1"})
    check("документ M09 прикреплён", c == 200 and len(m2["materials"]) == 1, c)
    mat_doc = m2["materials"][0]
    check("вид материала — документ", mat_doc["kind"] == "doc" and mat_doc["docId"] == "d1")
    check("наименование содержит код документа", "Д-001" in mat_doc["name"], mat_doc["name"])
    check("примечание сохранено", mat_doc["note"] == "К вопросу 1")
    check("автор и дата прикрепления зафиксированы", mat_doc["addedBy"] == "u1" and mat_doc["addedAt"] > 0)
    c, e = adm.err("meeting.materials.add", {"id": mid, "docId": "d1"})
    check("повторное прикрепление документа отклонено", c == 400 and "уже прикреплён" in e, e)
    m2, c, j = adm.res("meeting.materials.add", {"id": mid, "name": "Сводка загрузки", "url": "\\\\srv-files\\plan\\svodka.xlsx"})
    check("ссылка ЛВС прикреплена", c == 200 and m2["materials"][-1]["kind"] == "link", c)
    mat_link = m2["materials"][-1]
    c, e = adm.err("meeting.materials.add", {"id": mid, "name": "дубль", "url": "\\\\srv-files\\plan\\svodka.xlsx"})
    check("повторная ссылка отклонена", c == 400, e)
    c, e = adm.err("meeting.materials.add", {"id": mid, "name": "x", "url": "javascript:alert(1)"})
    check("опасная схема ссылки отклонена", c == 400 and "схема" in e, e)
    c, e = adm.err("meeting.materials.add", {"id": mid, "name": "без адреса"})
    check("ссылка без адреса отклонена", c == 400, e)
    c, e = adm.err("meeting.materials.add", {"id": mid, "docId": "d_none"})
    check("несуществующий документ отклонён", c in (400, 404), e)
    m2, c, j = adm.res("meeting.materials.remove", {"id": mid, "materialId": mat_link["id"]})
    check("материал откреплён", c == 200 and len(m2["materials"]) == 1, c)
    c, e = adm.err("meeting.materials.remove", {"id": mid, "materialId": "mm_none"})
    check("открепление несуществующего материала отклонено", c == 400, e)
    for i in range(19):
        adm.res("meeting.materials.add", {"id": mid, "name": "Материал %d" % i, "url": "https://lan.local/m/%d" % i})
    m2, c, j = adm.res("meeting.materials.add", {"id": mid, "name": "лишний", "url": "https://lan.local/m/over"})
    check("ограничение 20 материалов соблюдается", c == 400 and "не более" in (j.get("error") or {}).get("message", ""), c)
    st = adm.state()
    sm = [x for x in st["meetings"] if x["id"] == mid][0]
    check("в состоянии 20 материалов", len(sm["materials"]) == 20, len(sm["materials"]))

    print("== Уведомления о материалах и права ==")
    exe = Client(); exe.login("novikov")
    n, c, j = exe.res("notif.list", {"limit": 100})
    types = [x["type"] for x in (n or {}).get("items", [])]
    check("участник получил уведомление о материалах", "meeting.materials" in types, types[:6])
    c, e = exe.err("meeting.materials.add", {"id": mid, "name": "x", "url": "https://x"})
    check("исполнитель не может прикреплять материалы (403)", c == 403, "%s %s" % (c, e))
    obs = Client(); obs.login("zaytseva")
    c, e = obs.err("meeting.projects.set", {"id": mid, "projects": ["p1"]})
    check("наблюдатель не может менять проекты (403)", c == 403, c)
    c, e = obs.err("meeting.reschedule", {"id": mid, "date": D(4), "reason": "попытка"})
    check("наблюдатель не может переносить (403)", c == 403, c)
    c, e = exe.err("meeting.cancel", {"id": mid, "reason": "попытка"})
    check("исполнитель не может отменять (403)", c == 403, c)
    pm = Client(); pm.login("volkova")
    m3, c, j = meet(pm, title="Совещание РП")
    check("руководитель проекта создаёт совещание", c == 200 and m3, c)
    pmid = m3["id"]
    m3, c, j = pm.res("meeting.materials.add", {"id": pmid, "docId": "d2"})
    check("руководитель проекта прикрепляет материал", c == 200, c)

    print("== Перенос совещания ==")
    c, e = adm.err("meeting.reschedule", {"id": mid, "date": D(3)})
    check("перенос без основания отклонён", c == 400, e)
    c, e = adm.err("meeting.reschedule", {"id": mid, "date": D(3), "reason": "х"})
    check("короткое основание отклонено", c == 400, e)
    c, e = adm.err("meeting.reschedule", {"id": mid, "date": D(1), "time": "10:00", "reason": "без изменений"})
    check("перенос без изменения даты отклонён", c == 400 and "не изменились" in e, e)
    c, e = adm.err("meeting.reschedule", {"id": mid, "date": "2026-02-30", "reason": "некорректная дата"})
    check("некорректная дата переноса отклонена", c == 400, e)
    c, e = adm.err("meeting.reschedule", {"id": mid, "date": D(3), "time": "25:99", "reason": "некорректное время"})
    check("некорректное время переноса отклонено", c == 400, e)
    rs, c, j = adm.res("reminder.list", {"ownerType": "meeting", "ownerId": mid})
    before_fire = sorted(x["fire_at"] for x in (rs or []))
    m2, c, j = adm.res("meeting.reschedule", {"id": mid, "date": D(4), "time": "12:30", "reason": "Перенос по просьбе заказчика"})
    check("перенос выполнен", c == 200 and m2["date"] == D(4) and m2["time"] == "12:30", c)
    mv = [x for x in m2["planLog"] if x["action"] == "move"]
    check("перенос зафиксирован в истории", len(mv) == 1, m2["planLog"])
    check("история хранит прежние и новые дату/время", mv and mv[0]["from"]["date"] == D(1) and mv[0]["to"]["date"] == D(4), mv)
    check("основание переноса сохранено", mv and "заказчика" in mv[0]["reason"])
    rs, c, j = adm.res("reminder.list", {"ownerType": "meeting", "ownerId": mid})
    after_fire = sorted(x["fire_at"] for x in (rs or []))
    check("напоминания пересчитаны от новой даты", after_fire and after_fire != before_fire, "%d → %d" % (len(before_fire), len(after_fire)))
    check("число напоминаний сохранено", len(after_fire) == len(before_fire))
    n, c, j = exe.res("notif.list", {"limit": 100})
    check("участник оповещён о переносе", "meeting.moved" in [x["type"] for x in (n or {}).get("items", [])])
    evs = adm.state()["events"]
    check("событие переноса в журнале", any((e.get("details") or {}).get("event") == "MEETING_RESCHEDULED" for e in evs))

    print("== Отмена и возобновление ==")
    c, e = adm.err("meeting.cancel", {"id": mid})
    check("отмена без основания отклонена", c == 400, e)
    c, e = adm.err("meeting.cancel", {"id": mid, "reason": "х"})
    check("короткое основание отмены отклонено", c == 400, e)
    m2, c, j = adm.res("meeting.cancel", {"id": mid, "reason": "Отпуск председателя"})
    check("совещание отменено", c == 200 and m2["status"] == "Отменено", c)
    check("отмена в истории планирования", any(x["action"] == "cancel" for x in m2["planLog"]))
    rs, c, j = adm.res("reminder.list", {"ownerType": "meeting", "ownerId": mid})
    check("напоминания сняты", rs == [], rs)
    n, c, j = exe.res("notif.list", {"limit": 100})
    check("участник оповещён об отмене", "meeting.cancelled" in [x["type"] for x in (n or {}).get("items", [])])
    c, e = adm.err("meeting.cancel", {"id": mid, "reason": "повторно"})
    check("повторная отмена отклонена", c == 400, e)
    c, e = adm.err("meeting.agenda.add", {"id": mid, "title": "вопрос", "reporter": "u3"})
    check("повестка отменённого совещания не меняется", c == 400 and "отменено" in e, e)
    c, e = adm.err("meeting.conduct", {"id": mid})
    check("отменённое совещание нельзя провести", c == 400, e)
    c, e = adm.err("meeting.update", {"id": mid, "title": "новое"})
    check("параметры отменённого совещания не меняются", c == 400, e)
    c, e = adm.err("meeting.materials.add", {"id": mid, "name": "x", "url": "https://x"})
    check("материалы к отменённому не добавляются", c == 400, e)
    c, e = adm.err("meeting.reschedule", {"id": mid, "date": D(6), "reason": "перенос отменённого"})
    check("отменённое совещание не переносится", c == 400, e)
    c, e = adm.err("meeting.restore", {"id": pmid})
    check("возобновление неотменённого отклонено", c == 400, e)
    m2, c, j = adm.res("meeting.restore", {"id": mid, "date": D(5), "reason": "Председатель вернулся"})
    check("совещание возобновлено", c == 200 and m2["status"] == "Запланировано" and m2["date"] == D(5), c)
    check("возобновление в истории", any(x["action"] == "restore" for x in m2["planLog"]))
    rs, c, j = adm.res("reminder.list", {"ownerType": "meeting", "ownerId": mid})
    check("напоминания восстановлены", len(rs or []) > 0, rs)
    st = adm.state()
    check("отменённые совещания остаются в общем календаре", any(x["id"] == mid for x in st["meetings"]))
    today_m, c, j = meet(adm, title="Совещание сегодня", date=D(0))
    tid = today_m["id"]
    adm.res("meeting.conduct", {"id": tid})
    c, e = adm.err("meeting.cancel", {"id": tid, "reason": "отмена проведённого"})
    check("проведённое совещание не отменяется", c == 400, e)
    c, e = adm.err("meeting.reschedule", {"id": tid, "date": D(2), "reason": "перенос проведённого"})
    check("проведённое совещание не переносится", c == 400, e)

    print("== Изменение параметров ==")
    m2, c, j = adm.res("meeting.update", {"id": pmid, "date": D(3), "time": "15:00", "reason": "Уточнение расписания"})
    check("изменение даты через карточку выполняется", c == 200 and m2["date"] == D(3), c)
    check("изменение даты попадает в историю планирования", any(x["action"] == "move" for x in m2["planLog"]))
    m2, c, j = adm.res("meeting.update", {"id": pmid, "attendees": ["u3", "u5", "u6"]})
    check("изменение состава попадает в историю", any(x["action"] == "params" for x in m2["planLog"]))

    print("== Регулярные совещания: серия ==")
    ms, c, j = meet(adm, title="Регулярный штаб", date=D(1), time="09:00",
                    recurrence={"freq": "weekly", "interval": 1, "weekdays": [1, 3]})
    check("головное совещание серии создано", c == 200 and ms["recurrence"]["master"] is True, c)
    sid = ms["seriesId"]; smid = ms["id"]
    check("серия имеет идентификатор", bool(sid))
    ser, c, j = adm.res("meeting.series.list", {"id": smid})
    check("состав серии получен", c == 200 and ser["seriesId"] == sid, c)
    items = ser["items"]
    check("ближайшие даты созданы отдельными карточками", len(items) >= 3, len(items))
    check("первая карточка — головная", items[0]["master"] is True and items[0]["n"] == 1)
    check("номера карточек последовательны", [x["n"] for x in items] == list(range(1, len(items) + 1)))
    check("даты серии соответствуют дням недели правила", all(wd(x["date"]) in (1, 3) for x in items if not x["master"]),
          [(x["date"], wd(x["date"])) for x in items])
    check("дата головного совещания сохранена как задана", items[0]["date"] == ms["date"])
    check("у каждой карточки свой протокол", all(x["protocol"] == "Подготовка" for x in items))
    check("коды карточек уникальны", len({x["code"] for x in items}) == len(items))
    check("все карточки запланированы", all(x["status"] == "Запланировано" for x in items))
    occ = [x for x in items if not x["master"]][0]
    adm.res("meeting.agenda.add", {"id": occ["id"], "title": "Вопрос только этой карточки", "reporter": "u3"})
    ser2, c, j = adm.res("meeting.series.list", {"id": smid})
    other = [x for x in ser2["items"] if x["id"] != occ["id"]]
    check("повестка карточки независима от серии", all(x["agenda"] == 0 for x in other),
          [(x["code"], x["agenda"]) for x in ser2["items"]])
    check("у карточки серии есть своя повестка", [x for x in ser2["items"] if x["id"] == occ["id"]][0]["agenda"] == 1)
    g, c, j = adm.res("meeting.series.generate", {"id": smid})
    check("повторное создание ближайших дат идемпотентно", c == 200 and g["created"] == [], g)
    g, c, j = adm.res("meeting.series.generate", {"id": smid, "count": 3})
    check("явное создание ещё N карточек", c == 200 and len(g["created"]) == 3, g)
    c, e = adm.err("meeting.series.generate", {"id": occ["id"]})
    check("генерация из неголовной карточки отклонена", c == 400, e)
    c, e = adm.err("meeting.series.generate", {"id": smid, "count": 0})
    check("нулевое число создаваемых карточек отклонено", c == 400, e)
    c, e = adm.err("meeting.series.update", {"id": occ["id"], "rule": {"freq": "daily"}})
    check("расписание меняется только в головной карточке", c == 400, e)
    c, e = adm.err("meeting.series.update", {"id": smid, "rule": {"freq": "hourly"}})
    check("неизвестная периодичность отклонена", c == 400 and "периодичность" in e, e)
    c, e = adm.err("meeting.series.update", {"id": smid, "rule": {"freq": "daily", "interval": 99}})
    check("недопустимый интервал отклонён", c == 400, e)
    c, e = adm.err("meeting.series.update", {"id": smid, "rule": {"freq": "daily", "count": 500}})
    check("недопустимое число совещаний отклонено", c == 400, e)
    c, e = adm.err("meeting.series.update", {"id": smid, "rule": "ежедневно"})
    check("расписание нестрокой-объектом отклонено", c == 400, e)

    print("== Пропуск даты и изменение расписания ==")
    ser, c, j = adm.res("meeting.series.list", {"id": smid})
    future = [x for x in ser["items"] if x["date"] > ms["date"] and x["status"] == "Запланировано"][0]
    c, e = adm.err("meeting.series.skip", {"id": smid, "date": future["date"], "reason": "х"})
    check("короткое основание пропуска отклонено", c == 400, e)
    c, e = adm.err("meeting.series.skip", {"id": smid, "date": D(0), "reason": "прошлая дата"})
    check("пропуск даты раньше головной отклонён", c == 400, e)
    m2, c, j = adm.res("meeting.series.skip", {"id": smid, "date": future["date"], "reason": "Праздничный день"})
    check("дата серии пропущена", c == 200, c)
    check("пропуск записан в правило", any(x["date"] == future["date"] for x in m2["recurrence"]["skipped"]))
    ser, c, j = adm.res("meeting.series.list", {"id": smid})
    skipped_card = [x for x in ser["items"] if x["date"] == future["date"]]
    check("карточка на пропущенную дату отменена", skipped_card and skipped_card[0]["status"] == "Отменено", skipped_card)
    check("счётчик отменённых в серии обновлён", ser["cancelled"] >= 1, ser["cancelled"])
    g, c, j = adm.res("meeting.series.generate", {"id": smid})
    check("пропущенная дата не создаётся заново", all(x["date"] != future["date"] for x in g["created"]), g["created"])
    m2, c, j = adm.res("meeting.series.update", {"id": smid, "rule": {"freq": "daily", "interval": 2}, "applyToFuture": True})
    check("расписание изменено на каждые 2 дня", c == 200 and m2["recurrence"]["freq"] == "daily" and m2["recurrence"]["interval"] == 2, c)
    check("изменение расписания в истории", any(x["action"] == "series" for x in m2["planLog"]))
    m2, c, j = adm.res("meeting.series.update", {"id": smid, "rule": {"freq": "none"}})
    check("повторение отключается", c == 200 and m2["recurrence"]["freq"] == "none" and m2["recurrence"]["master"] is False, c)
    ser, c, j = adm.res("meeting.series.list", {"id": smid})
    check("созданные карточки серии сохраняются", len(ser["items"]) >= 3, len(ser["items"]))

    print("== Ограничения серии: число, окончание, ежемесячно ==")
    mc, c, j = meet(adm, title="Ежедневный стендап", date=D(0), time="09:15",
                    recurrence={"freq": "daily", "interval": 1, "count": 4})
    check("серия с ограничением по числу создана", c == 200, c)
    ser, c, j = adm.res("meeting.series.list", {"id": mc["id"]})
    check("число карточек не превышает заданное", len(ser["items"]) == 4, len(ser["items"]))
    g, c, j = adm.res("meeting.series.generate", {"id": mc["id"], "count": 5})
    check("сверх заданного числа карточки не создаются", g["created"] == [], g)
    mu, c, j = meet(adm, title="Серия до даты", date=D(0), time="08:00",
                    recurrence={"freq": "daily", "until": D(3)})
    ser, c, j = adm.res("meeting.series.list", {"id": mu["id"]})
    check("окончание серии соблюдается", all(x["date"] <= D(3) for x in ser["items"]) and len(ser["items"]) == 4,
          [x["date"] for x in ser["items"]])
    mm, c, j = meet(adm, title="Месячный отчёт", date="2027-01-31", time="14:00",
                    recurrence={"freq": "monthly", "monthDay": 31, "count": 4})
    g, c, j = adm.res("meeting.series.generate", {"id": mm["id"], "count": 5})
    ser, c, j = adm.res("meeting.series.list", {"id": mm["id"]})
    check("ежемесячная серия учитывает длину месяца",
          [x["date"] for x in ser["items"]] == ["2027-01-31", "2027-02-28", "2027-03-31", "2027-04-30"],
          [x["date"] for x in ser["items"]])
    mw, c, j = meet(adm, title="Раз в две недели", date="2026-12-07", time="10:00",
                    recurrence={"freq": "weekly", "interval": 2, "weekdays": [1], "count": 3})
    g, c, j = adm.res("meeting.series.generate", {"id": mw["id"], "count": 3})
    ser, c, j = adm.res("meeting.series.list", {"id": mw["id"]})
    check("интервал 2 недели даёт шаг 14 дней",
          [x["date"] for x in ser["items"]] == ["2026-12-07", "2026-12-21", "2027-01-04"],
          [x["date"] for x in ser["items"]])

    print("== Планировщик: автоматическое создание дат серии ==")
    ma, c, j = meet(adm, title="Автопродление серии", date=D(0), time="07:30",
                    recurrence={"freq": "daily", "until": D(6)})
    aid = ma["id"]
    ser, c, j = adm.res("meeting.series.list", {"id": aid})
    n0 = len(ser["items"])
    check("серия создана на горизонт планировщика", n0 == 7, n0)
    c, j = adm.rpc("notif.tick")
    ser, c, j = adm.res("meeting.series.list", {"id": aid})
    check("повторный цикл не дублирует карточки", len(ser["items"]) == n0, len(ser["items"]))
    adm.res("meeting.update", {"id": aid, "date": D(-4), "reason": "Уточнение даты головного совещания"})
    c, j = adm.rpc("notif.tick")
    check("цикл планировщика выполнен", c == 200, c)
    ser, c, j = adm.res("meeting.series.list", {"id": aid})
    check("планировщик достроил пропущенные даты серии", len(ser["items"]) == n0 + 4, len(ser["items"]))
    lg, c, j = adm.res("admin.logs", {"name": "supip.log", "lines": 400})
    txt = "\n".join((lg or {}).get("lines") or [])
    check("автосоздание дат отражено в файловом журнале", "Регулярные совещания" in txt)
    evs = adm.state()["events"]
    check("событие автосоздания серии в журнале событий",
          any((e.get("details") or {}).get("event") == "MEETING_SERIES_AUTO" for e in evs))

    print("== Протокол и выгрузка (§2.5 в документе) ==")
    m2, c, j = adm.res("meeting.materials.add", {"id": pmid, "name": "Схема стенда", "url": "https://lan.local/stand.pdf", "note": "для доклада"})
    code, data = adm.req("GET", "/api/export?kind=meeting&id=%s&fmt=docx" % pmid)
    check("протокол DOCX выгружен", code == 200 and data[:2] == b"PK", code)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        doc = z.read("word/document.xml").decode("utf-8")
    check("DOCX: раздел связанных проектов", "Связанные проекты" in doc)
    check("DOCX: предварительные материалы", "Предварительные материалы" in doc)
    check("DOCX: наименование материала", "Схема стенда" in doc)
    check("DOCX: повторение указано", "Повторение" in doc)
    check("DOCX: история планирования", "Планирование: переносы, отмены, изменения" in doc)
    code, data = adm.req("GET", "/api/export?kind=meeting&id=%s&fmt=pdf" % pmid)
    check("протокол PDF выгружен", code == 200 and data[:5] == b"%PDF-", code)

    print("== Резервная копия и схема БД ==")
    c, raw = adm.req("GET", "/api/backup")
    bk = json.loads(raw if isinstance(raw, (bytes, str)) else json.dumps(raw))
    rows = (bk.get("tables") or {}).get("meetings") or []
    check("копия содержит совещания", c == 200 and len(rows) > 0, c)
    row = [x for x in rows if x["id"] == smid]
    check("копия хранит материалы и проекты", row and "materials" in row[0] and "projects" in row[0], list(row[0].keys()) if row else None)
    check("копия хранит расписание и историю", row and "recurrence" in row[0] and "plan_log" in row[0] and "series_id" in row[0])
    c, j = adm.req("POST", "/api/restore", bk)
    check("восстановление из копии выполнено", c == 200, (c, j if c != 200 else ""))
    adm.login("admin")
    st = adm.state()
    sm = [x for x in st["meetings"] if x["id"] == smid]
    check("после восстановления расписание сохранено", sm and sm[0]["recurrence"]["freq"] in ("none", "daily"), sm[:1])
    check("после восстановления история сохранена", sm and isinstance(sm[0]["planLog"], list) and len(sm[0]["planLog"]) > 0)
    src = open(SERVER, encoding="utf-8").read()
    check("схема БД не ниже версии 5 (планирование §2.5)", srv.SCHEMA_VERSION >= 5, srv.SCHEMA_VERSION)
    check("миграция 5 добавляет поля планирования", "ADD COLUMN IF NOT EXISTS materials" in src and "ADD COLUMN IF NOT EXISTS series_id" in src)

    adm.rpc("admin.reset")
    print("\nИТОГ: %d ok, %d FAIL" % (ok, fail))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
