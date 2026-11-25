#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
СУП-ИП. Испытания установки, настройки и журналов (ТЗ §7, §8, §15).
Запуск:  python3 tests/setup_test.py --base http://127.0.0.1:8000 [--nsis]

Проверяется по отчёту пользователя об отказе установки:
  A. проба фактического запуска сервера (/api/health) и ожидание подъёма службы;
  B. однозначная трактовка состояния порта (Windows: SO_EXCLUSIVEADDRUSE);
  C. отсутствие службы Windows — штатная ситуация для stop/remove (winerror 1060);
  D. резервная копия при недоступной БД — предупреждение и код 3, а не аварийный traceback;
  E. планировщик считает только реально доставленные уведомления (защита от повторов);
  F. журнал установщика: единая кодировка UTF-16LE, полный вывод команд без обрезки;
  G. выбор источника PostgreSQL: офлайн-пакет имеет приоритет, радиокнопки не смешиваются;
  H. контроль целостности загруженного файла PostgreSQL по фактическому размеру;
  I. сведения об ОС и версия в журнале, сборка установщика (makensis, ключ --nsis);
  K. обновление поверх установленной версии: служба уже существует (1073) — перенастройка
     и запуск вместо отказа; коды возврата --health и мастера не скрывают неподнятый сервер;
  L. журнал установки не смешивает кодировки: новый файл UTF-16LE на каждый запуск;
  M. чтение журналов UTF-16LE/UTF-8/ANSI в M14 и при выгрузке по HTTP.
