# -*- coding: utf-8 -*-
"""Интеграционные тесты API СУП-ИП (запускать при работающем сервере).
   python3 tests/api_test.py [--base http://127.0.0.1:8000]
   ВНИМАНИЕ: тесты создают/меняют данные и в конце восстанавливают эталонный набор (admin.reset)."""
import argparse, json, sys, urllib.request, urllib.error

BASE = "http://127.0.0.1:8000"
PASS, FAIL = 0, 0

def check(name, cond, extra=""):
    global PASS, FAIL
    if cond: PASS += 1; print("  ok  %s" % name)
    else: FAIL += 1; print("FAIL  %s  %s" % (name, extra))

class Client:
    def __init__(self): self.cookie = None
    def req(self, method, path, body=None, raw=False, ctype="application/json"):
        data = None
        if body is not None:
            data = body if raw else json.dumps(body, ensure_ascii=False).encode()
        r = urllib.request.Request(BASE + path, data=data, method=method)
        if data is not None: r.add_header("Content-Type", ctype)
        if self.cookie: r.add_header("Cookie", self.cookie)
        try:
            resp = urllib.request.urlopen(r, timeout=30)
            status, headers, payload = resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as e:
            status, headers, payload = e.code, e.headers, e.read()
        sc = headers.get("Set-Cookie")
        if sc and "supip_sid=" in sc:
            v = sc.split("supip_sid=")[1].split(";")[0]
            self.cookie = "supip_sid=" + v if v else None
        if "application/json" in (headers.get("Content-Type") or ""):
            return status, json.loads(payload.decode())
        return status, payload
    def login(self, login, pw="demo"):
        return self.req("POST", "/api/login", {"login": login, "password": pw})
    def rpc(self, action, **payload):
        return self.req("POST", "/api/rpc", {"action": action, "payload": payload})

