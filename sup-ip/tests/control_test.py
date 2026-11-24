#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
СУП-ИП. Испытания контроля сроков и отчётности по поручениям (ТЗ M16 §2.11.1–2.11.2).
Запуск при работающем сервере:  python3 tests/control_test.py --base http://127.0.0.1:8000
Проверяется: автоматическое выявление шести категорий отклонений (приближающийся срок, просрочка,
отсутствие исполнителя, отсутствие срока, отсутствие обновлений состояния, необходимость проверки
результата), настраиваемый состав получателей уведомлений (исполнитель, соисполнители, председатель,
секретарь, руководитель подразделения, контролирующее лицо), пороги выявления, рассылка планировщиком
и внеочередным проходом, семь видов отчётов, их выгрузка и сохранение снимком в M13, права и аудит.
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

def ctl(cl, **f):
    return cl.res("directive.control", f) or {}

def ctl_row(cl, tid):
    return next((x for x in ctl(cl).get("rows", []) if x["id"] == tid), {})

def issues(cl, tid):
    return ctl_row(cl, tid).get("issues", [])

def notifs(cl, type_, obj_id=None):
    cl.rpc("notif.list")
    return [n for n in cl.state["notifications"] if n["type"] == type_ and (obj_id is None or n["obj_id"] == obj_id)]

def report(cl, kind, **f):
    f["kind"] = kind
    c, j = cl.rpc("directive.report", f)
    return c, j, (j.get("result") or {}).get("report") or {}

