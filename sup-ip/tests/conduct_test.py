#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
СУП-ИП. Испытания рабочей формы проведения совещания (ТТ-2, M16, экранные требования MTG-08 §11).
Запуск при работающем сервере:  python3 tests/conduct_test.py --base http://127.0.0.1:8000

Проверяется: компоновка §11.2 (вопросы и статусы слева, текущий вопрос с докладчиком, описанием,
материалами и полем обсуждения в центре, решения/поручения/ответственные/сроки справа),
восемь полей фиксации обсуждения §11.3, черновик по каждому вопросу (устойчивость к обрыву связи),
управление ходом §11.4 («Начать», «Следующий вопрос», «Предыдущий вопрос», «Отметить рассмотренным»,
«Создать решение», «Создать поручение», «Завершить»), время начала и завершения рассмотрения,
подтверждение завершения при нерассмотренных вопросах и черновиках, права ролей, аудит,
содержание протокола, резервная копия.
Только стандартная библиотека Python.
"""
import argparse, datetime, http.cookiejar, importlib.util, io, json, os, sys, time, urllib.error, urllib.request

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
        e = (j.get("error") or {}) if isinstance(j, dict) else {}
        return c, e.get("message", ""), e.get("code", "")

    def state(self):
        c, j = self.req("GET", "/api/state")
        return j.get("state") or j.get("result") or j


def meet(cli, **kw):
    p = {"title": "Проведение MTG-08", "type": "Оперативное", "date": D(0), "time": "10:00",
         "chair": "u2", "secretary": "u3", "attendees": ["u2", "u3", "u5", "u6"]}
    p.update(kw)
    return cli.res("meeting.create", p)


def run(cli, mid):
    r, c, j = cli.res("meeting.run.state", {"id": mid})
    return r or {}


def item(cli, mid, n):
    return next((x for x in run(cli, mid).get("items", []) if x["n"] == n), None)


def events(cli, code):
    return [e for e in cli.state()["events"] if code in ((e.get("details") or {}).get("events") or [])
            or (e.get("details") or {}).get("event") == code]


def main():
    global BASE
    ap = argparse.ArgumentParser(); ap.add_argument("--base", default=BASE)
    BASE = ap.parse_args().base.rstrip("/")
    spec = importlib.util.spec_from_file_location("supip_conduct_test", SERVER)
    srv = importlib.util.module_from_spec(spec); spec.loader.exec_module(srv)

    adm = Client()
    c, j = adm.login("admin")
    check("вход администратора", c == 200 and j.get("ok"), c)
    adm.rpc("admin.reset")
    st = adm.state()
    did = st["docs"][0]["id"]

    r, c, j = meet(adm)
    mid = (r or {}).get("id")
    check("совещание создано", c == 200 and mid, (c, j))
    for t, rep, dur in (("Ход работ по СИ-3", "u5", 20), ("Приёмка расчёта РЛ-240", "u6", 15),
                        ("План на неделю", "u3", 10)):
        adm.rpc("meeting.agenda.add", {"id": mid, "title": t, "reporter": rep, "durationMin": dur,
                                       "content": "Содержание: " + t, "expected": "Решение по вопросу"})
    ag = (adm.res("meeting.agenda.list", {"id": mid})[0] or {}).get("items") or []
    check("повестка из трёх вопросов", len(ag) == 3, len(ag))
    a1, a2, a3 = ag[0]["id"], ag[1]["id"], ag[2]["id"]
    adm.rpc("meeting.materials.add", {"id": mid, "docId": did, "type": "Доклад", "itemId": a1})

    print("== A. Компоновка рабочей формы (§11.2) ==")
    R = run(adm, mid)
    check("форма проведения получена", R.get("meetingId") == mid, R.get("meetingId"))
    check("состояние хода «Не начато»", (R.get("run") or {}).get("status") == "Не начато", R.get("run"))
    check("список вопросов слева", len(R.get("items") or []) == 3)
    it = (R.get("items") or [])[0]
    check("порядковый номер вопроса", it.get("n") == 1)
    check("статус рассмотрения вопроса", it.get("status") == "Включён в повестку", it.get("status"))
    check("докладчик вопроса", it.get("reporter") == "u5" and "Новиков" in (it.get("reporterName") or ""))
    check("описание вопроса", "Содержание" in (it.get("content") or ""))
    check("ожидаемый результат", it.get("expected") == "Решение по вопросу")
    check("плановая продолжительность", it.get("durationMin") == 20)
    check("материалы вопроса в центре", len(it.get("materials") or []) == 1, it.get("materials"))
    check("решения вопроса справа", it.get("decisions") == [])
    check("поручения вопроса справа", it.get("tasks") == [])
    check("восемь полей фиксации §11.3", len(R.get("fields") or []) == 8, R.get("fields"))
    keys = [f["key"] for f in R.get("fields") or []]
    for k in ("summary", "positions", "remarks", "proposals", "dissent", "outcome"):
        check("поле §11.3 «%s»" % k, k in keys)
    check("сводка хода", (R.get("stats") or {}).get("total") == 3 and (R.get("stats") or {}).get("open") == 3, R.get("stats"))
    check("плановое время повестки", (R.get("stats") or {}).get("plannedMin") == 45, R.get("stats"))
    check("форма доступна для редактирования", R.get("editable") is True)

    print("== B. Управление ходом: «Начать» (§11.4) ==")
    c, msg, code = adm.err("meeting.run.goto", {"id": mid, "dir": "next"})
    check("переход до начала отклонён", c == 400 and "не начато" in msg, (c, msg))
    c, msg, code = adm.err("meeting.run.finish", {"id": mid})
    check("завершение до начала отклонено", c == 400, (c, msg))
    r, c, j = adm.res("meeting.run.start", {"id": mid})
    check("совещание начато", c == 200, (c, j))
    R = run(adm, mid)
    check("состояние хода «Идёт»", R["run"]["status"] == "Идёт", R["run"]["status"])
    check("время начала зафиксировано", isinstance(R["run"]["startedAt"], int) and R["run"]["startedAt"] > 0)
    check("инициатор начала зафиксирован", R["run"]["startedBy"] == "u1")
    check("текущий вопрос — первый", (R.get("current") or {}).get("n") == 1, R.get("current"))
    it1 = item(adm, mid, 1)
    check("первый вопрос «Рассматривается»", it1["status"] == "Рассматривается", it1["status"])
    check("время начала рассмотрения вопроса", isinstance(it1["startedAt"], int) and it1["startedAt"] > 0)
    check("журнал хода содержит начало", any(x["action"] == "start" for x in R["run"]["log"]))
    c, msg, code = adm.err("meeting.run.start", {"id": mid})
    check("повторный запуск отклонён", c == 400 and "уже проводится" in msg, (c, msg))
    c, msg, code = adm.err("meeting.reschedule", {"id": mid, "date": D(3), "time": "11:00", "reason": "Проверка"})
    check("перенос во время проведения запрещён", c == 400 and "проводится" in msg, (c, msg))
    c, msg, code = adm.err("meeting.cancel", {"id": mid, "reason": "Проверка отмены"})
    check("отмена во время проведения запрещена", c == 400 and "проводится" in msg, (c, msg))

    print("== C. Черновик рассмотрения по вопросу (§11.3) ==")
    r, c, j = adm.res("meeting.run.draft", {"id": mid, "itemId": a1,
                                            "fields": {"summary": "Доклад о ходе работ по СИ-3",
                                                       "positions": "Работы идут по графику"}})
    check("черновик сохранён", c == 200 and set((r or {}).get("saved") or []) == {"summary", "positions"}, (c, j))
    it1 = item(adm, mid, 1)
    check("черновик хранится на сервере", it1["draft"]["fields"].get("summary") == "Доклад о ходе работ по СИ-3")
    check("время черновика зафиксировано", isinstance(it1["draft"]["ts"], int))
    check("автор черновика зафиксирован", it1["draft"]["by"] == "u1")
    check("поля вопроса ещё не изменены", it1["fields"]["summary"] == "", it1["fields"])
    r, c, j = adm.res("meeting.run.draft", {"id": mid, "itemId": a1, "fields": {"summary": "Доклад о ходе работ по СИ-3"}})
    check("повторное автосохранение без изменений — пустой список", (r or {}).get("saved") == [], r)
    c, msg, code = adm.err("meeting.run.draft", {"id": mid, "itemId": a1})
    check("черновик без полей отклонён", c == 400, (c, msg))
    c, msg, code = adm.err("meeting.run.draft", {"id": mid, "itemId": a1, "fields": {"summary": "x" * 4001}})
    check("превышение длины поля отклонено", c == 400 and "4000" in msg, (c, msg))
    c, msg, code = adm.err("meeting.run.draft", {"id": mid, "itemId": "aq_none", "fields": {"summary": "x"}})
    check("черновик несуществующего вопроса — 404", c == 404, (c, msg))
    # имитация обрыва связи: новый вход, черновик должен сохраниться
    adm2 = Client(); adm2.login("admin")
    it1b = item(adm2, mid, 1)
    check("черновик сохранён после повторного входа", it1b["draft"]["fields"].get("positions") == "Работы идут по графику")
    check("черновики считаются в сводке", run(adm2, mid)["stats"]["drafts"] == 1, run(adm2, mid)["stats"])

    print("== D. Фиксация рассмотрения и «Отметить рассмотренным» (§11.4) ==")
    r, c, j = adm.res("meeting.run.commit", {"id": mid, "itemId": a1,
                                             "fields": {"remarks": "Замечание по срокам",
                                                        "proposals": "Ускорить поставку",
                                                        "dissent": "Разногласий нет",
                                                        "problems": "Риск задержки поставки",
                                                        "outcome": "Принять к исполнению"}})
    check("рассмотрение зафиксировано", c == 200, (c, j))
    it1 = item(adm, mid, 1)
    check("краткое содержание доклада в полях", it1["fields"]["summary"] == "Доклад о ходе работ по СИ-3")
    check("основные позиции в полях", it1["fields"]["positions"] == "Работы идут по графику")
    check("замечания в полях", it1["fields"]["remarks"] == "Замечание по срокам")
    check("предложения в полях", it1["fields"]["proposals"] == "Ускорить поставку")
    check("разногласия в полях", it1["fields"]["dissent"] == "Разногласий нет")
    check("выявленные проблемы в полях", it1["fields"]["problems"] == "Риск задержки поставки")
    check("итог рассмотрения в полях", it1["fields"]["outcome"] == "Принять к исполнению")
    check("черновик очищен после фиксации", it1["draft"]["fields"] == {}, it1["draft"])
    c, msg, code = adm.err("meeting.run.commit", {"id": mid, "itemId": a1})
    check("повторная фиксация без изменений отклонена", c == 400, (c, msg))
    time.sleep(1.1)
    r, c, j = adm.res("meeting.run.item.done", {"id": mid, "itemId": a1})
    check("вопрос отмечен рассмотренным", c == 200, (c, j))
    it1 = item(adm, mid, 1)
    check("статус «Рассмотрен»", it1["status"] == "Рассмотрен", it1["status"])
    check("время завершения зафиксировано", isinstance(it1["finishedAt"], int))
    check("длительность рассмотрения рассчитана", it1["sec"] >= 1, it1["sec"])
    check("фактический результат заполнен итогом", it1["fact"] == "Принять к исполнению", it1["fact"])
    check("сводка: рассмотрен 1", run(adm, mid)["stats"]["done"] == 1)
    check("сводка: открыто 2", run(adm, mid)["stats"]["open"] == 2)
    check("сводка: время рассмотрения", run(adm, mid)["stats"]["sec"] >= 1)
    c, msg, code = adm.err("meeting.run.item.done", {"id": mid, "itemId": a3})
    check("отметка без рассмотрения и решений отклонена", c == 400 and "итог рассмотрения" in msg, (c, msg))

    print("== E. Навигация «Следующий» / «Предыдущий вопрос» (§11.4) ==")
    r, c, j = adm.res("meeting.run.goto", {"id": mid, "dir": "next"})
    check("переход к следующему вопросу", c == 200 and (r or {}).get("current", {}).get("n") == 2, (c, j))
    it2 = item(adm, mid, 2)
    check("вопрос 2 «Рассматривается»", it2["status"] == "Рассматривается")
    check("время начала вопроса 2", isinstance(it2["startedAt"], int))
    r, c, j = adm.res("meeting.run.goto", {"id": mid, "dir": "next"})
    check("переход к третьему вопросу", (r or {}).get("current", {}).get("n") == 3)
    c, msg, code = adm.err("meeting.run.goto", {"id": mid, "dir": "next"})
    check("за последним вопросом переход отклонён", c == 400 and "последний" in msg, (c, msg))
    r, c, j = adm.res("meeting.run.goto", {"id": mid, "dir": "prev"})
    check("возврат к предыдущему открытому вопросу", (r or {}).get("current", {}).get("n") == 2)
    r, c, j = adm.res("meeting.run.goto", {"id": mid, "itemId": a3})
    check("выбор вопроса в списке слева", (r or {}).get("current", {}).get("n") == 3)
    c, msg, code = adm.err("meeting.run.goto", {"id": mid, "itemId": a1})
    check("выбор рассмотренного вопроса отклонён", c == 400 and "Рассмотрен" in msg, (c, msg))
    check("журнал хода содержит переходы", len([x for x in run(adm, mid)["run"]["log"] if x["action"] == "goto"]) >= 3)

    print("== F. Решения и поручения из рабочей формы (§11.2, §11.4) ==")
    r, c, j = adm.res("meeting.decision.add", {"id": mid, "text": "Ускорить поставку по СИ-3", "resp": "u5",
                                               "due": D(7), "agendaN": 3, "createTask": True,
                                               "title": "Поставка СИ-3", "assignee": "u5"})
    check("решение по текущему вопросу создано", c == 200, (c, j))
    it3 = item(adm, mid, 3)
    check("решение отображается справа", len(it3["decisions"]) == 1, it3["decisions"])
    check("ответственный решения", it3["decisions"][0]["resp"] == "u5")
    check("срок решения", it3["decisions"][0]["due"] == D(7))
    check("поручение отображается справа", len(it3["tasks"]) == 1, it3["tasks"])
    check("исполнитель поручения", it3["tasks"][0]["assignee"] == "u5")
    check("срок поручения", it3["tasks"][0]["due"] == D(7))
    check("сводка: решений 1", run(adm, mid)["stats"]["decisions"] == 1)
    check("сводка: поручений 1", run(adm, mid)["stats"]["tasks"] == 1)
    r, c, j = adm.res("meeting.run.item.done", {"id": mid, "itemId": a3})
    check("вопрос с решением отмечается рассмотренным без текста", c == 200, (c, j))
    check("фактический результат по решениям", item(adm, mid, 3)["fact"].startswith("Рассмотрен"), item(adm, mid, 3)["fact"])

    print("== G. Завершение совещания (§11.4) ==")
    adm.rpc("meeting.run.draft", {"id": mid, "itemId": a2, "fields": {"summary": "Черновик по вопросу 2"}})
    c, msg, code = adm.err("meeting.run.finish", {"id": mid})
    check("завершение с черновиком требует подтверждения", c == 409 and code == "NEED_CONFIRM", (c, code))
    check("в сообщении перечислены вопросы с черновиками", "черновик" in msg.lower() and "2" in msg, msg)
    check("в сообщении перечислены нерассмотренные вопросы", "Не рассмотрены" in msg, msg)
    r, c, j = adm.res("meeting.run.finish", {"id": mid, "confirm": True, "commitDrafts": True})
    check("совещание завершено с подтверждением", c == 200, (c, j))
    R = run(adm, mid)
    check("состояние хода «Завершено»", R["run"]["status"] == "Завершено")
    check("время завершения совещания", isinstance(R["run"]["finishedAt"], int))
    check("инициатор завершения", R["run"]["finishedBy"] == "u1")
    m = next(x for x in adm.state()["meetings"] if x["id"] == mid)
    check("статус совещания «Проведено»", m["status"] == "Проведено", m["status"])
    check("протокол переведён в «Черновик»", m["protocol"]["status"] == "Черновик", m["protocol"]["status"])
    check("черновик вопроса 2 зафиксирован при завершении",
          next(a for a in m["agenda"] if a["n"] == 2).get("summary") == "Черновик по вопросу 2")
    check("форма больше не редактируется", R.get("editable") is False)
    c, msg, code = adm.err("meeting.run.draft", {"id": mid, "itemId": a2, "fields": {"summary": "x"}})
    check("черновик после завершения отклонён", c == 400 and "уже проведено" in msg, (c, msg))
    c, msg, code = adm.err("meeting.run.start", {"id": mid})
    check("повторный запуск проведённого совещания отклонён", c == 400, (c, msg))

    print("== H. Ограничения по дате и пустой повестке ==")
    r, c, j = meet(adm, title="Будущее совещание", date=D(5))
    mid_f = (r or {}).get("id")
    adm.rpc("meeting.agenda.add", {"id": mid_f, "title": "Вопрос", "reporter": "u5"})
    c, msg, code = adm.err("meeting.run.start", {"id": mid_f})
    check("начало раньше даты совещания отклонено", c == 400 and "раньше его даты" in msg, (c, msg))
    r, c, j = meet(adm, title="Без повестки")
    mid_e = (r or {}).get("id")
    c, msg, code = adm.err("meeting.run.start", {"id": mid_e})
    check("начало с пустой повесткой отклонено", c == 400 and "Повестка пуста" in msg, (c, msg))
    r, c, j = meet(adm, title="Отменённое совещание")
    mid_c = (r or {}).get("id")
    adm.rpc("meeting.agenda.add", {"id": mid_c, "title": "Вопрос", "reporter": "u5"})
    adm.rpc("meeting.cancel", {"id": mid_c, "reason": "Отпуск председателя"})
    c, msg, code = adm.err("meeting.run.start", {"id": mid_c})
    check("начало отменённого совещания отклонено", c == 400, (c, msg))

    print("== I. Права ролей ==")
    r, c, j = meet(adm, title="Права MTG-08")
    mid_p = (r or {}).get("id")
    adm.rpc("meeting.agenda.add", {"id": mid_p, "title": "Вопрос прав", "reporter": "u5"})
    for login, role in (("volkova", "руководитель проектов"), ("novikov", "исполнитель"), ("zaytseva", "наблюдатель")):
        cli = Client(); cli.login(login)
        c, j = cli.rpc("meeting.run.state", {"id": mid_p})
        check("%s: просмотр формы разрешён" % role, c == 200, c)
    exe = Client(); exe.login("novikov")
    for act, payload in (("meeting.run.start", {"id": mid_p}),
                         ("meeting.run.goto", {"id": mid_p, "dir": "next"}),
                         ("meeting.run.draft", {"id": mid_p, "n": 1, "fields": {"summary": "x"}}),
                         ("meeting.run.commit", {"id": mid_p, "n": 1}),
                         ("meeting.run.item.done", {"id": mid_p, "n": 1}),
                         ("meeting.run.finish", {"id": mid_p})):
        c, j = exe.rpc(act, payload)
        check("исполнитель: отказ в %s" % act.split(".")[-1], c == 403, (c, j))
    obs = Client(); obs.login("zaytseva")
    c, j = obs.rpc("meeting.run.start", {"id": mid_p})
    check("наблюдатель: отказ в начале проведения", c == 403, c)
    pm = Client(); pm.login("volkova")
    c, j = pm.rpc("meeting.run.start", {"id": mid_p})
    check("руководитель проектов: проведение начато", c == 200, (c, j))
    c, j = pm.rpc("meeting.run.draft", {"id": mid_p, "n": 1, "fields": {"summary": "Доклад РП"}})
    check("руководитель проектов: черновик сохранён", c == 200, (c, j))

    print("== K. Аудит и события §2.13 ==")
    for ev in ("MEETING_RUN_STARTED", "MEETING_RUN_ITEM_STARTED", "MEETING_AGENDA_DISCUSSION",
               "MEETING_RUN_ITEM_DONE", "MEETING_COMPLETED"):
        check("событие %s зарегистрировано" % ev, len(events(adm, ev)) >= 1, len(events(adm, ev)))
    check("отказ прав записан в журнал", any(e.get("cat") == "SECURITY" and "Отказано" in (e.get("action") or "")
                                            for e in adm.state()["events"]))

    print("== L. Протокол содержит рассмотрение (§2.8) ==")
    r, c, j = adm.res("meeting.protocol.preview", {"id": mid, "html": True})
    html = (r or {}).get("html") or ""
    check("предпросмотр протокола получен", c == 200 and html, (c, j))
    for probe in ("Краткое содержание доклада", "Основные позиции", "Предложения", "Разногласия",
                  "Итог рассмотрения", "Время рассмотрения"):
        check("в протоколе есть «%s»" % probe, probe in html, probe)
    r, c, j = adm.res("meeting.protocol", {"id": mid, "formats": ["docx"]})
    check("протокол выпущен после проведения", c == 200 and (r or {}).get("docId"), (c, j))

    print("== M. Резервная копия ==")
    c, raw = adm.req("GET", "/api/backup")
    bk = json.loads(raw if isinstance(raw, (bytes, str)) else json.dumps(raw))
    row = [x for x in (bk.get("tables") or {}).get("meetings", []) if x["id"] == mid]
    check("копия содержит ход проведения", c == 200 and row and isinstance(row[0].get("run"), dict), c)
    check("в копии сохранено время начала и завершения",
          row and row[0]["run"].get("startedAt") and row[0]["run"].get("finishedAt"))
    check("в копии сохранены поля рассмотрения",
          row and any(a.get("outcome") for a in row[0]["agenda"]))
    c, j = adm.req("POST", "/api/restore", bk)
    check("восстановление из копии выполнено", c == 200, (c, j))
    adm.login("admin")
    R = run(adm, mid)
    check("после восстановления ход сохранён", R["run"]["status"] == "Завершено", R["run"])
    check("после восстановления время рассмотрения сохранено", item(adm, mid, 1)["sec"] >= 1)

    adm.rpc("admin.reset")
    print("\nИТОГ: %d ok, %d FAIL" % (ok, fail))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