Только стандартная библиотека Python.
"""
import argparse, contextlib, http.cookiejar, importlib.util, io, json, os, re, shutil, socket, subprocess, sys, tempfile, time
import urllib.error, urllib.request

BASE = "http://127.0.0.1:8000"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SERVER = os.path.join(ROOT, "server.py")
NSI = os.path.join(ROOT, "installer", "setup.nsi")
ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print("  ok  " + name)
    else:
        fail += 1; print("  FAIL " + name + ("  " + str(extra) if extra else ""))


def load_server():
    spec = importlib.util.spec_from_file_location("supip_server_setup_test", SERVER)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


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
            return resp.getcode(), (json.loads(payload) if "json" in ct else payload.decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            payload = e.read()
            try:
                return e.code, json.loads(payload)
            except Exception:
                return e.code, payload.decode("utf-8", "replace")

    def login(self, login, password="demo"):
        return self.req("POST", "/api/login", {"login": login, "password": password})

    def rpc(self, action, payload=None):
        return self.req("POST", "/api/rpc", {"action": action, "payload": payload or {}})


def nsi_func(text, name):
    """Тело функции NSIS (ровно до её FunctionEnd)."""
    i = text.index("Function %s\n" % name)
    return text[i:text.index("FunctionEnd", i)]


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


def main():
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--nsis", action="store_true", help="дополнительно собрать установщик makensis")
    a = ap.parse_args()
    BASE = a.base.rstrip("/")
    host = re.sub(r"^https?://", "", BASE).split(":")[0]
    port = int(BASE.rsplit(":", 1)[1]) if re.search(r":\d+$", BASE) else 80
    scheme = "https" if BASE.startswith("https") else "http"

    srv = load_server()
    src = open(SERVER, encoding="utf-8").read()
    nsi = open(NSI, encoding="utf-8").read()

    print("== A. Проба фактического запуска сервера ==")
    ready, info = srv.server_health_probe(scheme, host, port)
    check("проба /api/health отвечает", ready, info)
    check("проба возвращает версию", info.get("version") == srv.VERSION, info)
    check("проба возвращает СУБД PostgreSQL", info.get("db") == "PostgreSQL", info)
    check("проба возвращает признак ok", info.get("ok") is True, info)
    dead = free_port()
    ready2, info2 = srv.server_health_probe(scheme, host, dead, timeout=2)
    check("проба незапущенного сервера — отказ", ready2 is False, info2)
    check("отказ содержит причину", bool(info2.get("error")), info2)
    check("отказ содержит адрес пробы", str(dead) in (info2.get("url") or ""), info2)
    ready3, info3 = srv.server_health_probe("https" if scheme == "http" else "http", host, port, timeout=2)
    check("проба по неверной схеме — отказ", ready3 is False, info3)
    t0 = time.time()
    w, wi = srv.wait_server_ready(scheme, host, port, wait_s=5)
    dt = time.time() - t0
    check("ожидание готового сервера завершается сразу", w is True and dt < 3, "%.1f с" % dt)
    t0 = time.time()
    w2, wi2 = srv.wait_server_ready(scheme, host, dead, wait_s=2, interval=0.5)
    dt2 = time.time() - t0
    check("ожидание незапущенного сервера — отказ", w2 is False, wi2)
    check("ожидание выдерживает заданный срок", 1.9 <= dt2 < 8, "%.1f с" % dt2)
    check("ожидание не превышает срок вдвое", dt2 < 4.5, "%.1f с" % dt2)
    check("в мастере используется ожидание подъёма службы", "wait_server_ready(scheme, probe_host" in src)
    check("мастер сообщает об отсутствии ответа за 40 с", "НЕТ ОТВЕТА за 40 с" in src)
    check("--health проверяет ответ сервера", "Сервер: ОТВЕЧАЕТ" in src and "Сервер: НЕ ОТВЕЧАЕТ" in src)

    print("== B. Однозначная трактовка состояния порта ==")
    s1 = srv.port_status_line(8443, False, True)
    check("порт занят своей службой — это норма", "занят службой СУП-ИП" in s1, s1)
    check("нет ложной тревоги о занятом порте", "ЗАНЯТ другим процессом" not in s1, s1)
    check("указан номер порта", "8443" in s1, s1)
    s2 = srv.port_status_line(8443, True, True)
    check("сервер отвечает — состояние подтверждено", "сервер отвечает" in s2, s2)
    s3 = srv.port_status_line(8443, True, False)
    check("свободный порт при отсутствии ответа — сервер не запущен", "свободен" in s3 and "НЕ запущен" in s3, s3)
    check("подсказка о службе при свободном порте", "служба не подняла порт" in s3, s3)
    s4 = srv.port_status_line(8443, False, False)
    check("чужой процесс на порте — ошибка", "ЗАНЯТ другим процессом" in s4, s4)
    check("подсказка об изменении настроек", "supip.ini" in s4, s4)
    check("мастер использует единую трактовку порта", src.count("port_status_line(port") >= 1)
    check("проба порта на Windows исключительная", "SO_EXCLUSIVEADDRUSE" in src)
    check("на прочих ОС сохраняется SO_REUSEADDR", "SO_REUSEADDR" in src)
    check("проба свободного порта", srv.socket_bind_probe("127.0.0.1", free_port()) is True)
    check("проба занятого порта", srv.socket_bind_probe("127.0.0.1", port) is False)

    print("== C. Служба Windows: отсутствие службы — не ошибка ==")
    e1060 = OSError(1060, "GetServiceKeyName")
    e1060.winerror = 1060
    check("распознан winerror 1060", srv._svc_absent(e1060) is True)
    check("распознан текст (1060,'GetServiceKeyName')", srv._svc_absent(Exception("(1060, 'GetServiceKeyName', 'Указанная служба не установлена.')")) is True)
    e1061 = OSError(1061, "StartService"); e1061.winerror = 1061
    check("прочие ошибки службы не подменяются", srv._svc_absent(e1061) is False)
    check("обычное исключение не считается отсутствием службы", srv._svc_absent(Exception("отказано в доступе")) is False)
    check("remove: отсутствие службы — код 0", 'LOG.info("Служба Windows не установлена — удаление не требуется")' in src)
    check("stop/restart: отсутствие службы — код 0", 'action in ("stop", "restart")' in src)
    check("start: отсутствие службы остаётся ошибкой", '_svc_absent(e) and action in ("stop", "restart")' in src)
    check("сообщение пользователю об удалении", "Служба не установлена — удаление не требуется." in src)
    check("сообщение пользователю об остановке", "Служба не установлена — остановка не требуется." in src)

    print("== D. Резервная копия при недоступной БД ==")
    tmp = tempfile.mkdtemp(prefix="supip-setup-test-")
    bad = "host=127.0.0.1 port=%d dbname=nodb user=nouser password=nopass connect_timeout=2" % free_port()
    env = dict(os.environ); env.pop("SUPIP_DSN", None)
    r = subprocess.run([sys.executable, SERVER, "--backup-now", "--dsn", bad, "--log-dir", tmp],
                       capture_output=True, text=True, timeout=90, env=env)
    out = (r.stdout or "") + (r.stderr or "")
    check("--backup-now без БД: код 3 (пропуск)", r.returncode == 3, r.returncode)
    check("понятное сообщение вместо traceback", "Резервная копия не создана" in out, out[:200])
    check("нет аварийного traceback", "Traceback" not in out, out[:200])
    check("объяснение про первую установку", "При первой установке это нормально" in out, out[:300])
    check("указан путь журнала", "supip.log" in out, out[:300])
    lg = os.path.join(tmp, "supip.log")
    txt = open(lg, encoding="utf-8", errors="replace").read() if os.path.isfile(lg) else ""
    check("в журнале WARNING, а не CRITICAL", "WARNING" in txt and "CRITICAL" not in txt, txt[-300:])
    check("журнал содержит причину отказа", "PostgreSQL недоступен" in txt, txt[-300:])
    r2 = subprocess.run([sys.executable, SERVER, "--dsn", bad, "--log-dir", tmp, "--port", str(free_port())],
                        capture_output=True, text=True, timeout=90, env=env)
    out2 = (r2.stdout or "") + (r2.stderr or "")
    check("обычный запуск без БД по-прежнему отказ (код 2)", r2.returncode == 2, r2.returncode)
    check("обычный запуск подсказывает мастер установки", "--install" in out2, out2[:200])
    shutil.rmtree(tmp, ignore_errors=True)

    print("== E. Планировщик: счётчик реально доставленных уведомлений ==")
    check("просрочка считается только при доставке", 'if notify(db, cur, t["assignee"], "task.overdue"' in src)
    check("истечение срока считается только при доставке", 'if notify(db, cur, t["assignee"], "task.due"' in src)
    adm = Client()
    c, j = adm.login("admin")
    check("вход администратора", c == 200 and j.get("ok"), c)
    c, j = adm.rpc("notif.tick")
    n1 = ((j.get("result") or {}).get("delivered")) if c == 200 else None
    check("внеочередной цикл планировщика выполнен", c == 200 and isinstance(n1, int), j)
    c, j = adm.rpc("notif.tick")
    n2 = ((j.get("result") or {}).get("delivered")) if c == 200 else None
    check("повторный цикл не доставляет дубли", n2 == 0, "первый %s · повторный %s" % (n1, n2))
    c, j = adm.rpc("notif.tick")
    n3 = ((j.get("result") or {}).get("delivered")) if c == 200 else None
    check("третий цикл также без дублей", n3 == 0, n3)

    print("== F. Журнал установщика: единая кодировка и полный вывод ==")
    check("установщик собран в Unicode", re.search(r"^Unicode true", nsi, re.M) is not None)
    check("записи журнала в UTF-16LE", 'FileWriteUTF16LE $LogFile' in nsi)
    check("нет записи журнала в ANSI", 'FileWrite $LogFile' not in nsi)
    check("метка порядка байтов для нового журнала", "FileWriteWord $LogFile 0xFEFF" in nsi)
    check("новый журнал создаётся макросом LOG_NEW", nsi.count("!insertmacro LOG_NEW") >= 2)
    check("прежний журнал сохраняется отдельно", "SUP-IP-Setup.prev.log" in nsi and "SUP-IP-Uninstall.prev.log" in nsi)
    check("старый журнал не дописывается в другой кодировке", 'FileOpen $LogFile "$LogPath" a\n  FileSeek $LogFile 0 END\n  !insertmacro LOG_BOM' not in nsi)
    check("позиция в конце перед каждой записью", nsi.count("FileSeek $LogFile 0 END") >= 3)
    check("вывод команды переносится в файл", 'supip-out.txt' in nsi)
    check("команда выполняется через cmd.exe", 'cmd.exe" /d /c "$TEMP\\supip-cmd.bat"' in nsi)
    check("вывод и ошибки собираются вместе", "2>&1" in nsi)
    check("код возврата передаётся из bat", "exit /b %ERRORLEVEL%" in nsi)
    check("вывод Python принудительно в UTF-8", "PYTHONIOENCODING=utf-8" in nsi)
    check("bat не меняет кодовую страницу (кириллические пути целы)", re.search(r'FileWrite \$R8 "[^"]*chcp', nsi) is None)
    check("строгое распознавание UTF-8", "UTF8Encoding($$false,$$true)" in nsi)
    check("резервная кодировка OEM 866", "GetEncoding(866)" in nsi)
    check("дописывание в журнал как Unicode", "[Text.Encoding]::Unicode" in nsi)
    check("путь журнала хранится отдельно от дескриптора", "Var LogPath" in nsi)
    check("внешний процесс дописывает по пути, а не по дескриптору",
          "AppendAllText('$LogPath'" in nsi and "AppendAllText('$LogFile'" not in nsi)
    app = nsi[nsi.index("!macro APPEND_OUT"):]
    app = app[:app.index("!macroend")]
    check("журнал закрывается на время дописывания", "FileClose $LogFile" in app and 'FileOpen $LogFile "$LogPath" a' in app)
    check("резервный путь построчного чтения", "FileRead $R4 $R5" in nsi)
    check("отсутствие вывода отмечается в журнале", "(вывод отсутствует)" in nsi)
    check("функция дописывания есть у деинсталлятора", "Function un.AppendCmdOutput" in nsi or '!insertmacro APPEND_OUT "un."' in nsi)
    check("деинсталлятор пишет журнал команд", nsi.count("!insertmacro URUN") == 3)
    check("журнал установки копируется в каталог программы", "installer.log" in nsi)
    check("журнал настройки копируется в каталог программы", "setup.log" in nsi)

    print("== G. Выбор источника PostgreSQL ==")
    pgpage = nsi_func(nsi, "PgPage")
    check("радиокнопки переключаются группой (авто)", "Call PgPickAuto" in pgpage)
    check("радиокнопки переключаются группой (файл)", "Call PgPickFile" in pgpage)
    check("радиокнопки переключаются группой (существующий)", "Call PgPickExist" in pgpage)
    check("нет одиночного NSD_Check без снятия прочих", "${NSD_Check} $Rb" not in pgpage)
    pick = nsi_func(nsi, "PgPickFile")
    check("выбор файла снимает режим загрузки", "${NSD_Uncheck} $RbAuto" in pick)
    check("выбор файла снимает режим существующего сервера", "${NSD_Uncheck} $RbExist" in pick)
    leave = nsi_func(nsi, "PgPageLeave")
    check("офлайн-пакет имеет приоритет на странице", 'StrCpy $PgMode "file"' in leave)
    check("приоритет офлайн-пакета отражён в журнале", "загрузка из сети не требуется" in leave)
    secpg = nsi[nsi.index('Section "PostgreSQL"'):nsi.index('Section "Настройка"')]
    check("дублирующая защита в секции установки", secpg.count('StrCpy $PgMode "file"') >= 2)
    check("автономность: пакет не требует интернета (ТЗ §8)", "Используется офлайн-пакет из установщика" in secpg)
    check("при неполной загрузке используется пакет", "Загрузка неполна — используется офлайн-пакет" in secpg)
    check("подсказка про полный установщик", "SUP-IP-Setup-full.exe" in secpg)

    print("== H. Контроль целостности файла PostgreSQL ==")
    check("размер измеряется по факту, а не макросом Locate", "${GetSize}" not in nsi)
    check("размер берётся через FileSeek в конец", 'FileSeek $4 0 END $5' in nsi)
    check("пересчёт байтов в МБ", "IntOp $1 $5 / 1048576" in nsi)
    check("в журнале и МБ, и байты", 'Загружено: $1 МБ ($5 байт)' in nsi)
    check("порог полноты сохранён", "${If} $1 < 100" in nsi)
    check("отказ журналируется", "ОТКАЗ: загруженный файл неполон" in nsi)

    print("== I. Сведения об окружении и версии ==")
    check("в журнале реальные сведения об ОС", 'CurrentVersion" "ProductName"' in nsi and 'CurrentVersion" "CurrentBuild"' in nsi)
    check("дата сборки не выдаётся за сведения об ОС", 'LOG "ОС: ${__DATE__}' not in nsi)
    check("версия установщика совпадает с версией сервера", ('!define VERSION "%s"' % srv.VERSION) in nsi, srv.VERSION)
    check("версия сервера в формате X.Y.Z", re.match(r"^\d+\.\d+\.\d+$", srv.VERSION) is not None, srv.VERSION)
    c, j = Client().req("GET", "/api/health")
    check("сервер сообщает ту же версию", c == 200 and j.get("version") == srv.VERSION, j)
    c, j = adm.rpc("admin.logs", {"name": "supip.log", "lines": 5})
    res = j.get("result") or {}
    names = [f["name"] for f in res.get("files", [])]
    check("M14 видит журнал работы сервера", "supip.log" in names, names)
    check("M14 видит журнал настройки", "setup.log" in names or "installer.log" in names, names)
    check("журнал отдаётся построчно", isinstance(res.get("lines"), list) and len(res["lines"]) > 0)
    c, t = adm.req("GET", "/api/logs/supip.log?tail=3")
    check("выгрузка журнала по HTTP", c == 200 and isinstance(t, str) and len(t) > 0, c)

    print("== K. Обновление поверх установленной версии: служба и коды возврата ==")
    e1073 = Exception("(1073, 'CreateService', 'Указанная служба уже существует.')")
    e1056 = Exception("(1056, 'StartService', 'Экземпляр службы уже запущен.')")
    e1062 = Exception("(1062, 'ControlService', 'Служба не была запущена.')")
    check("распознан winerror 1073 (служба уже существует)", srv._svc_exists(e1073) is True)
    check("1073 не считается отсутствием службы", srv._svc_absent(e1073) is False)
    check("распознан winerror 1056 (уже запущена)", srv._svc_running(e1056) is True)
    check("распознан winerror 1062 (не запущена)", srv._svc_stopped(e1062) is True)
    e5 = OSError(5, "CreateService"); e5.winerror = 5
    check("код ошибки берётся из winerror", srv._svc_code(e5) == 5)
    check("прочие ошибки не подменяются", srv._svc_exists(e5) is False and srv._svc_running(e5) is False)
    check("состояние службы: работает", srv.service_state_text(4) == "работает")
    check("состояние службы: остановлена", srv.service_state_text(1) == "остановлена")
    check("состояние службы: нет службы", srv.service_state_text(None) == "не установлена")
    check("на Linux состояние службы не выдумывается", srv.service_state() is None)

    # имитация pywin32: проверяется сам путь обновления службы (создание / 1073 / отказ запуска)
    class FakeSvc:
        SERVICE_AUTO_START = 2; SERVICE_WIN32_OWN_PROCESS = 16; SERVICE_ERROR_NORMAL = 1
    class FakeUtil:
        def __init__(self, exists=False, state=4, start_err=None, install_err=None, stay_stopped=False):
            self.exists = exists; self.state = 1 if not exists else state; self.start_err = start_err
            self.install_err = install_err; self.calls = []; self.stay_stopped = stay_stopped
        def InstallService(self, *a, **k):
            self.calls.append(("install", a, k))
            if self.install_err: raise self.install_err
            if self.exists: raise Exception("(1073, 'CreateService', 'Указанная служба уже существует.')")
        def StartService(self, name):
            self.calls.append(("start", name))
            if self.start_err: raise self.start_err
            if not self.stay_stopped: self.state = 4
        def StopService(self, name):
            self.calls.append(("stop", name)); self.state = 1
        def QueryServiceStatus(self, name): return (16, self.state)
    class FakeDone:
        def __init__(self, rc=0): self.returncode = rc; self.stdout = b""; self.stderr = b""
    def run_install(fake, sc_rc=0):
        util_mod = type(sys)("win32serviceutil"); svc_mod = type(sys)("win32service")
        for n in ("InstallService", "StartService", "StopService", "QueryServiceStatus"):
            setattr(util_mod, n, getattr(fake, n))
        for n in ("SERVICE_AUTO_START", "SERVICE_WIN32_OWN_PROCESS", "SERVICE_ERROR_NORMAL"):
            setattr(svc_mod, n, getattr(FakeSvc, n))
        saved = (sys.modules.get("win32serviceutil"), sys.modules.get("win32service"),
                 srv.subprocess.run, srv.service_state)
        sys.modules["win32serviceutil"] = util_mod; sys.modules["win32service"] = svc_mod
        sc_calls = []
        def fake_run(args, **kw):
            sc_calls.append(args)
            return FakeDone(sc_rc if args[:2] == ["sc", "config"] else 0)
        srv.subprocess.run = fake_run
        srv.service_state = lambda name=srv.SVC_NAME: fake.state
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc, note = srv.service_install("C:\\Program Files\\SUP-IP\\SUP-IP.exe", '--service run --config "C:\\Program Files\\SUP-IP\\supip.ini"')
        finally:
            for mod, val in zip(("win32serviceutil", "win32service"), saved[:2]):
                if val is None: sys.modules.pop(mod, None)
                else: sys.modules[mod] = val
            srv.subprocess.run, srv.service_state = saved[2], saved[3]
        return rc, note, sc_calls, buf.getvalue()

    f = FakeUtil(exists=False)
    rc, note, sc_calls, out = run_install(f)
    check("первая установка: код 0", rc == 0, note)
    check("первая установка: служба создана и запущена",
          [c[0] for c in f.calls] == ["install", "start"], f.calls)
    check("первая установка: настроен автоперезапуск", any(c[:2] == ["sc", "failure"] for c in sc_calls))
    check("первая установка: отчёт «установлена»", "установлена" in note and "обновлена" not in note, note)

    f = FakeUtil(exists=True)
    rc, note, sc_calls, out = run_install(f)
    check("обновление поверх 1073: код 0 (главное исправление)", rc == 0, note)
    check("обновление: путь службы переведён на новый EXE",
          any(c[:2] == ["sc", "config"] and any("SUP-IP.exe" in str(x) for x in c) for c in sc_calls), sc_calls)
    check("обновление: автозапуск сохранён", any("auto" in c for c in sc_calls if c[:2] == ["sc", "config"]))
    check("обновление: описание службы обновлено", any(c[:2] == ["sc", "description"] for c in sc_calls))
    check("обновление: служба перезапущена на новую версию",
          ("stop", srv.SVC_NAME) in f.calls and ("start", srv.SVC_NAME) in f.calls, f.calls)
    check("обновление: отчёт «обновлена»", "обновлена" in note, note)

    f = FakeUtil(exists=True, start_err=Exception("(5, 'StartService', 'Отказано в доступе.')"))
    rc, note, sc_calls, out = run_install(f)
    check("отказ запуска службы: код 1, а не ложный успех", rc == 1, note)
    check("отказ запуска: причина в отчёте", "не запустилась" in note, note)

    f = FakeUtil(exists=True)
    rc, note, sc_calls, out = run_install(f, sc_rc=1)
    check("отказ перенастройки службы: код 1", rc == 1, note)
    check("отказ перенастройки: причина в отчёте", "не обновлена" in note, note)

    f = FakeUtil(install_err=e5)
    rc, note, sc_calls, out = run_install(f)
    check("отказ создания службы: код 1", rc == 1, note)
    check("отказ создания: подсказка про администратора", "администратора" in out, out)

    f = FakeUtil(exists=True, state=1, stay_stopped=True)
    rc, note, sc_calls, out = run_install(f)
    check("служба осталась остановленной: код 1", rc == 1, note)
    check("состояние службы в отчёте", "остановлена" in note, note)

    check("код --health: всё исправно → 0", srv.health_exit_code(True, True, True) == 0)
    check("код --health: сервер не отвечает → 4", srv.health_exit_code(True, True, False) == 4)
    check("код --health: БД неисправна → 1", srv.health_exit_code(False, True, True) == 1)
    check("код --health: хранилище неисправно → 1", srv.health_exit_code(True, False, False) == 1)
    check("--health использует единую функцию кода", "rc = health_exit_code(ok, ok, ready)" in src)
    check("--health подсказывает запуск службы", "--service start (или sc start SUP-IP)" in src)
    check("код мастера: служба не запрошена → 0", srv.setup_exit_code(False, False, False) == 0)
    check("код мастера: служба не установлена → 4", srv.setup_exit_code(True, False, False) == 4)
    check("код мастера: служба есть, сервер молчит → 4", srv.setup_exit_code(True, True, False) == 4)
    check("код мастера: сервер отвечает → 0", srv.setup_exit_code(True, True, True) == 0)
    check("мастер возвращает код проверки, а не всегда 0", "return rc" in src and "=== Настройка завершена, но сервер не отвечает" in src)
    check("отчёт мастера показывает состояние службы", "установлена и запущена (состояние: %s)" in src)
    check("команда --service status добавлена", "install|remove|start|stop|restart|status|run" in src)
    check("start: уже запущена — не ошибка", '_svc_running(e) and action in ("start", "restart")' in src)
    check("stop: не запущена — не ошибка", '_svc_stopped(e) and action == "stop"' in src)

    print("== L. Установщик: обновление, журнал и итоговая проверка ==")
    check("мастер: код 4 не прерывает установку молча", '${If} $0 == 4' in nsi and "служба не подняла сервер (код 4)" in nsi)
    check("мастер: повторный запуск службы при коде 4", 'Повторный запуск службы СУП-ИП' in nsi)
    check("мастер: повторная проверка работоспособности", 'Повторная проверка работоспособности' in nsi)
    check("мастер: пользователю показана инструкция запуска", "sc start SUP-IP" in nsi)
    check("итоговая проверка различает успех и предупреждение", "УСТАНОВКА ЗАВЕРШЕНА С ПРЕДУПРЕЖДЕНИЕМ" in nsi)
    check("работающий PostgreSQL той же версии не переустанавливается", "повторная установка СУБД пропущена" in nsi)
    check("пропуск установки СУБД только для своей службы", '${If} $PgService == "postgresql-x64-${PG_VER}"' in nsi)
    check("ярлык запуска службы", "Запуск сервера (служба).lnk" in nsi)
    check("ярлык остановки службы", "Остановка сервера (служба).lnk" in nsi)
    check("ярлык состояния службы", "Состояние службы.lnk" in nsi and "--service status" in nsi)
    check("ярлык проверки работоспособности", "Проверка работоспособности.lnk" in nsi)

    print("== M. Чтение журналов разных кодировок (M14 и выгрузка) ==")
    check("распознан UTF-16LE с BOM", srv.decode_log_bytes("Журнал установки".encode("utf-16")) == "Журнал установки")
    check("распознан UTF-8 с BOM", srv.decode_log_bytes("\ufeffСервер запущен".encode("utf-8")) == "Сервер запущен")
    check("распознан UTF-8 без BOM", srv.decode_log_bytes("Планировщик: доставлено 3".encode("utf-8")) == "Планировщик: доставлено 3")
    check("распознан ANSI cp1251 (журнал прежней версии)", srv.decode_log_bytes("Служба установлена".encode("cp1251")) == "Служба установлена")
    check("кириллица не теряется на неполном блоке", srv.decode_log_bytes("ОТКАЗ: код 4".encode("cp866")) != "")
    check("кодировка выгрузки для UTF-16", srv.log_charset(b"\xff\xfe") == "utf-16")
    check("кодировка выгрузки для UTF-8", srv.log_charset(b"20") == "utf-8")
    ldir = srv.LOG_DIR
    os.makedirs(ldir, exist_ok=True)
    probe = os.path.join(ldir, "codectest.log")
    try:
        with open(probe, "wb") as f:
            f.write("ШАГ: мастер настройки\r\nСлужба Windows: обновлена и запущена\r\n".encode("utf-16"))
        c, j = adm.rpc("admin.logs", {"name": "codectest.log", "lines": 10})
        lines = (j.get("result") or {}).get("lines") or []
        check("M14 читает журнал установщика (UTF-16LE)", any("Служба Windows: обновлена и запущена" in l for l in lines), lines)
        check("в журнале нет символов подмены", not any("\ufffd" in l for l in lines), lines)
        c, t = adm.req("GET", "/api/logs/codectest.log?tail=2")
        check("выгрузка хвоста журнала в UTF-8", c == 200 and "Служба Windows" in t, c)
        with open(probe, "wb") as f:
            f.write("Журнал прежней версии: служба установлена\r\n".encode("cp1251"))
        c, j = adm.rpc("admin.logs", {"name": "codectest.log", "lines": 5})
        lines = (j.get("result") or {}).get("lines") or []
        check("M14 читает журнал прежней версии (cp1251)", any("служба установлена" in l for l in lines), lines)
        big = ("строка журнала установщика номер %d\r\n" % 0)
        with open(probe, "wb") as f:
            f.write("".join("строка журнала %d\r\n" % i for i in range(4000)).encode("utf-16"))
        c, j = adm.rpc("admin.logs", {"name": "codectest.log", "lines": 3})
        lines = (j.get("result") or {}).get("lines") or []
        check("хвост большого UTF-16 журнала читается без разрыва кодовых единиц",
              len(lines) == 3 and all("строка журнала" in l for l in lines), lines)
    finally:
        if os.path.isfile(probe): os.remove(probe)

    if a.nsis:
        print("== J. Сборка установщика (makensis) ==")
        mk = shutil.which("makensis")
        check("makensis доступен", bool(mk))
        if mk:
            exe = os.path.join(ROOT, "dist", "SUP-IP.exe")
            made = False
            if not os.path.isfile(exe):
                os.makedirs(os.path.dirname(exe), exist_ok=True)
                open(exe, "wb").write(b"MZ" + b"\0" * 2048); made = True
            r = subprocess.run([mk, "-V2", "setup.nsi"], cwd=os.path.join(ROOT, "installer"),
                               capture_output=True, text=True, timeout=600)
            check("установщик собирается без ошибок", r.returncode == 0, (r.stdout or "")[-500:])
            check("нет предупреждений о вызовах функций деинсталлятора", "un." not in (r.stdout or ""))
            out = os.path.join(ROOT, "dist", "SUP-IP-Setup-%s.exe" % srv.VERSION)
            check("файл установщика создан", os.path.isfile(out), out)
            check("размер установщика больше 100 КБ", os.path.isfile(out) and os.path.getsize(out) > 100000)
            if made:
                os.remove(exe)

    print("\nИТОГ: %d ok, %d FAIL" % (ok, fail))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())