def main():
    global BASE
    ap = argparse.ArgumentParser(); ap.add_argument("--base", default=BASE)
    BASE = ap.parse_args().base.rstrip("/")

    adm = Client(); c, j = adm.login("admin")
    check("вход администратора", c == 200 and j.get("ok"), c)
    c, j = adm.rpc("admin.reset")
    check("подготовка эталонного набора", c == 200 and j.get("ok"), j.get("error"))

    exe = Client(); exe.login("novikov")     # u5 — исполнитель
    co = Client(); co.login("kozlova")       # u6 — соисполнитель и исполнитель второго поручения
    head = Client(); head.login("morozov")   # u2 — руководитель подразделения, председатель
    pm = Client(); pm.login("volkova")       # u3 — секретарь, без права утверждения совещаний
    ctlr = Client(); ctlr.login("lebedev")   # u4 — контролирующее лицо
    obs = Client(); obs.login("zaytseva")    # u8 — наблюдатель

    print("== Настройка контроля по умолчанию (ТЗ §2.11.1) ==")
    r = ctl(adm)
    cfg = r.get("cfg", {})
    check("контроль сроков доступен", "rows" in r and "checks" in r and "cfg" in r)
    check("порог приближающегося срока — 3 дня", cfg.get("dueSoonDays") == 3, cfg.get("dueSoonDays"))
    check("порог отсутствия обновлений — 7 дней", cfg.get("staleDays") == 7, cfg.get("staleDays"))
    checks = {x["key"]: x for x in r.get("checks", [])}
    check("шесть категорий проверок", set(checks) == {"dueSoon", "overdue", "noAssignee", "noDue", "stale", "needReview"},
          sorted(checks))
    check("все проверки включены по умолчанию", all(x["on"] for x in checks.values()))
    recs = {x["key"]: x for x in r.get("recipientKinds", [])}
    check("шесть категорий получателей", set(recs) == {"assignee", "coassignees", "chair", "secretary", "head", "controller"},
          sorted(recs))
    check("все получатели включены по умолчанию", all(x["on"] for x in recs.values()))
    check("наименования проверок на русском", checks.get("overdue", {}).get("name") == "Просрочено", checks.get("overdue"))

    print("== Подготовка поручений для контроля ==")
    c, j = adm.rpc("meeting.create", {"title": "Испытания контроля сроков", "type": "Оперативное", "date": D(0),
                                      "time": "09:00", "chair": "u2", "secretary": "u3", "attendees": ["u5", "u6"]})
    mid = (j.get("result") or {}).get("id")
    adm.rpc("meeting.agenda.add", {"id": mid, "title": "Контроль исполнения", "reporter": "u3"})
    def mk(text, resp, due, **kw):
        c, j = adm.rpc("meeting.decision.add", dict(id=mid, text=text, resp=resp, due=due, agendaN=1,
                                                    createTask=True, **kw))
        d = (j.get("result") or {}).get("decisions", [{}])[-1]
        return (d.get("taskIds") or [None])[0]
    tA = mk("Подготовить сводку по этапу", "u5", D(2), coassignees=["u6"], criteria="Сводка согласована")
    tB = mk("Передать исходные данные лаборатории", "u5", D(-3))
    tC = mk("Уточнить перечень оборудования", "u6", D(20))
    check("поручение с приближающимся сроком создано", bool(tA))
    check("просроченное поручение создано", bool(tB))
    check("поручение без отклонений создано", bool(tC))
    c, j = adm.rpc("meeting.decision.add", {"id": mid, "text": "Решение без поручения (испытание)",
                                            "resp": "u5", "due": D(-1), "agendaN": 1})
    check("решение без поручения зафиксировано", c == 200, j.get("error"))

    print("== Автоматическое выявление отклонений ==")
    check("приближающийся срок выявлен", "dueSoon" in issues(adm, tA), issues(adm, tA))
    check("просрочка выявлена", "overdue" in issues(adm, tB), issues(adm, tB))
    check("срок без отклонений не попадает в контроль", ctl_row(adm, tC) == {}, ctl_row(adm, tC))
    rowB = ctl_row(adm, tB)
    check("строка контроля содержит все поля реестра",
          all(k in rowB for k in ("code", "meetingCode", "assignee", "due", "status", "progress", "issues", "recipients")))
    check("просрочка в днях рассчитана", rowB.get("overdueDays", 0) >= 3, rowB.get("overdueDays"))
    check("получатели уведомления определены", set(rowB.get("recipients", [])) >= {"u5", "u2", "u3"},
          rowB.get("recipients"))
    check("руководитель подразделения включён в получатели", "u2" in rowB.get("recipients", []))
    check("контролирующее лицо по умолчанию — руководитель", "u2" in rowB.get("recipients", []))
    check("давность обновления состояния указана", "idleDays" in rowB and "stateAt" in rowB)
    demo = [t for t in adm.state["tasks"] if t["type"] == "directive" and t["id"] not in (tA, tB, tC)]
    check("поручения без обновления состояния выявлены",
          any("stale" in issues(adm, t["id"]) for t in demo), [issues(adm, t["id"]) for t in demo])
    dstale = next((t["id"] for t in demo if "stale" in issues(adm, t["id"])), None)
    adm.rpc("directive.update", {"id": dstale, "progress": 55})
    check("после обновления состояния отметка снимается", "stale" not in issues(adm, dstale), issues(adm, dstale))
    exe.rpc("task.status", {"id": tA, "to": "В работе"})
    exe.rpc("task.status", {"id": tA, "to": "На проверке", "result": "Сводка подготовлена"})
    check("необходимость проверки результата выявлена", "needReview" in issues(adm, tA), issues(adm, tA))
    adm.rpc("directive.update", {"id": tC, "assignee": None})
    check("поручение без исполнителя выявлено", "noAssignee" in issues(adm, tC), issues(adm, tC))
    adm.rpc("directive.update", {"id": tC, "due": None})
    check("поручение без срока выявлено", "noDue" in issues(adm, tC), issues(adm, tC))
    check("без срока просрочка не начисляется", ctl_row(adm, tC).get("overdueDays") == 0, ctl_row(adm, tC).get("overdueDays"))
    c, j = adm.rpc("gantt.shift", {"id": tC, "delta": 2})
    check("сдвиг поручения без срока отклонён", c == 400, c)
    c, b = adm.req("POST", "/api/export", {"kind": "gantt", "fmt": "docx"}, raw=True)
    check("график работ выгружается при поручении без срока", c == 200 and len(b) > 200, c)
    c, b = adm.req("POST", "/api/export", {"kind": "tasks", "fmt": "csv"}, raw=True)
    check("реестр задач выгружается при поручении без срока", c == 200 and len(b) > 100, c)
    r = ctl(adm, check="overdue")
    check("отбор по категории отклонения", all("overdue" in x["issues"] for x in r["rows"]) and r["total"] >= 1, r["total"])
    r = ctl(adm)
    cnt = {x["key"]: x["count"] for x in r["checks"]}
    check("счётчики категорий соответствуют строкам",
          cnt["overdue"] == sum(1 for x in r["rows"] if "overdue" in x["issues"]) and
          cnt["noDue"] == sum(1 for x in r["rows"] if "noDue" in x["issues"]), cnt)
    r = ctl(adm, meetingId=mid)
    check("отбор по совещанию", all(x["meetingId"] == mid for x in r["rows"]) and r["total"] >= 3, r["total"])
    r = ctl(adm, assignee="u5")
    check("отбор по исполнителю", all(x["assignee"] == "u5" or "u5" in x["coassignees"] for x in r["rows"]))
    c, j = obs.rpc("directive.control")
    check("наблюдатель видит контроль (просмотр)", c == 200 and j.get("ok"), c)

    print("== Настройка порогов, проверок и получателей ==")
    c, j = pm.rpc("directive.controlConfig", {})
    check("чтение настройки доступно секретарю", c == 200 and (j.get("result") or {}).get("dueSoonDays") == 3, c)
    c, j = pm.rpc("directive.controlConfig", {"dueSoonDays": 5})
    check("изменение настройки без права admin/edit запрещено (403)", c == 403, c)
    c, j = head.rpc("directive.controlConfig", {"dueSoonDays": 5})
    check("руководитель подразделения не меняет системную настройку (403)", c == 403, c)
    for v, name in ((0, "нижняя граница"), (31, "верхняя граница")):
        c, j = adm.rpc("directive.controlConfig", {"dueSoonDays": v})
        check("порог приближения срока: " + name + " отклонена", c == 400, c)
    c, j = adm.rpc("directive.controlConfig", {"dueSoonDays": "много"})
    check("порог приближения срока: не число отклонено", c == 400, c)
    for v in (0, 91):
        c, j = adm.rpc("directive.controlConfig", {"staleDays": v})
        check("порог обновлений %s отклонён" % v, c == 400, c)
    c, j = adm.rpc("directive.controlConfig", {"recipients": {"everyone": True}})
    check("неизвестный получатель отклонён", c == 400, c)
    c, j = adm.rpc("directive.controlConfig", {"recipients": {k: False for k in
                       ("assignee", "coassignees", "chair", "secretary", "head", "controller")}})
    check("полное отключение получателей запрещено", c == 400, c)
    c, j = adm.rpc("directive.controlConfig", {"checks": {"unknown": True}})
    check("неизвестная проверка отклонена", c == 400, c)
    c, j = adm.rpc("directive.controlConfig", {"checks": {k: False for k in
                       ("dueSoon", "overdue", "noAssignee", "noDue", "stale", "needReview")}})
    check("полное отключение проверок запрещено", c == 400, c)
    c, j = adm.rpc("directive.controlConfig", {"controller": "u9"})
    check("несуществующее контролирующее лицо отклонено", c == 400, c)
    c, j = adm.rpc("directive.controlConfig", {"dueSoonDays": 5, "staleDays": 10, "controller": "u4"})
    check("настройка сохранена", c == 200 and (j.get("result") or {}).get("dueSoonDays") == 5, j.get("error"))
    check("контролирующее лицо назначено", (j.get("result") or {}).get("controller") == "u4")
    check("порог обновлений сохранён", ctl(adm)["cfg"]["staleDays"] == 10)
    check("контролирующее лицо включено в получатели", "u4" in ctl_row(adm, tB).get("recipients", []),
          ctl_row(adm, tB).get("recipients"))
    c, j = adm.rpc("directive.controlConfig", {"checks": {"dueSoon": False}})
    r = ctl(adm)
    check("отключённая проверка не выявляет отклонения",
          not any("dueSoon" in x["issues"] for x in r["rows"]) and
          next(x for x in r["checks"] if x["key"] == "dueSoon")["count"] == 0)
    adm.rpc("directive.controlConfig", {"checks": {"dueSoon": True}})
    c, j = adm.rpc("directive.controlConfig", {"recipients": {"assignee": False}})
    check("исполнитель исключён из получателей настройкой", "u5" not in ctl_row(adm, tB).get("recipients", []),
          ctl_row(adm, tB).get("recipients"))
    adm.rpc("directive.controlConfig", {"recipients": {"assignee": True}})
    adm.rpc("directive.update", {"id": tC, "controller": "u4"})
    check("контролирующее лицо поручения в карточке", ctl_row(adm, tC).get("controller") == "u4")

    print("== Рассылка уведомлений настроенному кругу получателей ==")
    c, j = exe.rpc("directive.controlNotify")
    check("исполнитель не запускает рассылку (403)", c == 403, c)
    c, j = pm.rpc("directive.controlNotify")
    check("секретарь без права утверждения не запускает рассылку (403)", c == 403, c)
    c, j = adm.rpc("directive.controlNotify")
    res = j.get("result") or {}
    check("внеочередной контроль выполнен", c == 200 and res.get("delivered", 0) > 0, j.get("error"))
    check("рассылка по категориям отражена в результате", set(res.get("byCheck", {})) ==
          {"dueSoon", "overdue", "noAssignee", "noDue", "stale", "needReview"}, res.get("byCheck"))
    check("уведомления о просрочке доставлены", res["byCheck"]["overdue"] > 0, res["byCheck"])
    check("исполнитель уведомлён о просрочке", len(notifs(exe, "directive.overdue", tB)) >= 1)
    check("исполнитель уведомлён о приближающемся сроке", len(notifs(exe, "directive.due", tA)) >= 1)
    check("исполнитель уведомлён о необходимости проверки", len(notifs(exe, "directive.review", tA)) >= 1)
    check("соисполнитель уведомлён", len(notifs(co, "directive.due", tA)) >= 1)
    check("председатель уведомлён о просрочке", len(notifs(head, "directive.overdue", tB)) >= 1)
    check("секретарь уведомлён о просрочке", len(notifs(pm, "directive.overdue", tB)) >= 1)
    check("руководитель подразделения уведомлён об отсутствии исполнителя",
          len(notifs(head, "directive.noassignee", tC)) >= 1)
    check("контролирующее лицо уведомлено об отсутствии срока", len(notifs(ctlr, "directive.nodue", tC)) >= 1)
    before = len(notifs(exe, "directive.overdue", tB))
    adm.rpc("directive.controlNotify"); adm.rpc("directive.controlNotify")
    check("повторные проходы в тот же день не дублируют уведомления",
          len(notifs(exe, "directive.overdue", tB)) == before, before)
    c, j = adm.rpc("notif.tick")
    check("планировщик включает контроль поручений", c == 200 and (j.get("result") or {}).get("delivered", 0) >= 0, c)
    adm.rpc("notif.list")
    evs = [e for e in adm.state["events"] if isinstance(e.get("details"), dict)]
    over_evs = [e for e in evs if e["details"].get("event") == "MEETING_TASK_OVERDUE" and e["objId"] == tB]
    check("событие MEETING_TASK_OVERDUE зарегистрировано", len(over_evs) >= 1)
    check("событие просрочки не дублируется в течение суток", len(over_evs) == 1, len(over_evs))
    check("настройка контроля зафиксирована в журнале",
          any(e["details"].get("event") == "MEETING_DIRECTIVE_CONTROL_CONFIG" for e in evs))
    check("проход контроля зафиксирован в журнале",
          any(e["details"].get("event") == "MEETING_DIRECTIVE_CONTROL_RUN" for e in evs))

    print("== Отчётность по поручениям: семь видов (ТЗ §2.11.2) ==")
    kinds = ["registry", "overdue", "assignees", "meetings", "projects", "period", "unfulfilled"]
    reps = {}
    for k in kinds:
        c, j, rep = report(adm, k)
        reps[k] = rep
        check("отчёт «%s» сформирован" % k, c == 200 and rep.get("columns") and "summary" in rep, j.get("error"))
    check("все семь видов отчётов перечислены",
          len(((adm.rpc("directive.reportKinds")[1].get("result") or {}).get("kinds") or [])) == 7)
    all_codes = {x["code"] for x in (adm.res("directive.list", {}) or {}).get("rows", [])}
    check("реестр всех поручений содержит все поручения M16",
          {r[0] for r in reps["registry"]["rows"]} == all_codes and len(all_codes) >= 5,
          (len(reps["registry"]["rows"]), len(all_codes)))
    check("реестр: 13 колонок по §2.11", len(reps["registry"]["columns"]) == 13, len(reps["registry"]["columns"]))
    check("реестр: сводка по состояниям", any(s[0] == "Всего поручений" for s in reps["registry"]["summary"]))
    ov = reps["overdue"]["rows"]
    check("перечень просроченных: только просроченные", ov and all(int(r[5]) > 0 for r in ov), ov)
    check("перечень просроченных: сортировка по убыванию просрочки",
          [int(r[5]) for r in ov] == sorted([int(r[5]) for r in ov], reverse=True))
    check("перечень просроченных: сводка максимальной просрочки",
          any(s[0] == "Максимальная просрочка, дн" for s in reps["overdue"]["summary"]))
    check("отчёт по исполнителям: группировка", len(reps["assignees"]["rows"]) >= 2)
    check("отчёт по исполнителям: строка без исполнителя",
          any("без исполнителя" in r[0] for r in reps["assignees"]["rows"]), reps["assignees"]["rows"])
    check("отчёт по исполнителям: дисциплина исполнения",
          any(s[0] == "Исполнительская дисциплина, %" for s in reps["assignees"]["summary"]))
    mrow = next((r for r in reps["meetings"]["rows"] if r[0].startswith(
        next(m["code"] for m in adm.state["meetings"] if m["id"] == mid))), None)
    check("отчёт по совещаниям: строка испытательного совещания", bool(mrow), reps["meetings"]["rows"])
    check("отчёт по совещаниям: решения без поручений учтены", mrow and int(mrow[3]) >= 4, mrow)
    check("отчёт по проектам: трудозатраты по поручениям",
          any(s[0] == "Трудозатраты по поручениям, ч" for s in reps["projects"]["summary"]))
    check("отчёт по проектам: процент исполнения", len(reps["projects"]["columns"]) == 8)
    ps = dict(reps["period"]["summary"])
    check("отчёт за период: период по умолчанию 30 дней", "Период" in ps, ps)
    check("отчёт за период: поставлено и исполнено", "Поставлено поручений" in ps and "Исполнено (дата факта)" in ps)
    check("отчёт за период: дисциплина и продления", "Дисциплина исполнения, %" in ps and "Продлений срока за период" in ps)
    c, j, rep = report(adm, "period", **{"from": D(0), "to": D(-10)})
    check("период с началом позже окончания отклонён", c == 400, c)
    c, j, rep = report(adm, "period", **{"from": "2026-13-45"})
    check("некорректная дата периода отклонена", c == 400, c)
    unf = reps["unfulfilled"]["rows"]
    check("отчёт по неисполненным решениям: решение без поручения",
          any("не создано" in r[6] for r in unf), unf)
    check("отчёт по неисполненным решениям: незавершённые поручения",
          any(r[6] != "— не создано —" for r in unf))
    check("отчёт по неисполненным решениям: сводка без поручений",
          any(s[0] == "Из них без поручения" and s[1] >= 1 for s in reps["unfulfilled"]["summary"]))
    c, j, rep = report(adm, "unknown")
    check("неизвестный вид отчёта отклонён (404)", c == 404, c)
    c, j, rep = report(adm, "registry", meetingId=mid)
    check("отчёт с отбором по совещанию", rep["total"] >= 3 and rep["params"].get("meetingId") == mid, rep["total"])
    c, j, rep = report(adm, "assignees", assignee="u5")
    check("отчёт с отбором по исполнителю", all("Новиков" in r[0] or "Козлова" in r[0] for r in rep["rows"]), rep["rows"])
    c, j = obs.rpc("directive.report", {"kind": "registry"})
    check("наблюдатель формирует отчёт (просмотр)", c == 200, c)
    c, j = obs.rpc("directive.report", {"kind": "registry", "save": True})
    check("наблюдатель не сохраняет снимок в M13 (403)", c == 403, c)
    nrep = len(adm.state["reports"])
    c, j = adm.rpc("directive.report", {"kind": "overdue", "save": True})
    saved = (j.get("result") or {}).get("saved") or {}
    check("снимок отчёта сохранён в M13", c == 200 and saved.get("code", "").startswith("ОТ-"), j.get("error"))
    check("снимок появился в реестре отчётов", len(adm.state["reports"]) == nrep + 1, (nrep, len(adm.state["reports"])))
    check("снимок содержит таблицу отчёта", "Перечень просроченных" in saved.get("html", ""))
    adm.rpc("notif.list")
    check("событие MEETING_DIRECTIVE_REPORT зарегистрировано",
          any(isinstance(e.get("details"), dict) and e["details"].get("event") == "MEETING_DIRECTIVE_REPORT"
              for e in adm.state["events"]))

    print("== Выгрузка отчётов по поручениям (ТЗ §25) ==")
    for fmt in ("docx", "xlsx", "pdf", "csv", "html"):
        c, b = adm.req("POST", "/api/export", {"kind": "directives", "fmt": fmt, "params": {"kind": "overdue"}}, raw=True)
        check("выгрузка отчёта в %s" % fmt.upper(), c == 200 and isinstance(b, bytes) and len(b) > 200, (c, len(b) if isinstance(b, bytes) else b))
    c, b = adm.req("POST", "/api/export", {"kind": "directives", "fmt": "csv", "params": {"kind": "registry"}}, raw=True)
    check("CSV содержит номера поручений", "З-0".encode("utf-8") in b, b[:80])
    c, b = obs.req("POST", "/api/export", {"kind": "directives", "fmt": "docx", "params": {"kind": "registry"}}, raw=True)
    check("наблюдателю выгрузка запрещена (403)", c == 403, c)
    c, b = adm.req("POST", "/api/export", {"kind": "directives", "fmt": "docx", "params": {"kind": "nope"}}, raw=True)
    check("выгрузка неизвестного отчёта отклонена", c == 404, c)

    print("== Приёмка результата исключает поручение из контроля ==")
    adm.rpc("directive.update", {"id": tC, "assignee": "u6", "due": D(10)})
    check("исполнитель и срок восстановлены", ctl_row(adm, tC).get("issues", []) == [] or
          "noAssignee" not in issues(adm, tC), issues(adm, tC))
    c, j = adm.rpc("directive.update", {"id": tC, "due": D(15)})
    check("изменение установленного срока требует продления", c == 400 and "продлен" in (j.get("error") or {}).get("message", ""), c)
    c, j = adm.rpc("directive.extend", {"id": tC, "due": D(15), "reason": "Согласовано с лабораторией"})
    check("продление срока с основанием выполнено", c == 200, j.get("error"))
    co.rpc("task.status", {"id": tC, "to": "В работе"})
    co.rpc("task.status", {"id": tC, "to": "На проверке", "result": "Перечень оборудования уточнён"})
    c, j = head.rpc("directive.accept", {"id": tC, "verdict": "Принято", "comment": "Перечень принят"})
    check("результат принят руководителем", c == 200, j.get("error"))
    check("принятое поручение исключено из контроля", ctl_row(adm, tC) == {}, ctl_row(adm, tC))
    c, j, rep = report(adm, "unfulfilled")
    check("принятое поручение исключено из неисполненных решений",
          not any(tC and r[6].startswith(next(t["code"] for t in adm.state["tasks"] if t["id"] == tC)) for r in rep["rows"]),
          rep["rows"])
    adm.rpc("notif.list")
    check("событие MEETING_TASK_COMPLETED зарегистрировано",
          any(isinstance(e.get("details"), dict) and "MEETING_TASK_COMPLETED" in (e["details"].get("events") or [])
              for e in adm.state["events"]))

    print("== Сохранение настройки в резервной копии ==")
    c, backup = adm.req("GET", "/api/backup", raw=True)
    backup = json.loads(backup)
    dcfg = (backup["tables"]["settings"][0].get("data") or {}).get("dirControl") or {}
    check("настройка контроля в резервной копии", dcfg.get("dueSoonDays") == 5 and dcfg.get("controller") == "u4", dcfg)
    adm.rpc("directive.controlConfig", {"dueSoonDays": 7})
    c, j = adm.req("POST", "/api/restore", backup)
    check("копия восстановлена", c == 200 and j.get("ok"), (c, j.get("error")))
    check("настройка контроля восстановлена", ctl(adm)["cfg"]["dueSoonDays"] == 5, ctl(adm)["cfg"])

    print("== Сброс демо-данных ==")
    c, j = adm.rpc("admin.reset")
    check("сброс выполнен", c == 200 and j.get("ok"), j.get("error"))
    check("испытательное совещание удалено",
          not any(m["title"] == "Испытания контроля сроков" for m in adm.state["meetings"]))
    check("настройка контроля возвращена к значениям по умолчанию", ctl(adm)["cfg"]["dueSoonDays"] == 3)

    print("\nИТОГ: %d ok, %d FAIL" % (ok, fail))
    sys.exit(1 if fail else 0)

if __name__ == "__main__":
    main()
