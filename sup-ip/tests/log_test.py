#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
СУП-ИП. Испытания файлового журналирования (ТЗ §15: контроль, разбор ошибок и противоречий).
Запуск при работающем сервере:  python3 tests/log_test.py --base http://127.0.0.1:8000
Проверяется: журнал работы сервера (запуск, операции, отказы, ошибки, планировщик), журнал
мастера настройки и самотестирования, выгрузка журналов через API и M14, права доступа,
защита от обхода каталога, отсутствие паролей в журнале, параметры --log-level/--log-dir.
Только стандартная библиотека Python.
"""
import argparse, json, os, re, subprocess, sys, tempfile, urllib.request, urllib.error, http.cookiejar

BASE = "http://127.0.0.1:8000"
HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(os.path.dirname(HERE), "server.py")
ok = fail = 0

def check(name, cond, extra=""):
    global ok, fail
    if cond: ok += 1; print("  ok  " + name)
    else: fail += 1; print("  FAIL " + name + ("  " + str(extra) if extra else ""))

class Client:
    def __init__(self):
        self.cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))
    def req(self, method, path, data=None):
        body = json.dumps(data).encode("utf-8") if data is not None else None
        r = urllib.request.Request(BASE + path, data=body, method=method,
                                   headers={"Content-Type": "application/json"} if body else {})
        try:
            resp = self.op.open(r, timeout=60)
            payload = resp.read()
            ct = resp.headers.get("Content-Type", "")
            return resp.getcode(), (json.loads(payload) if "json" in ct else payload.decode("utf-8", "replace")), dict(resp.headers)
        except urllib.error.HTTPError as e:
            payload = e.read()
            try: return e.code, json.loads(payload), dict(e.headers)
            except Exception: return e.code, payload.decode("utf-8", "replace"), dict(e.headers)
    def login(self, login, password="demo"):
        return self.req("POST", "/api/login", {"login": login, "password": password})
    def rpc(self, action, payload=None):
        c, j, _ = self.req("POST", "/api/rpc", {"action": action, "payload": payload or {}})
        return c, j
    def logs(self, name="supip.log", lines=400):
        c, j = self.rpc("admin.logs", {"name": name, "lines": lines})
        return (j.get("result") or {}) if c == 200 else {}

def main():
    global BASE
    ap = argparse.ArgumentParser(); ap.add_argument("--base", default=BASE)
    BASE = ap.parse_args().base.rstrip("/")

    adm = Client()
    c, j, _ = adm.login("admin")
    check("вход администратора", c == 200 and j.get("ok"), c)

    print("== Журнал работы сервера ==")
    r = adm.logs()
    files = [f["name"] for f in r.get("files", [])]
    check("RPC admin.logs возвращает список журналов", "supip.log" in files, files)
    check("указан каталог журналов", str(r.get("dir", "")).endswith("logs"), r.get("dir"))
    check("указан уровень журналирования", r.get("level") in ("DEBUG", "INFO", "WARNING", "ERROR"), r.get("level"))
    # записи о запуске ищем по всему файлу: журнал пополняется и «хвост» их уже не содержит
    c, full, _ = adm.req("GET", "/api/logs/supip.log")
    text = full if isinstance(full, str) else "\n".join(r.get("lines", []))
    check("журнал содержит запись о запуске сервера", "СЕРВЕР ЗАПУЩЕН" in text, text[-300:])
    check("журнал содержит запуск планировщика уведомлений", "Планировщик уведомлений запущен" in text)
    check("строки журнала с отметкой времени и уровнем",
          bool(re.search(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} (INFO|WARNING|ERROR|DEBUG|CRITICAL)", text, re.M)))
    check("пароль подключения в журнал не пишется", "password=supip" not in text and "password=***" in text)
    check("HTTP-запросы журналируются", "HTTP" in text and "/api/login" in text)
    check("вход пользователя зафиксирован", "ВХОД: admin" in text)

    print("== Журналирование операций, отказов и ошибок ==")
    adm.rpc("notif.tick")
    t = "\n".join(adm.logs(lines=200).get("lines", []))
    check("успешная операция зафиксирована", "RPC notif.tick" in t and "OK" in t)
    check("внеочередной цикл планировщика зафиксирован", "Внеочередной цикл" in t or "RPC notif.tick" in t)
    exe = Client(); exe.login("novikov")
    c, j = exe.rpc("admin.reset")
    check("исполнителю сброс запрещён (403)", c == 403, c)
    t = "\n".join(adm.logs(lines=200).get("lines", []))
    check("отказ прав зафиксирован в журнале", "ОТКАЗ ПРАВ" in t, t[-200:])
    c, j = adm.rpc("no.such.action")
    check("неизвестная операция → 404", c == 404, c)
    t = "\n".join(adm.logs(lines=200).get("lines", []))
    check("неизвестная операция зафиксирована", "неизвестная операция" in t)
    bad = Client(); bad.login("kozlova", "wrong-password")
    t = "\n".join(adm.logs(lines=200).get("lines", []))
    check("неуспешный вход зафиксирован", "ВХОД отклонён" in t, t[-200:])
    adm.req("POST", "/api/rpc", {"action": "client.log", "payload": {"cat": "INTEGRATION", "action": "Испытание журнала", "module": "admin"}})
    t = "\n".join(adm.logs(lines=200).get("lines", []))
    check("клиентское событие проходит через журнал сервера", "RPC client.log" in t)
    lim = adm.logs(lines=15).get("lines", [])
    check("параметр числа строк соблюдается", 0 < len(lim) <= 15, len(lim))

    print("== Выгрузка журналов через API и права ==")
    c, j, _ = adm.req("GET", "/api/logs")
    check("GET /api/logs — список файлов", c == 200 and any(f["name"] == "supip.log" for f in j.get("files", [])), c)
    c, body, hdr = adm.req("GET", "/api/logs/supip.log?tail=25")
    check("GET /api/logs/supip.log?tail=25 — текст", c == 200 and isinstance(body, str) and 0 < len(body.splitlines()) <= 25, c)
    c, body, hdr = adm.req("GET", "/api/logs/supip.log")
    check("полная выгрузка журнала", c == 200 and len(body) > 200, c)
    check("выгрузка отдаётся как файл", "attachment" in (hdr.get("Content-Disposition") or ""), hdr.get("Content-Disposition"))
    c, j, _ = adm.req("GET", "/api/logs/no-such-file.log")
    check("несуществующий журнал → 404", c == 404, c)
    c, j, _ = adm.req("GET", "/api/logs/..%2Fsupip.ini")
    check("обход каталога запрещён", c in (400, 404), c)
    c, j, _ = exe.req("GET", "/api/logs")
    check("не-администратору список журналов запрещён (403)", c == 403, c)
    c, j, _ = exe.req("GET", "/api/logs/supip.log?tail=10")
    check("не-администратору выгрузка запрещена (403)", c == 403, c)
    c, j = exe.rpc("admin.logs")
    check("RPC admin.logs не-администратору → 403", c == 403, c)
    anon = Client()
    c, j, _ = anon.req("GET", "/api/logs")
    check("без сессии → 401", c == 401, c)

    print("== Журнал мастера настройки и самотестирования ==")
    env = dict(os.environ, SUPIP_DSN="host=127.0.0.1 port=5432 dbname=supip user=supip password=supip")
    p = subprocess.run([sys.executable, SERVER, "--selftest"], capture_output=True, text=True, env=env, timeout=120)
    check("самотестирование выполняется", p.returncode == 0, p.stdout[-200:])
    check("в выводе указан путь журнала", "setup.log" in p.stdout, p.stdout[-200:])
    r = adm.logs("setup.log", 200)
    st = "\n".join(r.get("lines", []))
    check("журнал настройки создан", r.get("exists") is True, r.get("files"))
    check("самотестирование записано в журнал настройки", "Самотестирование" in st, st[-200:])
    p = subprocess.run([sys.executable, SERVER, "--health"], capture_output=True, text=True, env=env, timeout=120)
    check("диагностика --health выполняется", p.returncode == 0, p.stdout[-200:])
    c, t, _ = adm.req("GET", "/api/logs/supip.log")
    check("запуск в режиме диагностики зафиксирован", "Режим запуска: --health" in t, t[-300:])

    print("== Параметры журналов ==")
    with tempfile.TemporaryDirectory() as td:
        p = subprocess.run([sys.executable, SERVER, "--selftest", "--log-dir", td, "--log-level", "DEBUG"],
                           capture_output=True, text=True, env=env, timeout=120)
        check("--log-dir/--log-level принимаются", p.returncode == 0, p.stdout[-200:])
        check("журнал создан в указанном каталоге", os.path.isfile(os.path.join(td, "setup.log")), os.listdir(td))
    size_before = next((f["size"] for f in adm.logs().get("files", []) if f["name"] == "supip.log"), 0)
    for i in range(3): adm.rpc("notif.list")
    size_after = next((f["size"] for f in adm.logs().get("files", []) if f["name"] == "supip.log"), 0)
    check("журнал пополняется при работе", size_after > size_before, (size_before, size_after))

    print("\nИТОГ: %d ok, %d FAIL" % (ok, fail))
    sys.exit(1 if fail else 0)

if __name__ == "__main__":
    main()