def main():
    global BASE
    ap = argparse.ArgumentParser(); ap.add_argument("--base", default=BASE)
    BASE = ap.parse_args().base.rstrip("/")

    print("== Здоровье и анонимный доступ ==")
    anon = Client()
    s, j = anon.req("GET", "/api/health")
    check("health 200 + PostgreSQL", s == 200 and j["db"] == "PostgreSQL", j)
    s, j = anon.req("GET", "/api/bootstrap")
    check("bootstrap анонимный", s == 200 and j["authed"] is False)
    s, j = anon.req("GET", "/api/state")
    check("state без сессии → 401", s == 401)
    s, j = anon.rpc("project.create", name="x")
    check("rpc без сессии → 401", s == 401)

    print("== Вход и состояние ==")
    s, j = anon.login("admin", "wrong")
    check("неверный пароль → 401", s == 401 and j["error"]["code"] == "BAD_CREDENTIALS")
    adm = Client()
    s, j = adm.login("admin")
    check("вход администратора", s == 200 and j["ok"] and j["state"]["me"] == "u1", j.get("error"))
    st = j["state"]
    check("state: 8 пользователей, 6 проектов, 28 задач",
          len(st["users"]) == 8 and len(st["projects"]) == 6 and len(st["tasks"]) == 28,
          (len(st["users"]), len(st["projects"]), len(st["tasks"])))
    check("state: rev/settings/PostgreSQL", st["rev"] >= 1 and st["settings"]["wip"] == 8 and st["server"]["db"] == "PostgreSQL")
    s, j = adm.req("GET", "/api/state?rev=%d" % st["rev"])
    check("поллинг same=true", s == 200 and j.get("same") is True)

    print("== Права: наблюдатель ==")
    obs = Client(); obs.login("zaytseva")
    s, j = obs.rpc("project.create", name="Нельзя", cat="НИОКР", pm="u3", start="2026-12-01", end="2026-12-20", budgetHours=100)
    check("observer project.create → 403", s == 403 and j["error"]["code"] == "FORBIDDEN")
    s, j = adm.req("GET", "/api/state")
    sec = [e for e in j["state"]["events"] if e["cat"] == "SECURITY" and "Отказано" in e["action"]]
    check("отказ записан в SECURITY-журнал", len(sec) >= 1)

    print("== Проекты и задачи (РП volkova/u3) ==")
    pm = Client(); pm.login("volkova")
    s, j = pm.rpc("project.create", name="Тестовый проект API", cat="НИОКР", pm="u3",
                  start="2026-12-01", end="2027-02-01", budgetHours=400, team=["u5", "u6"])
    check("project.create", s == 200 and j["ok"], j.get("error"))
    pid = j["result"]["id"]; pcode = j["result"]["code"]
    check("код проекта П-YYYY-NN", pcode.startswith("П-2026-"), pcode)
    check("team нормализован (РП + исполнители)", j["result"]["team"] == ["u3", "u5", "u6"], j["result"]["team"])
    s, j = pm.rpc("project.create", name="Плохие даты", cat="НИОКР", pm="u3", start="2027-02-01", end="2026-12-01", budgetHours=10)
    check("end<start отклонён", s == 400)
    s, j = pm.rpc("task.create", projectId=pid, title="Задача A", assignee="u5", start="2026-12-01", end="2026-12-10",
                  planHours=24, priority="Высокий", initialStatus="Готова к выполнению")
    check("task.create", s == 200 and j["ok"], j.get("error"))
    ta = j["result"]["id"]
    s, j = pm.rpc("task.create", projectId=pid, title="Задача B", assignee="u6", start="2026-12-05", end="2026-12-15",
                  planHours=16, priority="Средний", initialStatus="Готова к выполнению", deps=[{"id": ta, "type": "FS", "lag": 0}])
    tb = j["result"]["id"]
    check("task.create с зависимостью FS", s == 200 and j["ok"])
    s, j = pm.rpc("task.create", projectId=pid, title="Веха", type="milestone", assignee="u3",
                  start="2026-12-20", end="2026-12-21", planHours=8, priority="Средний")
    check("веха: end нормализован к start, часы 0", s == 200 and j["result"]["end"] == "2026-12-20" and j["result"]["planHours"] == 0)
    s, j = pm.rpc("task.status", id=ta, to="В работе")
    check("переход В работе", s == 200 and j["result"]["status"] == "В работе")
    s, j = pm.rpc("task.status", id=ta, to="На проверке")
    check("На проверке без результата → NEED_RESULT", s == 400 and j["error"]["code"] == "NEED_RESULT")
    s, j = pm.rpc("task.status", id=ta, to="Закрыта")
    check("нелегальный переход отклонён", s == 400)
    s, j = pm.rpc("task.status", id=ta, to="На проверке", result="Готово, отчёт приложен")
    check("На проверке с результатом", s == 200)
    s, j = pm.rpc("task.status", id=ta, to="Принята")
    check("Принята → прогресс 100", s == 200 and j["result"]["progress"] == 100)
    s, j = pm.rpc("task.comment", id=tb, text="Комментарий теста")
    check("комментарий задачи", s == 200)

    print("== Исполнитель: ограничения ==")
    ex = Client(); ex.login("novikov")
    s, j = ex.rpc("task.status", id=tb, to="Принята")
    check("исполнитель не может принять задачу", s in (400, 403))
    s, j = ex.rpc("project.delete", id=pid)
    check("исполнитель не удаляет проекты", s == 403)

    print("== Канбан и Гант ==")
    s, j = pm.rpc("kanban.move", id=tb, col="work")
    check("kanban.move → В работе", s == 200 and j["result"]["status"] == "В работе", j.get("error"))
    s, j = pm.rpc("kanban.move", id=tb, col="done")
    check("kanban: move без результата отклонён", s == 400, j.get("error"))
    s, j = pm.rpc("gantt.impact", id=ta, delta=5)
    check("gantt.impact считает каскад", s == 200 and "affected" in j["result"], j.get("error"))
    s, j = pm.rpc("gantt.shift", id=ta, delta=5)
    check("gantt.shift применён", s == 200, j.get("error"))
    s, j = pm.rpc("gantt.shift", id=ta, delta=400)
    check("delta>365 отклонён", s == 400)

    print("== Трудозатраты ==")
    s, j = ex.rpc("time.create", taskId=ta, date="2026-11-20", hours=13, act="Разработка")
    check("hours>12 отклонён", s == 400)
    s, j = ex.rpc("time.create", taskId=ta, date="2026-11-20", hours=6, act="Разработка", comment="тест")
    check("time.create", s == 200 and j["ok"], j.get("error"))
    te = j["result"]["id"]
    s, j = ex.rpc("time.status", id=te, to="Отправлена")
    check("time Отправлена", s == 200)
    s, j = ex.rpc("time.update", id=te, hours=5)
    check("правка не-черновика отклонена", s == 400)
    s, j = pm.rpc("time.status", id=te, to="Утверждена")
    check("РП утверждает часы", s == 200 and j["result"]["approvedBy"] == "u3", j.get("error"))

    print("== Риски ==")
    s, j = pm.rpc("risk.create", title="Риск API", projectId=pid, cat="Технические", p=6, i=3, owner="u3",
                  strategy="Снижение", review="2026-12-15", status="Открыт")
    check("p=6 отклонён", s == 400)
    s, j = pm.rpc("risk.create", title="Риск API", projectId=pid, cat="Технические", p=4, i=3, owner="u3",
                  strategy="Снижение", review="2026-12-15", status="Открыт")
    check("risk.create", s == 200 and j["ok"], j.get("error"))
    rid = j["result"]["id"]
    s, j = pm.rpc("risk.update", id=rid, p=2, i=2)
    check("переоценка пишет историю", s == 200 and len(j["result"]["history"]) >= 2)
    s, j = pm.rpc("risk.measure.add", id=rid, title="Мера 1", assignee="u5", due="2026-12-10")
    mid = j["result"]["measures"][-1]["id"]
    check("мера добавлена", s == 200)
    s, j = pm.rpc("risk.measure.toggle", id=rid, mid=mid)
    check("мера выполнена", s == 200 and j["result"]["measures"][-1]["done"] is True)
    s, j = pm.rpc("risk.realize", id=rid)
    check("риск реализован", s == 200 and j["result"]["status"] == "Реализовался")

    print("== Документы и файлы ==")
    s, j = pm.rpc("doc.create", title="ТЗ API", type="Техническое задание", projectId=pid, fileName="tz_api.pdf", note="ред.1")
    check("doc.create", s == 200 and j["ok"], j.get("error"))
    did = j["result"]["id"]
    content = b"%PDF-1.4 demo test file"
    s, j = pm.req("POST", "/api/files/%s/1?name=tz_api.pdf" % did, content, raw=True, ctype="application/octet-stream")
    check("загрузка файла v1", s == 200 and j["ok"], j)
    s, payload = pm.req("GET", "/api/files/%s/1" % did)
    check("скачивание файла = загруженному", s == 200 and payload == content)
    s, j = pm.rpc("doc.version", id=did, fileName="tz_api_v2.pdf", note="ред.2")
    check("новая версия сбрасывает статус", s == 200 and j["result"]["status"] == "Черновик" and len(j["result"]["versions"]) == 2, j.get("error"))
    s, j = pm.rpc("doc.status", id=did, to="На согласовании")
    check("doc На согласовании", s == 200)
    s, j = pm.rpc("doc.status", id=did, to="Утверждён")
    check("doc Утверждён (у РП есть approve)", s == 200)
    s, payload = obs.req("GET", "/api/files/%s/1" % did)
    check("наблюдатель скачивает файл (view)", s == 200)

    print("== Инженерные расчёты ==")
    good = {"f": 5000, "d": 10, "ptx": 20, "gtx": 20, "grx": 20, "lmisc": 5, "sens": -90}
    s, j = pm.rpc("calc.run", model="link", title="РРЛ тест", projectId=pid, inputs=good)
    check("calc.run link", s == 200 and j["ok"], j.get("error"))
    cid = j["result"]["id"]
    fspl = next(o["v"] for o in j["result"]["outputs"]["outs"] if o["k"] == "fspl")
    check("FSPL≈126.42 дБ", abs(fspl - 126.42) < 0.05, fspl)
    check("запас ≥10 → ok", j["result"]["outputs"]["ok"] is True)
    bad = dict(good, f=-1)
    s, j = pm.rpc("calc.run", model="link", title="bad", projectId=pid, inputs=bad)
    check("невалидный ввод отклонён", s == 400)
    s, j = pm.rpc("calc.verify", id=cid)
    check("calc.verify", s == 200 and j["result"]["status"] == "Проверен")
    s, j = pm.rpc("calc.rerun", id=cid, inputs=dict(good, f=6000))
    check("rerun сбрасывает проверку", s == 200 and j["result"]["status"] == "Выполнен")

    print("== Совещания и поручения ==")
    s, j = pm.rpc("meeting.create", title="Штаб API", type="Штаб", date="2026-11-24", time="10:00", chair="u3", secretary="u5", attendees=["u3", "u5"])
    check("meeting.create", s == 200 and j["ok"], j.get("error"))
    mid = j["result"]["id"]
    check("код СОВ-2026-NNN", j["result"]["code"].startswith("СОВ-2026-"))
    s, j = pm.rpc("meeting.agenda.add", id=mid, title="Вопрос 1", reporter="u5")
    check("повестка", s == 200)
    s, j = pm.rpc("meeting.conduct", id=mid)
    check("проведено", s == 200)
    s, j = pm.rpc("meeting.decision.add", id=mid, text="Поручение API", resp="u5", due="2026-12-05", createTask=True)
    check("решение → поручение", s == 200 and j["result"]["decisions"][0]["taskId"], j.get("error"))
    s, j = pm.rpc("meeting.approve", id=mid)
    check("РП не утверждает протокол (нет approve)", s == 403)
    s, j = adm.rpc("meeting.approve", id=mid)
    check("админ утверждает протокол", s == 200)

    print("== Отчёты, календарь, ресурсы, дашборд ==")
    s, j = pm.rpc("report.save", type="week", title="Недельный отчёт API", html="<h1>Отчёт</h1>", params={"week": "2026-11-23"})
    check("report.save", s == 200 and j["ok"], j.get("error"))
    rpid = j["result"]["id"]
    s, j = pm.rpc("report.approve", id=rpid)
    check("РП не может утверждать отчёт (нет approve)", s == 403)
    s, j = adm.rpc("report.approve", id=rpid)
    check("админ утверждает отчёт", s == 200)
    s, j = pm.rpc("calendar.create", title="Событие API", date="2026-12-01", time="12:00")
    check("calendar.create", s == 200)
    s, j = adm.rpc("resource.update", userId="u5", capacity=6, absence={"from": "2026-12-24", "to": "2026-12-31", "reason": "Отпуск"})
    check("resource.update", s == 200)
    s, j = pm.rpc("builder.set", keys=["kpi_portfolio", "list_my_tasks", "matrix_risks"])
    check("builder.set", s == 200)
    s, j = pm.rpc("builder.set", keys=["nope"])
    check("неизвестный виджет отклонён", s == 400)
    s, j = pm.rpc("builder.preset")
    check("builder.preset", s == 200)

    print("== Администрирование и безопасность ==")
    s, j = pm.rpc("admin.user.create", name="x", login="x", role="exec")
    check("РП не админ → 403", s == 403)
    s, j = adm.rpc("admin.user.create", name="Тест Юзер", login="testuser", role="exec")
    check("admin.user.create", s == 200, j.get("error"))
    s, j = adm.req("GET", "/api/state")
    tu = next(u for u in j["state"]["users"] if u["login"] == "testuser")
    s, j = adm.rpc("admin.user.block", id=tu["id"])
    check("блокировка", s == 200)
    tc = Client(); s, j = tc.login("testuser")
    check("заблокированный не входит", s == 403 and j["error"]["code"] == "BLOCKED")
    s, j = adm.rpc("admin.user.block", id=tu["id"])
    check("разблокировка", s == 200)
    s, j = adm.rpc("admin.perm", role="observer", module="portfolio", op="create", on=True)
    check("admin.perm выдача", s == 200)
    s, j = obs.rpc("project.create", name="Теперь можно", cat="НИОКР", pm="u3", start="2026-12-01", end="2026-12-10", budgetHours=10)
    check("observer создаёт после выдачи права", s == 200, j.get("error"))
    s, j = adm.rpc("admin.perm", role="observer", module="portfolio", op="create", on=False)
    check("admin.perm отзыв", s == 200)
    s, j = adm.rpc("admin.diag")
    check("диагностика 4 проверки", s == 200 and len(j["result"]["checks"]) == 4)
    s, j = pm.req("POST", "/api/rpc", {"action": "client.log", "payload": {"cat": "INTEGRATION", "action": "Экспорт CSV", "module": "tasks"}})
    check("client.log INTEGRATION", s == 200)
    s, j = pm.req("POST", "/api/rpc", {"action": "client.log", "payload": {"cat": "SECURITY", "action": "подделка"}})
    check("client.log SECURITY запрещён", s == 400)

    print("== Логин-лок (3 ошибки → 60 с) ==")
    lk = Client()
    for i in range(3): lk.login("orlov", "bad%d" % i)
    s, j = lk.login("orlov", "demo")
    check("после 3 ошибок даже верный пароль → 429", s == 429 and j["error"]["code"] == "LOCKED")

    print("== Резервная копия и восстановление ==")
    s, payload = adm.req("GET", "/api/backup")
    backup = payload if isinstance(payload, dict) else json.loads(payload.decode())
    check("backup скачан", s == 200 and "tables" in backup and len(backup["tables"].get("users", [])) >= 8)
    check("backup содержит 13 таблиц", len(backup["tables"]) == 13)
    s, j = pm.req("POST", "/api/restore", backup)
    check("restore не-админом → 403", s == 403)
    s, j = adm.req("POST", "/api/restore", backup)
    check("restore админом", s == 200 and j["ok"], j)
    s, j = adm.req("GET", "/api/state")
    check("сессия жива после restore", s == 200)

    print("== Выход и сброс демо-данных ==")
    s, j = adm.rpc("admin.reset")
    check("admin.reset", s == 200 and j["ok"], j.get("error"))
    s, j = adm.req("GET", "/api/state")
    check("после reset снова 28 задач", s == 200 and len(j["state"]["tasks"]) == 28)
    s, j = adm.req("POST", "/api/logout")
    check("logout", s == 200)
    s, j = adm.req("GET", "/api/state")
    check("после logout 401", s == 401)

    print("\nИТОГ: %d ok, %d FAIL" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()