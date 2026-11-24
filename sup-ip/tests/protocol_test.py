#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
СУП-ИП. Испытания маршрута согласования и версий протокола (ТЗ M16 §2.9).
Запуск при работающем сервере:  python3 tests/protocol_test.py --base http://127.0.0.1:8000
Проверяется: 8 статусов протокола, настройка маршрута (секретарь → председатель → руководитель →
иные согласующие), регистрация даты направления, согласующего, результата и замечаний,
возврат на доработку, утверждение отдельной версией (DOCX+PDF в M09 и снимок содержания),
новая редакция с сохранением истории, публикация, архив, права, уведомления и аудит.
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
        return self.rpc(action, payload)[1].get("result")

def _st(x):
    """Состояние пользователя: при передаче клиента состояние обновляется с сервера."""
    if isinstance(x, Client):
        x.rpc("notif.list")
        return x.state or {}
    return x or {}

def meet(st, mid):
    return next((m for m in _st(st)["meetings"] if m["id"] == mid), {})

def prot(st, mid):
    return meet(st, mid).get("protocol") or {}

def notifs(cl, type_, obj_id):
    cl.rpc("notif.list")
    return [n for n in cl.state["notifications"] if n["type"] == type_ and n["obj_id"] == obj_id]

def events(st, name):
    return [e for e in st["events"] if isinstance(e.get("details"), dict) and e["details"].get("event") == name]

def main():
    global BASE
    ap = argparse.ArgumentParser(); ap.add_argument("--base", default=BASE)
    BASE = ap.parse_args().base.rstrip("/")

    adm = Client(); c, j = adm.login("admin")
    check("вход администратора", c == 200 and j.get("ok"), c)
    c, j = adm.rpc("admin.reset")
    check("подготовка эталонного набора", c == 200 and j.get("ok"), j.get("error"))

    sec = Client(); sec.login("volkova")     # u3 — руководитель проектов, секретарь
    chair = Client(); chair.login("morozov") # u2 — руководитель подразделения, председатель
    other = Client(); other.login("lebedev") # u4 — иной согласующий
    exe = Client(); exe.login("novikov")     # u5 — исполнитель
    obs = Client(); obs.login("zaytseva")    # u8 — наблюдатель

    print("== Создание совещания и исходное состояние протокола ==")
    c, j = adm.rpc("meeting.create", {"title": "Испытания маршрута протокола", "type": "Техническое",
                                      "date": D(0), "time": "11:00", "chair": "u2", "secretary": "u3",
                                      "attendees": ["u4", "u5"]})
    m = j.get("result") or {}; mid = m.get("id")
    check("совещание создано", c == 200 and mid, j.get("error"))
    pr = prot(adm, mid)
    check("протокол в статусе «Подготовка»", pr.get("status") == "Подготовка", pr.get("status"))
    check("номер версии 0, маршрут и история пусты",
          pr.get("version") == 0 and pr.get("route") == [] and pr.get("versions") == [], pr)
    check("дата направления и утверждения не заполнены",
          pr.get("sentAt") is None and pr.get("approvedAt") is None)

    print("== Настройка маршрута согласования (ТЗ §2.9) ==")
    route = [{"user": "u3", "role": "Секретарь"}, {"user": "u2", "role": "Председатель"},
             {"user": "u4", "role": "Согласующий"}]
    c, j = adm.rpc("protocol.route", {"id": mid, "route": route})
    pr = prot(adm, mid)
    check("маршрут сохранён", c == 200 and len(pr["route"]) == 3, j.get("error"))
    check("порядок согласующих сохранён", [x["user"] for x in pr["route"]] == ["u3", "u2", "u4"])
    check("роли маршрута соответствуют ТЗ",
          [x["role"] for x in pr["route"]] == ["Секретарь", "Председатель", "Согласующий"])
    check("все шаги в состоянии «Ожидает»", all(x["state"] == "Ожидает" for x in pr["route"]))
    c, j = adm.rpc("protocol.route", {"id": mid, "route": route + [{"user": "u3", "role": "Секретарь"}]})
    check("повторное назначение одного лица исключается", len(prot(adm, mid)["route"]) == 3)
    c, j = adm.rpc("protocol.route", {"id": mid, "route": [{"user": "u9", "role": "Согласующий"}]})
    check("несуществующий согласующий отклоняется", c == 400, c)
    c, j = adm.rpc("protocol.route", {"id": mid, "route": [{"user": "u%d" % i} for i in range(2, 9)] +
                                     [{"user": "u1"}, {"user": "u3"}]})
    check("маршрут ограничен 8 согласующими", len(prot(adm, mid)["route"]) == 8,
          len(prot(adm, mid)["route"]))
    adm.rpc("protocol.route", {"id": mid, "route": route})
    c, j = exe.rpc("protocol.route", {"id": mid, "route": [{"user": "u5"}]})
    check("исполнитель без права правки совещания → 403", c == 403, c)
    c, j = obs.rpc("protocol.route", {"id": mid, "route": []})
    check("наблюдатель не может менять маршрут → 403", c == 403, c)

    print("== Направление на согласование ==")
    c, j = adm.rpc("protocol.send", {"id": mid})
    check("до проведения совещания направить нельзя", c == 400, c)
    c, j = adm.rpc("meeting.conduct", {"id": mid})
    check("совещание проведено", c == 200 and meet(adm, mid)["status"] == "Проведено", j.get("error"))
    check("протокол переведён в «Черновик»", prot(adm, mid)["status"] == "Черновик")
    c, j = adm.rpc("meeting.agenda.add", {"id": mid, "title": "Ход модернизации РРЛ-240", "reporter": "u3"})
    check("вопрос повестки добавлен", c == 200 and len(meet(adm, mid)["agenda"]) == 1, j.get("error"))
    c, j = adm.rpc("meeting.decision.add", {"id": mid, "text": "Завершить пусконаладку до конца месяца",
                                            "resp": "u5", "due": D(5)})
    check("решение зафиксировано", c == 200 and len(meet(adm, mid)["decisions"]) == 1, j.get("error"))
    c, j = adm.rpc("protocol.send", {"id": mid, "note": "Протокол подготовлен секретарём"})
    pr = prot(adm, mid)
    check("протокол направлен на согласование", c == 200 and pr["status"] == "Направлен на согласование", j.get("error"))
    check("дата направления и автор зафиксированы", bool(pr["sentAt"]) and pr["sentBy"] == "u1", pr)
    check("отметка о направлении в истории комментария",
          any(x["kind"] == "Направление" for x in pr["comments"]), pr["comments"])
    check("первый согласующий уведомлён", len(notifs(sec, "protocol.review", mid)) == 1)
    check("второй согласующий ещё не уведомлён", len(notifs(chair, "protocol.review", mid)) == 0)

    print("== Очередь согласования и замечания ==")
    c, j = chair.rpc("protocol.review", {"id": mid, "decision": "Согласовано"})
    check("согласование не в свою очередь отклоняется (403)", c == 403, c)
    check("в отказе указан текущий согласующий",
          "Волкова" in (j.get("error") or {}).get("message", ""), j.get("error"))
    c, j = sec.rpc("protocol.review", {"id": mid, "decision": "Замечания"})
    check("замечания без текста отклоняются", c == 400, c)
    c, j = sec.rpc("protocol.review", {"id": mid, "decision": "Непонятно"})
    check("недопустимый результат отклоняется", c == 400, c)
    c, j = sec.rpc("protocol.review", {"id": mid, "decision": "Замечания", "comment": "Уточнить сроки по п.2"})
    pr = prot(adm, mid)
    check("протокол возвращён на доработку", c == 200 and pr["status"] == "На доработке", j.get("error"))
    check("результат и замечание шага зарегистрированы",
          pr["route"][0]["state"] == "Замечания" and "Уточнить сроки" in pr["route"][0]["comment"], pr["route"][0])
    check("дата результата согласования зафиксирована", bool(pr["route"][0]["ts"]))
    check("инициатор направления уведомлён о доработке", len(notifs(adm, "protocol.rework", mid)) >= 1)
    c, j = adm.rpc("protocol.route", {"id": mid, "route": route})
    check("в статусе «На доработке» маршрут можно менять", c == 200, j.get("error"))
    c, j = adm.rpc("protocol.send", {"id": mid, "note": "Замечания устранены"})
    pr = prot(adm, mid)
    check("повторное направление после доработки", pr["status"] == "Направлен на согласование")
    check("шаги маршрута сброшены в «Ожидает»", all(x["state"] == "Ожидает" for x in pr["route"]))

    print("== Прохождение маршрута ==")
    c, j = sec.rpc("protocol.review", {"id": mid, "decision": "Согласовано", "comment": "Замечаний нет"})
    pr = prot(adm, mid)
    check("первый согласующий согласовал", c == 200 and pr["route"][0]["state"] == "Согласовано", j.get("error"))
    check("протокол остаётся на согласовании", pr["status"] == "Направлен на согласование")
    check("очередь передана председателю", len(notifs(chair, "protocol.review", mid)) == 1)
    c, j = chair.rpc("protocol.review", {"id": mid, "decision": "Согласовано"})
    check("председатель согласовал", c == 200 and prot(adm, mid)["route"][1]["state"] == "Согласовано", j.get("error"))
    c, j = other.rpc("protocol.review", {"id": mid, "decision": "Согласовано", "comment": "Согласовано без замечаний"})
    pr = prot(adm, mid)
    check("последний согласующий согласовал", c == 200 and pr["route"][2]["state"] == "Согласовано", j.get("error"))
    check("статус «Согласован» после всего маршрута", pr["status"] == "Согласован", pr["status"])
    c, j = other.rpc("protocol.review", {"id": mid, "decision": "Согласовано"})
    check("повторное согласование отклоняется", c == 400, c)

    print("== Утверждение отдельной версией ==")
    c, j = adm.rpc("protocol.publish", {"id": mid})
    check("публикация неутверждённого протокола отклоняется", c == 400, c)
    c, j = adm.rpc("protocol.archive", {"id": mid})
    check("архивирование неутверждённого протокола отклоняется", c == 400, c)
    c, j = adm.rpc("protocol.newVersion", {"id": mid})
    check("новая редакция до утверждения отклоняется", c == 400, c)
    c, j = exe.rpc("protocol.approve", {"id": mid})
    check("исполнитель не может утверждать протокол (403)", c == 403, c)
    c, j = chair.rpc("protocol.approve", {"id": mid, "note": "Утверждено на совещании"})
    res = j.get("result") or {}
    pr = prot(chair, mid)
    check("руководитель подразделения утвердил протокол", c == 200 and res.get("version") == 1, j.get("error"))
    check("статус протокола «Утверждён»", pr["status"] == "Утверждён", pr["status"])
    check("статус совещания «Протокол утверждён»", meet(chair, mid)["status"] == "Протокол утверждён")
    check("дата и лицо утверждения зафиксированы", bool(pr["approvedAt"]) and pr["approvedBy"] == "u2", pr)
    check("версия сохранена в истории редакций", len(pr["versions"]) == 1 and pr["versions"][0]["v"] == 1)
    v1 = pr["versions"][0]
    check("снимок повестки сохранён в редакции", len(v1["agenda"]) == 1, v1["agenda"])
    check("снимок решений сохранён в редакции", len(v1["decisions"]) == 1, v1["decisions"])
    check("редакция ссылается на документ M09", bool(v1["docId"]) and v1["docCode"].startswith("ДОК-"), v1)
    check("в редакции зарегистрированы DOCX и PDF",
          sorted(x["fmt"] for x in v1["files"]) == ["docx", "pdf"], v1["files"])
    doc = next((d for d in chair.state["docs"] if d["id"] == v1["docId"]), {})
    check("документ протокола создан в M09", doc.get("type") == "Протокол", doc.get("type"))
    check("участники уведомлены об утверждении", len(notifs(sec, "protocol.approved", mid)) >= 1)
    c, blob = adm.req("GET", "/api/files/%s/%d" % (v1["docId"], v1["docV"]), raw=True)
    check("файл DOCX утверждённой редакции выгружается", c == 200 and blob[:2] == b"PK", c)
    c, blob = adm.req("GET", "/api/files/%s/%d?fmt=pdf" % (v1["docId"], v1["docV"]), raw=True)
    check("файл PDF утверждённой редакции выгружается", c == 200 and blob[:4] == b"%PDF", c)
    c, j = adm.rpc("protocol.route", {"id": mid, "route": route})
    check("маршрут утверждённого протокола менять нельзя", c == 400, c)
    c, j = adm.rpc("protocol.send", {"id": mid})
    check("утверждённый протокол нельзя направить повторно", c == 400, c)

    print("== Публикация и новая редакция с сохранением истории ==")
    c, j = adm.rpc("protocol.publish", {"id": mid})
    pr = prot(adm, mid)
    check("протокол опубликован", c == 200 and pr["status"] == "Опубликован", j.get("error"))
    check("дата публикации зафиксирована", bool(pr["publishedAt"]))
    check("участники уведомлены о публикации", len(notifs(other, "protocol.published", mid)) >= 1)
    c, j = sec.rpc("protocol.newVersion", {"id": mid, "note": "Дополнено решение по п.3"})
    pr = prot(sec, mid)
    check("секретарь создал новую редакцию", c == 200 and pr["status"] == "Черновик", j.get("error"))
    check("история предыдущей редакции сохранена", len(pr["versions"]) == 1 and pr["version"] == 1)
    check("отметки направления и утверждения сброшены",
          pr["sentAt"] is None and pr["approvedAt"] is None and pr["publishedAt"] is None)
    check("совещание возвращено в статус «Проведено»", meet(sec, mid)["status"] == "Проведено")
    check("шаги маршрута сброшены для новой редакции", all(x["state"] == "Ожидает" for x in pr["route"]))
    c, j = adm.rpc("meeting.decision.add", {"id": mid, "text": "Провести повторные испытания стенда",
                                            "resp": "u7", "due": D(9)})
    check("в новую редакцию добавлено решение", len(meet(adm, mid)["decisions"]) == 2, j.get("error"))
    adm.rpc("protocol.send", {"id": mid, "note": "Редакция 2"})
    sec.rpc("protocol.review", {"id": mid, "decision": "Согласовано"})
    chair.rpc("protocol.review", {"id": mid, "decision": "Согласовано"})
    c, j = other.rpc("protocol.review", {"id": mid, "decision": "Согласовано"})
    check("вторая редакция прошла маршрут", prot(other, mid)["status"] == "Согласован", j.get("error"))
    c, j = chair.rpc("protocol.approve", {"id": mid, "note": "Редакция 2"})
    pr = prot(chair, mid)
    check("вторая редакция утверждена", c == 200 and pr["version"] == 2, j.get("error"))
    check("история содержит две редакции", [x["v"] for x in pr["versions"]] == [1, 2], pr["versions"])
    check("снимок второй редакции отличается от первой",
          len(pr["versions"][1]["decisions"]) == 2 and len(pr["versions"][0]["decisions"]) == 1)
    check("вторая редакция зарегистрирована как новая версия документа M09",
          pr["versions"][1]["docV"] == pr["versions"][0]["docV"] + 1, pr["versions"])
    c, blob = adm.req("GET", "/api/files/%s/%d" % (pr["versions"][0]["docId"], pr["versions"][0]["docV"]), raw=True)
    check("файл первой редакции остался доступен", c == 200 and blob[:2] == b"PK", c)

    print("== Архив и типовые сценарии ==")
    c, j = adm.rpc("protocol.archive", {"id": mid})
    check("протокол переведён в архив", c == 200 and prot(adm, mid)["status"] == "Архивный", j.get("error"))
    c, j = adm.rpc("protocol.newVersion", {"id": mid})
    check("из архива создаётся новая редакция", c == 200 and prot(adm, mid)["status"] == "Черновик", j.get("error"))
    check("история редакций сохранена после архива", len(prot(adm, mid)["versions"]) == 2)

    # совещание без настроенного маршрута: применяется типовой маршрут §2.9
    c, j = adm.rpc("meeting.create", {"title": "Типовой маршрут протокола", "type": "Оперативное",
                                      "date": D(0), "time": "12:00", "chair": "u2", "secretary": "u3",
                                      "attendees": ["u5"]})
    mid2 = (j.get("result") or {}).get("id")
    adm.rpc("meeting.conduct", {"id": mid2})
    c, j = adm.rpc("protocol.send", {"id": mid2})
    pr2 = prot(adm, mid2)
    check("типовой маршрут построен автоматически", c == 200 and len(pr2["route"]) >= 2, j.get("error"))
    check("типовой маршрут: секретарь → председатель → руководитель",
          [x["role"] for x in pr2["route"]] == ["Секретарь", "Председатель"] or
          [x["role"] for x in pr2["route"]] == ["Секретарь", "Председатель", "Руководитель подразделения"],
          pr2["route"])
    c, j = adm.rpc("meeting.approve", {"id": mid2})
    check("утверждение без маршрута отклоняется", c == 400, c)
    sec.rpc("protocol.review", {"id": mid2, "decision": "Согласовано"})
    chair.rpc("protocol.review", {"id": mid2, "decision": "Согласовано"})
    if prot(chair, mid2)["status"] != "Согласован":
        adm.rpc("protocol.review", {"id": mid2, "decision": "Согласовано"})
    c, j = chair.rpc("meeting.approve", {"id": mid2})
    check("meeting.approve использует единый контур §2.9",
          c == 200 and prot(chair, mid2)["version"] == 1, j.get("error"))
    check("версия содержит файлы DOCX и PDF",
          sorted(x["fmt"] for x in prot(chair, mid2)["versions"][0]["files"]) == ["docx", "pdf"])

    print("== Права, аудит и резервная копия ==")
    c, j = obs.rpc("protocol.publish", {"id": mid2})
    check("наблюдатель не может публиковать протокол (403)", c == 403, c)
    c, j = exe.rpc("protocol.review", {"id": mid2, "decision": "Согласовано"})
    check("посторонний исполнитель не может согласовывать (403/400)", c in (400, 403), c)
    adm.rpc("notif.list")
    evs = adm.state["events"]
    for name in ("MEETING_PROTOCOL_ROUTE_SET", "MEETING_PROTOCOL_SENT_FOR_APPROVAL", "MEETING_PROTOCOL_REWORK",
                 "MEETING_PROTOCOL_AGREED", "MEETING_PROTOCOL_APPROVED", "MEETING_PROTOCOL_VERSION_CREATED",
                 "MEETING_PROTOCOL_PUBLISHED", "MEETING_PROTOCOL_ARCHIVED"):
        check("событие аудита " + name, len(events(adm.state, name)) >= 1)
    c, backup = adm.req("GET", "/api/backup", raw=True)
    backup = json.loads(backup)
    rows = backup["tables"]["meetings"]
    check("резервная копия содержит состояние протокола",
          any("protocol" in r for r in rows), list(rows[0].keys())[:6] if rows else [])
    c, j = adm.req("POST", "/api/restore", backup)
    check("копия с состоянием протокола восстанавливается", c == 200 and j.get("ok"), (c, j.get("error")))
    st = adm.state or {}
    check("после восстановления редакции сохранены", len(prot(st, mid).get("versions", [])) == 2,
          prot(st, mid).get("version"))

    print("== Сброс демо-данных ==")
    c, j = adm.rpc("admin.reset")
    check("сброс выполнен", c == 200 and j.get("ok"), j.get("error"))
    check("испытательные совещания удалены при сбросе",
          not any(m["title"].startswith("Испытания маршрута") for m in adm.state["meetings"]))

    print("\nИТОГ: %d ok, %d FAIL" % (ok, fail))
    sys.exit(1 if fail else 0)

if __name__ == "__main__":
    main()
