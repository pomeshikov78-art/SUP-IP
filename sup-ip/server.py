#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
СУП-ИП — программная система управления проектами инженерного подразделения.
Серверная часть (единое ядро + функциональные модули M00–M17), ТЗ редакция 3.0.

Стек: Python 3.10+ (только stdlib + psycopg2), PostgreSQL, REST/JSON, модульный монолит.
Автономная работа в локальной сети, до 10 одновременных пользователей.
Запуск:  python server.py --host 0.0.0.0 --port 80
БД:      параметры подключения — аргументы --pg-* или переменные SUPIP_PG* / стандартные PG*.
"""
import argparse, base64, calendar, configparser, datetime, hashlib, hmac, json, logging, logging.handlers, math, os, platform, re, secrets, shutil, ssl, subprocess, sys, threading, time, traceback, urllib.parse, urllib.request
import http.server, socketserver
from http.cookies import SimpleCookie

try:
    import psycopg2, psycopg2.pool, psycopg2.extras
except ImportError:
    print("Требуется psycopg2:  python -m pip install psycopg2-binary"); sys.exit(1)

def _init_console():
    """Консоль Windows по умолчанию cp1252 и не кодирует кириллицу — CLI установки падал бы
       с UnicodeEncodeError. Принудительно UTF-8 (ТЗ §7.3: мастер установки в консоли)."""
    for stream in (sys.stdout, sys.stderr):
        try: stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception: pass
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception: pass

_init_console()

try:
    import docgen                      # генератор DOCX/XLSX/PDF (ТЗ §25), только stdlib
except ImportError:
    docgen = None

APP_NAME = "СУП-ИП"
VERSION = "1.10.0"
BASE_DIR = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
STATIC_DIR = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "static")
MAX_USERS_ONLINE = 10
MAX_BODY = 32 * 1024 * 1024
MAX_FILE = 25 * 1024 * 1024
DAY = 86400

# ---------------- Справочники и константы ядра ----------------
ROLES = {
    "admin": "Администратор", "head": "Руководитель подразделения",
    "pm": "Руководитель проекта", "exec": "Исполнитель", "observer": "Наблюдатель",
}
MODULES = {
    "dash": "M00", "portfolio": "M01", "tasks": "M02", "kanban": "M03", "gantt": "M04",
    "resources": "M05", "time": "M06", "control": "M07", "risks": "M08", "docs": "M09",
    "calcs": "M10", "analytics": "M11", "calendar": "M12", "reports": "M13",
    "admin": "M14", "journal": "M15", "meetings": "M16", "builder": "M17",
}
OPS = ["view", "create", "edit", "approve", "export", "delete"]
ALL = list(OPS); VIEW = ["view"]
BASE_PERMS = {
    "admin": {m: ALL for m in MODULES},
    "head": {"dash": ["view", "edit"], "portfolio": ALL, "tasks": ALL, "kanban": ["view", "edit"],
             "gantt": ["view", "edit", "export"], "calendar": ["view", "create", "edit"],
             "resources": ["view", "edit", "export"], "time": ["view", "create", "edit", "approve", "export"],
             "control": ["view", "create", "edit", "export"], "risks": ALL,
             "docs": ["view", "create", "edit", "approve", "export"], "calcs": ["view", "create", "edit", "approve", "export"],
             "analytics": ["view", "export"], "reports": ["view", "create", "approve", "export"],
             "meetings": ["view", "create", "edit", "approve", "export"], "builder": ["view", "edit"],
             "admin": [], "journal": ["view", "export"]},
    "pm": {"dash": ["view", "edit"], "portfolio": ["view", "create", "edit", "export"],
           "tasks": ["view", "create", "edit", "approve", "export"], "kanban": ["view", "edit"],
           "gantt": ["view", "edit", "export"], "calendar": ["view", "create", "edit"],
           "resources": ["view", "export"], "time": ["view", "create", "edit", "approve", "export"],
           "control": ["view", "create", "edit"], "risks": ["view", "create", "edit", "export"],
           "docs": ["view", "create", "edit", "approve", "export"], "calcs": ["view", "create", "edit", "approve", "export"],
           "analytics": ["view", "export"], "reports": ["view", "create", "export"],
           "meetings": ["view", "create", "edit", "export"], "builder": ["view", "edit"],
           "admin": [], "journal": ["view"]},
    "exec": {"dash": ["view", "edit"], "portfolio": VIEW, "tasks": ["view", "edit"], "kanban": ["view", "edit"],
             "gantt": VIEW, "calendar": ["view", "create"], "resources": VIEW,
             "time": ["view", "create", "edit"], "control": VIEW, "risks": ["view", "create"],
             "docs": ["view", "create"], "calcs": ["view", "create"], "analytics": VIEW, "reports": VIEW,
             "meetings": VIEW, "builder": ["view", "edit"], "admin": [], "journal": []},
    "observer": {"dash": VIEW, "portfolio": VIEW, "tasks": VIEW, "kanban": VIEW, "gantt": VIEW,
                 "calendar": VIEW, "resources": [], "time": [], "control": VIEW, "risks": VIEW,
                 "docs": VIEW, "calcs": [], "analytics": VIEW, "reports": VIEW, "meetings": VIEW,
                 "builder": [], "admin": [], "journal": []},
}
P_TRANS = {
    "Инициатива": ["Подготовка", "Отменён"], "Подготовка": ["Планирование", "Отменён"],
    "Планирование": ["Утверждён", "Отменён"], "Утверждён": ["Выполняется", "Приостановлен", "Отменён"],
    "Выполняется": ["Требует внимания", "Приостановлен", "Завершён"], "Требует внимания": ["Выполняется", "Приостановлен"],
    "Приостановлен": ["Выполняется", "Отменён"], "Завершён": ["Архивный"], "Отменён": ["Архивный"], "Архивный": [],
}
T_TRANS = {
    "Черновик": ["Новая", "Отменена"], "Новая": ["Запланирована", "Отменена"],
    "Запланирована": ["Готова к выполнению", "Новая", "Отменена"], "Готова к выполнению": ["В работе", "Отменена"],
    "В работе": ["Приостановлена", "Заблокирована", "На проверке"], "Приостановлена": ["В работе", "Отменена"],
    "Заблокирована": ["В работе", "Отменена"], "На проверке": ["Принята", "Возвращена на доработку"],
    "Возвращена на доработку": ["В работе"], "Принята": ["Закрыта"], "Закрыта": [], "Отменена": [],
}
EXEC_TRANS = {"Готова к выполнению": ["В работе"], "В работе": ["Приостановлена", "На проверке"],
              "Приостановлена": ["В работе"], "Возвращена на доработку": ["В работе"], "Заблокирована": ["В работе"]}
DONE_ST = ["Принята", "Закрыта"]
KB_COLS = {
    "plan": {"sts": ["Черновик", "Новая", "Запланирована"], "target": "Запланирована"},
    "todo": {"sts": ["Готова к выполнению"], "target": "Готова к выполнению"},
    "work": {"sts": ["В работе", "Возвращена на доработку", "Заблокирована"], "target": "В работе", "wip": True},
    "review": {"sts": ["На проверке"], "target": "На проверке"},
    "pause": {"sts": ["Приостановлена"], "target": "Приостановлена"},
    "done": {"sts": ["Принята", "Закрыта"], "target": "Принята"},
}
PROJ_TEMPLATES = {
    "none": [],
    "modern": [["Обследование объекта", 0.06, 0, 7], ["Разработка ТЗ", 0.10, 8, 18],
               ["Рабочая документация", 0.22, 19, 35], ["Поставка и монтаж", 0.30, 36, 55],
               ["Пусконаладка и испытания", 0.22, 56, 65], ["Передача документации", 0.10, 66, 70]],
    "nir": [["Аналитический обзор", 0.08, 0, 10], ["Аванпроект", 0.15, 11, 25],
            ["Моделирование", 0.25, 26, 50], ["Макетирование", 0.22, 51, 75],
            ["Испытания макета", 0.18, 76, 90], ["Отчёт о НИР", 0.12, 91, 100]],
}
DEFAULT_DICTS = {
    "activities": ["НИОКР", "ОКР", "Инженерное проектирование", "Разработка технической документации",
                   "Расчёты и моделирование", "Испытания", "Подготовка производства", "Сопровождение установочной партии",
                   "Эксплуатационные работы", "Аналитическая работа", "Подготовка отчётов", "Совещания",
                   "Согласования", "Административная деятельность", "Обучение", "Работа с документацией", "Иные работы"],
    "projCats": ["Разработка", "Модернизация", "НИОКР", "ОКР", "Автоматизация", "Сопровождение"],
    "taskTypes": ["Задача", "Этап", "Поручение", "Контрольная точка"],
    "riskCats": ["Технические", "Организационные", "Ресурсные", "Снабжение", "Информационная безопасность", "Финансовые"],
    "docTypes": ["Техническое задание", "Пояснительная записка", "Расчётная записка", "Конструкторская документация",
                 "Программа испытаний", "Протокол", "Акт", "Техническое описание", "Эксплуатационная документация", "Перечень"],
}

# ---------------- M10: расчётные модели (инженерные методики) ----------------
def _r2(x): return round(x, 2)
def CALC_LINK(v):
    fspl = 32.44 + 20*math.log10(v["f"]) + 20*math.log10(v["d"])
    prx = v["ptx"] + v["gtx"] + v["grx"] - fspl - v["lmisc"]
    margin = prx - v["sens"]
    ok = margin >= 10
    return {"outs": [
        {"k": "fspl", "n": "Потери в свободном пространстве (FSPL)", "u": "дБ", "v": _r2(fspl)},
        {"k": "prx", "n": "Расчётный уровень на входе приёмника", "u": "дБм", "v": _r2(prx)},
        {"k": "margin", "n": "Запас относительно чувствительности", "u": "дБ", "v": _r2(margin)}],
        "ok": ok, "verdict": "Условие выполнено: запас ≥ 10 дБ" if ok else "Условие НЕ выполнено: запас < 10 дБ — требуется корректировка трассы или оборудования",
        "formula": "FSPL = 32,44 + 20·lg(f[МГц]) + 20·lg(d[км]);  Pпр = Pпер + Gпер + Gпр − FSPL − Lпроч;  Запас = Pпр − Pчув"}
def CALC_COVERAGE(v):
    lmax = v["ptx"] + v["gtx"] + v["grx"] - v["lmisc"] - v["sens"]
    r = 10 ** ((lmax - 32.44 - 20*math.log10(v["f"])) / 20)
    cell = math.pi * r * r * 0.78
    n = math.ceil(v["area"] / cell)
    ok = n <= 64
    return {"outs": [
        {"k": "lmax", "n": "Допустимые суммарные потери", "u": "дБ", "v": round(lmax, 1)},
        {"k": "r", "n": "Расчётный радиус соты", "u": "км", "v": _r2(r)},
        {"k": "cell", "n": "Эффективная площадь соты (перекрытие 22%)", "u": "км²", "v": _r2(cell)},
        {"k": "n", "n": "Требуемое число базовых станций", "u": "шт", "v": n}],
        "ok": ok, "verdict": "Покрытие обеспечивается расчётным числом БС" if ok else "Требуется пересмотр параметров: слишком много БС",
        "formula": "Lmax = Pпер + Gбс + Gаб − Lзап − Pчув;  R = 10^((Lmax − 32,44 − 20·lg f)/20);  N = ⌈Sцель / (0,78·π·R²)⌉"}
def CALC_RELIABILITY(v):
    lam = (v["n1"]*v["l1"] + v["n2"]*v["l2"] + v["n3"]*v["l3"]) * 1e-6
    mtbf = 1 / lam
    p = math.exp(-lam * v["t"])
    kg = mtbf / (mtbf + v["mttr"])
    ok = p >= 0.9
    return {"outs": [
        {"k": "lam", "n": "Суммарная интенсивность отказов λΣ", "u": "·10⁻⁶ 1/ч", "v": _r2(lam*1e6)},
        {"k": "mtbf", "n": "Средняя наработка на отказ (MTBF)", "u": "ч", "v": round(mtbf)},
        {"k": "p", "n": "Вероятность безотказной работы P(t)", "u": "", "v": round(p, 4)},
        {"k": "kg", "n": "Коэффициент готовности Kг", "u": "", "v": round(kg, 5)}],
        "ok": ok, "verdict": "Требование по безотказности выполняется (P ≥ 0,9)" if ok else "Требование НЕ выполняется: P(t) < 0,9 — нужны резервирование или ЗИП",
        "formula": "λΣ = Σ nᵢ·λᵢ;  MTBF = 1/λΣ;  P(t) = e^(−λΣ·t);  Kг = MTBF/(MTBF + Tв)"}
def CALC_CAPACITY(v):
    c = v["b"] * math.log2(1 + 10 ** (v["snr"]/10))
    eff = c * v["eff"]; per = eff / v["users"]
    ok = per >= 2
    return {"outs": [
        {"k": "c", "n": "Предел Шеннона C", "u": "Мбит/с", "v": round(c, 1)},
        {"k": "eff", "n": "Эффективная скорость сектора", "u": "Мбит/с", "v": round(eff, 1)},
        {"k": "per", "n": "Средняя скорость на абонента", "u": "Мбит/с", "v": _r2(per)}],
        "ok": ok, "verdict": "Обеспечивается ≥ 2 Мбит/с на абонента" if ok else "Недостаточная скорость на абонента — расширить полосу или число секторов",
        "formula": "C = B·log₂(1 + 10^(SNR/10));  Cэф = C·η;  Cаб = Cэф / N"}
def CALC_LABOR(v):
    total = v["base"] * v["k"]
    stages = [["Аванпроект", 0.10], ["Эскизное проектирование", 0.15], ["Техническое проектирование", 0.25],
              ["Разработка КД", 0.30], ["Испытания и доводка", 0.20]]
    months = total / (v["team"] * v["fund"])
    outs = [{"k": "st%d" % i, "n": "Этап «%s» (%d%%)" % (n, round(p*100)), "u": "чел·ч", "v": round(total*p)}
            for i, (n, p) in enumerate(stages)]
    outs.append({"k": "total", "n": "Итоговая трудоёмкость", "u": "чел·ч", "v": round(total)})
    outs.append({"k": "months", "n": "Оценочная длительность", "u": "мес", "v": round(months, 1)})
    ok = months <= 18
    return {"outs": outs, "ok": ok,
            "verdict": "Срок укладывается в типовой цикл ОКР (≤ 18 мес)" if ok else "Длительность превышает типовой цикл — пересмотреть состав команды",
            "formula": "W = Wбаз·Kсл;  Wэтап = W·pэтап;  T = W / (Nком · Fмес)"}
def CALC_COST(v):
    fot = v["hours"] * v["rate"]; ovh = fot * v["over"] / 100
    sub = fot + ovh + v["mat"]; res = sub * v["risk"] / 100; total = sub + res
    return {"outs": [
        {"k": "fot", "n": "Фонд оплаты труда", "u": "₽", "v": round(fot)},
        {"k": "ovh", "n": "Накладные расходы", "u": "₽", "v": round(ovh)},
        {"k": "res", "n": "Резерв на риски", "u": "₽", "v": round(res)},
        {"k": "total", "n": "Итоговая стоимость", "u": "₽", "v": round(total)}],
        "ok": True, "verdict": "Смета сформирована и готова к согласованию",
        "formula": "ФОТ = W·ставка;  НР = ФОТ·p%;  Итог = (ФОТ + НР + Мат)·(1 + pрез%)"}
CALC_MODELS = {
    "link": {"name": "Бюджет радиолинии", "ver": "2.1", "run": CALC_LINK, "inputs": [
        ("f", "Частота", 30, 80000), ("d", "Длина пролёта", 0.1, 200), ("ptx", "Мощность передатчика", -10, 60),
        ("gtx", "Усиление антенны передачи", 0, 60), ("grx", "Усиление антенны приёма", 0, 60),
        ("lmisc", "Прочие потери", 0, 30), ("sens", "Чувствительность приёмника", -130, -30)]},
    "coverage": {"name": "Зона покрытия", "ver": "1.3", "run": CALC_COVERAGE, "inputs": [
        ("f", "Частота", 30, 80000), ("ptx", "Мощность передатчика", -10, 60), ("gtx", "Усиление антенны БС", 0, 40),
        ("grx", "Усиление антенны абонента", 0, 30), ("lmisc", "Запас на затенение", 0, 40),
        ("sens", "Чувствительность приёмника", -130, -30), ("area", "Целевая площадь", 0.1, 100000)]},
    "reliability": {"name": "Надёжность изделия (MTBF)", "ver": "1.0", "run": CALC_RELIABILITY, "inputs": [
        ("n1", "Группа 1: количество", 1, 100000), ("l1", "Группа 1: λ", 0.0001, 1000),
        ("n2", "Группа 2: количество", 0, 100000), ("l2", "Группа 2: λ", 0, 1000),
        ("n3", "Группа 3: количество", 0, 100000), ("l3", "Группа 3: λ", 0, 1000),
        ("t", "Время работы", 1, 1000000), ("mttr", "Время восстановления", 0.1, 1000)]},
    "capacity": {"name": "Пропускная способность канала", "ver": "1.1", "run": CALC_CAPACITY, "inputs": [
        ("b", "Полоса канала", 0.001, 2000), ("snr", "Отношение сигнал/шум", -10, 60),
        ("eff", "Эффективность протокола", 0.1, 1), ("users", "Число абонентов", 1, 10000)]},
    "labor": {"name": "Трудоёмкость этапов ОКР", "ver": "2.0", "run": CALC_LABOR, "inputs": [
        ("base", "Базовая трудоёмкость", 10, 1000000), ("k", "Коэффициент сложности", 0.5, 3),
        ("team", "Численность команды", 1, 100), ("fund", "Месячный фонд времени", 80, 200)]},
    "cost": {"name": "Стоимость работ", "ver": "1.2", "run": CALC_COST, "inputs": [
        ("hours", "Трудоёмкость работ", 1, 1000000), ("rate", "Средняя ставка", 1, 100000),
        ("over", "Накладные расходы", 0, 300), ("mat", "Материалы и покупные", 0, 1e9), ("risk", "Резерв на риски", 0, 100)]},
}

# ---------------- Утилиты дат ----------------
def today():
    d = datetime.date.today(); return d
def dISO(d): return d.isoformat()
def D(off): return dISO(today() + datetime.timedelta(days=off))
def parseD(s): return datetime.date.fromisoformat(s)
def diffD(a, b): return (parseD(a) - parseD(b)).days
def isWk(s): return parseD(s).weekday() >= 5
def workdays(a, b):
    n = 0; d = parseD(a)
    while d <= parseD(b):
        if d.weekday() < 5: n += 1
        d += datetime.timedelta(days=1)
    return max(n, 1)
def addWk(s, n):
    d = parseD(s); c = 0
    while c < n:
        d += datetime.timedelta(days=1)
        if d.weekday() < 5: c += 1
    return dISO(d)
def now_ms(): return int(time.time() * 1000)
def uid(p): return p + "_" + secrets.token_hex(5)

# ---------------- Пароли ----------------
def pass_hash(pw, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), 150000).hex()
    return salt, h
def pass_verify(pw, salt, h):
    return hmac.compare_digest(pass_hash(pw, salt)[1], h)

# ---------------- Слой БД (PostgreSQL) ----------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users(
  id TEXT PRIMARY KEY, login TEXT UNIQUE NOT NULL, name TEXT NOT NULL, role TEXT NOT NULL,
  position TEXT NOT NULL DEFAULT '', capacity INT NOT NULL DEFAULT 8, color TEXT NOT NULL DEFAULT '#2563eb',
  blocked BOOLEAN NOT NULL DEFAULT FALSE, pass_salt TEXT NOT NULL, pass_hash TEXT NOT NULL,
  failed INT NOT NULL DEFAULT 0, lock_until BIGINT NOT NULL DEFAULT 0, absences JSONB NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS sessions(
  token TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created BIGINT NOT NULL, last_seen BIGINT NOT NULL);
CREATE TABLE IF NOT EXISTS projects(
  id TEXT PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, cat TEXT NOT NULL,
  status TEXT NOT NULL, pm TEXT REFERENCES users(id) ON DELETE SET NULL,
  start TEXT NOT NULL, "end" TEXT NOT NULL, budget_hours INT NOT NULL DEFAULT 0,
  team JSONB NOT NULL DEFAULT '[]', descr TEXT NOT NULL DEFAULT '',
  baseline JSONB NOT NULL DEFAULT '{}', baseline_at BIGINT, created_at BIGINT NOT NULL);
CREATE TABLE IF NOT EXISTS meetings(
  id TEXT PRIMARY KEY, code TEXT UNIQUE NOT NULL, title TEXT NOT NULL, type TEXT NOT NULL,
  date TEXT NOT NULL, mtime TEXT NOT NULL DEFAULT '', chair TEXT REFERENCES users(id) ON DELETE SET NULL,
  secretary TEXT REFERENCES users(id) ON DELETE SET NULL, attendees JSONB NOT NULL DEFAULT '[]',
  status TEXT NOT NULL, agenda JSONB NOT NULL DEFAULT '[]', decisions JSONB NOT NULL DEFAULT '[]',
  protocol JSONB NOT NULL DEFAULT '{}', materials JSONB NOT NULL DEFAULT '[]',
  projects JSONB NOT NULL DEFAULT '[]', recurrence JSONB NOT NULL DEFAULT '{}',
  plan_log JSONB NOT NULL DEFAULT '[]', series_id TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS risks(
  id TEXT PRIMARY KEY, code TEXT UNIQUE NOT NULL, title TEXT NOT NULL, cat TEXT NOT NULL,
  project_id TEXT REFERENCES projects(id) ON DELETE CASCADE, p INT NOT NULL, i INT NOT NULL,
  strategy TEXT NOT NULL, owner TEXT REFERENCES users(id) ON DELETE SET NULL, status TEXT NOT NULL,
  review TEXT NOT NULL, residual JSONB, descr TEXT NOT NULL DEFAULT '',
  measures JSONB NOT NULL DEFAULT '[]', history JSONB NOT NULL DEFAULT '[]', risk_events JSONB NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS tasks(
  id TEXT PRIMARY KEY, code TEXT UNIQUE NOT NULL, project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
  parent TEXT REFERENCES tasks(id) ON DELETE SET NULL, title TEXT NOT NULL, type TEXT NOT NULL DEFAULT 'task',
  status TEXT NOT NULL, priority TEXT NOT NULL, assignee TEXT REFERENCES users(id) ON DELETE SET NULL,
  start TEXT NOT NULL, "end" TEXT NOT NULL, plan_hours INT NOT NULL DEFAULT 0, progress INT NOT NULL DEFAULT 0,
  result TEXT NOT NULL DEFAULT '', block_reason TEXT NOT NULL DEFAULT '',
  deps JSONB NOT NULL DEFAULT '[]', comments JSONB NOT NULL DEFAULT '[]', descr TEXT NOT NULL DEFAULT '',
  created_at BIGINT NOT NULL, created_by TEXT, meeting_id TEXT REFERENCES meetings(id) ON DELETE SET NULL,
  risk_id TEXT REFERENCES risks(id) ON DELETE SET NULL, directive JSONB NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS meeting_tasks(
  id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  decision_id TEXT NOT NULL, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  ts BIGINT NOT NULL, by_user TEXT, UNIQUE(decision_id, task_id));
CREATE INDEX IF NOT EXISTS ix_meeting_tasks_m ON meeting_tasks(meeting_id);
CREATE INDEX IF NOT EXISTS ix_meeting_tasks_t ON meeting_tasks(task_id);
CREATE INDEX IF NOT EXISTS ix_tasks_proj ON tasks(project_id);
CREATE INDEX IF NOT EXISTS ix_tasks_assignee ON tasks(assignee);
CREATE TABLE IF NOT EXISTS docs(
  id TEXT PRIMARY KEY, code TEXT UNIQUE NOT NULL, title TEXT NOT NULL, type TEXT NOT NULL,
  project_id TEXT REFERENCES projects(id) ON DELETE CASCADE, task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
  risk_id TEXT REFERENCES risks(id) ON DELETE SET NULL, status TEXT NOT NULL,
  author TEXT REFERENCES users(id) ON DELETE SET NULL);
CREATE TABLE IF NOT EXISTS doc_versions(
  doc_id TEXT NOT NULL REFERENCES docs(id) ON DELETE CASCADE, v INT NOT NULL, ts BIGINT NOT NULL,
  by_user TEXT, size BIGINT NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '',
  file_name TEXT NOT NULL, stored BOOLEAN NOT NULL DEFAULT FALSE, PRIMARY KEY(doc_id, v));
CREATE TABLE IF NOT EXISTS calcs(
  id TEXT PRIMARY KEY, code TEXT UNIQUE NOT NULL, title TEXT NOT NULL, model TEXT NOT NULL, ver TEXT NOT NULL,
  project_id TEXT REFERENCES projects(id) ON DELETE CASCADE, task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
  by_user TEXT, ts BIGINT NOT NULL, status TEXT NOT NULL, verified_by TEXT, verified_at BIGINT,
  inputs JSONB NOT NULL, outputs JSONB NOT NULL, runs JSONB NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS time_entries(
  id TEXT PRIMARY KEY, user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL, project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
  date TEXT NOT NULL, hours REAL NOT NULL, act TEXT NOT NULL, comment TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL, approved_by TEXT);
CREATE INDEX IF NOT EXISTS ix_time_user_date ON time_entries(user_id, date);
CREATE TABLE IF NOT EXISTS cal_events(
  id TEXT PRIMARY KEY, title TEXT NOT NULL, date TEXT NOT NULL, mtime TEXT NOT NULL DEFAULT '',
  type TEXT NOT NULL DEFAULT 'Событие', participants JSONB NOT NULL DEFAULT '[]', note TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS reports(
  id TEXT PRIMARY KEY, code TEXT UNIQUE NOT NULL, type TEXT NOT NULL, title TEXT NOT NULL,
  params JSONB NOT NULL DEFAULT '{}', ts BIGINT NOT NULL, by_user TEXT, status TEXT NOT NULL, html TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(
  id TEXT PRIMARY KEY, ts BIGINT NOT NULL, cat TEXT NOT NULL, level TEXT NOT NULL,
  user_id TEXT NOT NULL, module TEXT NOT NULL, action TEXT NOT NULL,
  obj TEXT NOT NULL DEFAULT '', obj_id TEXT NOT NULL DEFAULT '', details JSONB NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts DESC);
CREATE TABLE IF NOT EXISTS settings(id INT PRIMARY KEY DEFAULT 1, data JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS notifications(
  id TEXT PRIMARY KEY, ts BIGINT NOT NULL, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type TEXT NOT NULL, module TEXT NOT NULL, obj TEXT NOT NULL DEFAULT '', obj_id TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', channel TEXT NOT NULL DEFAULT 'web',
  status TEXT NOT NULL DEFAULT 'sent', error TEXT NOT NULL DEFAULT '', read_at BIGINT,
  dedupe TEXT NOT NULL UNIQUE);
CREATE INDEX IF NOT EXISTS ix_notif_user ON notifications(user_id, ts DESC);
CREATE TABLE IF NOT EXISTS reminders(
  id TEXT PRIMARY KEY, owner_type TEXT NOT NULL, owner_id TEXT NOT NULL, user_id TEXT NOT NULL,
  offset_min INT NOT NULL, fire_at BIGINT NOT NULL, sent_at BIGINT, key TEXT NOT NULL UNIQUE,
  created_by TEXT, created_at BIGINT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_reminders_fire ON reminders(fire_at) WHERE sent_at IS NULL;
"""
# kind -> (table, [(jsKey, column, type)])  type: t=text i=int f=real j=jsonb b=bool
ENT = {
 "project": ("projects", [("id","id","t"),("code","code","t"),("name","name","t"),("cat","cat","t"),("status","status","t"),
   ("pm","pm","t"),("start","start","t"),("end",'"end"',"t"),("budgetHours","budget_hours","i"),("team","team","j"),
   ("desc","descr","t"),("baseline","baseline","j"),("baselineAt","baseline_at","i"),("createdAt","created_at","i")]),
 "task": ("tasks", [("id","id","t"),("code","code","t"),("projectId","project_id","t"),("parent","parent","t"),
   ("title","title","t"),("type","type","t"),("status","status","t"),("priority","priority","t"),("assignee","assignee","t"),
   ("start","start","t"),("end",'"end"',"t"),("planHours","plan_hours","i"),("progress","progress","i"),
   ("result","result","t"),("blockReason","block_reason","t"),("deps","deps","j"),("comments","comments","j"),
   ("desc","descr","t"),("createdAt","created_at","i"),("createdBy","created_by","t"),("meetingId","meeting_id","t"),("riskId","risk_id","t"),
   ("directive","directive","j")]),
 "risk": ("risks", [("id","id","t"),("code","code","t"),("title","title","t"),("cat","cat","t"),("projectId","project_id","t"),
   ("p","p","i"),("i","i","i"),("strategy","strategy","t"),("owner","owner","t"),("status","status","t"),("review","review","t"),
   ("residual","residual","j"),("desc","descr","t"),("measures","measures","j"),("history","history","j"),("events","risk_events","j")]),
 "doc": ("docs", [("id","id","t"),("code","code","t"),("title","title","t"),("type","type","t"),("projectId","project_id","t"),
   ("taskId","task_id","t"),("riskId","risk_id","t"),("status","status","t"),("author","author","t")]),
 "calc": ("calcs", [("id","id","t"),("code","code","t"),("title","title","t"),("model","model","t"),("ver","ver","t"),
   ("projectId","project_id","t"),("taskId","task_id","t"),("by","by_user","t"),("ts","ts","i"),("status","status","t"),
   ("verifiedBy","verified_by","t"),("verifiedAt","verified_at","i"),("inputs","inputs","j"),("outputs","outputs","j"),("runs","runs","j")]),
 "time": ("time_entries", [("id","id","t"),("userId","user_id","t"),("taskId","task_id","t"),("projectId","project_id","t"),
   ("date","date","t"),("hours","hours","f"),("act","act","t"),("comment","comment","t"),("status","status","t"),("approvedBy","approved_by","t")]),
 "meeting": ("meetings", [("id","id","t"),("code","code","t"),("title","title","t"),("type","type","t"),("date","date","t"),
   ("time","mtime","t"),("chair","chair","t"),("secretary","secretary","t"),("attendees","attendees","j"),
   ("status","status","t"),("agenda","agenda","j"),("decisions","decisions","j"),("protocol","protocol","j"),
   ("materials","materials","j"),("projects","projects","j"),("recurrence","recurrence","j"),
   ("planLog","plan_log","j"),("seriesId","series_id","t")]),
 "calEvent": ("cal_events", [("id","id","t"),("title","title","t"),("date","date","t"),("time","mtime","t"),
   ("type","type","t"),("participants","participants","j"),("note","note","t")]),
 "report": ("reports", [("id","id","t"),("code","code","t"),("type","type","t"),("title","title","t"),("params","params","j"),
   ("ts","ts","i"),("by","by_user","t"),("status","status","t"),("html","html","t")]),
}
STATE_KEYS = {"project":"projects","task":"tasks","risk":"risks","doc":"docs","calc":"calcs","time":"time",
              "meeting":"meetings","calEvent":"calEvents","report":"reports"}

class DB:
    def __init__(self, dsn):
        self.pool = psycopg2.pool.ThreadedConnectionPool(1, 12, dsn)
        self.wlock = threading.Lock()
        conn = self.get()
        try:
            with conn.cursor() as cur:
                cur.execute(SCHEMA)
                cur.execute("INSERT INTO meta(k,v) VALUES('rev','1') ON CONFLICT DO NOTHING")
                cur.execute("INSERT INTO meta(k,v) VALUES('schema',%s) ON CONFLICT DO NOTHING", (str(SCHEMA_VERSION),))
            conn.commit()
        finally: self.put(conn)
    def get(self): return self.pool.getconn()
    def put(self, c): self.pool.putconn(c)
    def row2obj(self, kind, row):
        _, fields = ENT[kind]
        o = {}
        for js, col, ty in fields:
            o[js] = row[col.strip('"')]
        return o
    def obj2row(self, kind, o):
        _, fields = ENT[kind]
        vals = {}
        for js, col, ty in fields:
            v = o.get(js)
            if ty == "t" and v is None and js in ("seriesId",): v = ""   # записи прежних версий
            if ty == "j": v = psycopg2.extras.Json(v if v is not None else ([] if js in ("team","deps","comments","measures","history","events","attendees","agenda","decisions","participants","runs",
                                                    "materials","projects","planLog") else {}))
            vals[col.strip('"')] = v
        return vals
    def get_ent(self, cur, kind, id_):
        table, _ = ENT[kind]
        cur.execute(f"SELECT * FROM {table} WHERE id=%s", (id_,))
        r = cur.fetchone()
        if not r: return None
        o = self.row2obj(kind, r)
        if kind == "doc":
            cur.execute("SELECT v,ts,by_user,size,note,file_name,stored FROM doc_versions WHERE doc_id=%s ORDER BY v", (id_,))
            o["versions"] = [{"v":x["v"],"ts":x["ts"],"by":x["by_user"],"size":x["size"],"note":x["note"],"name":x["file_name"],"stored":x["stored"]} for x in cur.fetchall()]
        return o
    def all_ent(self, cur, kind, order="code"):
        table, _ = ENT[kind]
        oc = order if order in [c.strip('"') for _,c,_ in ENT[kind][1]] else "id"
        cur.execute(f'SELECT * FROM {table} ORDER BY {oc}')
        rows = [self.row2obj(kind, r) for r in cur.fetchall()]
        if kind == "doc":
            cur.execute("SELECT doc_id,v,ts,by_user,size,note,file_name,stored FROM doc_versions ORDER BY doc_id,v")
            vs = {}
            for x in cur.fetchall():
                vs.setdefault(x["doc_id"], []).append({"v":x["v"],"ts":x["ts"],"by":x["by_user"],"size":x["size"],"note":x["note"],"name":x["file_name"],"stored":x["stored"]})
            for o in rows: o["versions"] = vs.get(o["id"], [])
        return rows
    def save_ent(self, cur, kind, o):
        table, fields = ENT[kind]
        vals = self.obj2row(kind, o)
        cols = [c for _,c,_ in fields]
        names = [c.strip('"') for c in cols]
        quoted = ", ".join(cols)
        ph = ", ".join(["%s"]*len(cols))
        upd = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols if c != "id")
        cur.execute(f"INSERT INTO {table}({quoted}) VALUES({ph}) ON CONFLICT (id) DO UPDATE SET {upd}",
                    [vals[n] for n in names])
    def del_ent(self, cur, kind, id_):
        table, _ = ENT[kind]
        cur.execute(f"DELETE FROM {table} WHERE id=%s", (id_,))
    def settings(self, cur):
        cur.execute("SELECT data FROM settings WHERE id=1")
        r = cur.fetchone()
        return r["data"] if r else None
    def save_settings(self, cur, s):
        cur.execute("INSERT INTO settings(id,data) VALUES(1,%s) ON CONFLICT (id) DO UPDATE SET data=EXCLUDED.data", (psycopg2.extras.Json(s),))
    def bump(self, cur):
        cur.execute("UPDATE meta SET v=(v::bigint+1)::text WHERE k='rev' RETURNING v")
        return int(cur.fetchone()["v"])
    def rev(self, cur):
        cur.execute("SELECT v FROM meta WHERE k='rev'"); return int(cur.fetchone()["v"])
    def audit(self, cur, cat, level, user_id, module, action, obj="", obj_id="", details=None):
        cur.execute("INSERT INTO events(id,ts,cat,level,user_id,module,action,obj,obj_id,details) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (uid("e"), now_ms(), cat, level, user_id or "system", module, action, obj, obj_id or "", psycopg2.extras.Json(details or {})))
    def users_public(self, cur):
        cur.execute("SELECT id,login,name,role,position,capacity,color,blocked,absences FROM users ORDER BY id")
        return [dict(r) for r in cur.fetchall()]
    def state(self, cur, me_id):
        s = {"v": 3, "rev": self.rev(cur), "me": me_id, "server": {"name": APP_NAME, "version": VERSION, "db": "PostgreSQL"}}
        s["users"] = self.users_public(cur)
        for kind, key in STATE_KEYS.items():
            s[key] = self.all_ent(cur, kind, order="code" if kind not in ("time","report","calEvent") else "id")
        cur.execute("SELECT id,ts,cat,level,user_id,module,action,obj,obj_id,details FROM events ORDER BY ts DESC, id DESC LIMIT 800")
        s["events"] = [{"id":r["id"],"ts":r["ts"],"cat":r["cat"],"level":r["level"],"user":r["user_id"],"module":r["module"],
                        "action":r["action"],"obj":r["obj"],"objId":r["obj_id"],"details":r["details"]} for r in cur.fetchall()]
        s["settings"] = self.settings(cur)
        items, unread = notif_list(self, cur, me_id, 100)
        s["notifications"] = items
        s["notifUnread"] = unread
        cur.execute("SELECT id,owner_type,owner_id,user_id,offset_min,fire_at,sent_at FROM reminders ORDER BY fire_at")
        s["reminders"] = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT id,meeting_id,decision_id,task_id,ts,by_user FROM meeting_tasks ORDER BY ts")
        s["meetingTasks"] = [dict(r) for r in cur.fetchall()]
        return s

# ---------------- Демо-данные (эталонный набор) ----------------
def seed(db):
    conn = db.get()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT COUNT(*) AS n FROM users")
            if cur.fetchone()["n"] > 0: return False
            now = now_ms()
            users = [
                ("u1","admin","Соколов Артём","admin","Инженер-администратор",8,"#7c3aed"),
                ("u2","morozov","Морозов Дмитрий","head","Начальник инженерного подразделения",8,"#0e7490"),
                ("u3","volkova","Волкова Елена","pm","Руководитель проектов",8,"#2563eb"),
                ("u4","lebedev","Лебедев Игорь","pm","Руководитель проектов",8,"#c026d3"),
                ("u5","novikov","Новиков Сергей","exec","Инженер-конструктор",8,"#16a34a"),
                ("u6","kozlova","Козлова Мария","exec","Инженер-расчётчик",8,"#ea580c"),
                ("u7","orlov","Орлов Павел","exec","Инженер-испытатель",8,"#0891b2"),
                ("u8","zaytseva","Зайцева Нина","observer","Специалист по качеству",8,"#64748b"),
            ]
            for id_,login,name,role,pos,cap,color in users:
                salt,h = pass_hash("demo")
                abs_ = [{"from":D(6),"to":D(10),"reason":"Отпуск"}] if id_=="u7" else []
                cur.execute("INSERT INTO users(id,login,name,role,position,capacity,color,pass_salt,pass_hash,absences) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                            (id_,login,name,role,pos,cap,color,salt,h,psycopg2.extras.Json(abs_)))
            projects = [
                dict(id="p1",code="П-2026-01",name="Модернизация радиорелейной линии РРЛ-240",cat="Модернизация",status="Выполняется",pm="u3",start=D(-45),end=D(40),budgetHours=1200,team=["u3","u5","u6","u7"],desc="Модернизация действующей радиорелейной линии: замена приёмопередатчиков, пересчёт бюджета радиолинии, пусконаладка и приёмочные испытания.",baseline={},baselineAt=None,createdAt=now-45*DAY*1000),
                dict(id="p2",code="П-2026-02",name="Система мониторинга технологических параметров СМТП-3",cat="Разработка",status="Выполняется",pm="u4",start=D(-60),end=D(25),budgetHours=1600,team=["u4","u5","u6"],desc="Разработка аппаратно-программного комплекса сбора и визуализации технологических параметров производства.",baseline={},baselineAt=None,createdAt=now-60*DAY*1000),
                dict(id="p3",code="П-2026-03",name="Модуль помехозащищённого приёма МПП-7",cat="НИОКР",status="Требует внимания",pm="u3",start=D(-80),end=D(15),budgetHours=2000,team=["u3","u6","u7"],desc="НИОКР по созданию модуля адаптивной помехозащиты приёмного тракта. Выявлены отклонения по срокам алгоритмических работ.",baseline={},baselineAt=None,createdAt=now-80*DAY*1000),
                dict(id="p4",code="П-2026-04",name="Автоматизация контроля испытаний АКИ-12",cat="Автоматизация",status="Планирование",pm="u4",start=D(-10),end=D(90),budgetHours=900,team=["u4","u5"],desc="Автоматизация стендов контроля и испытаний: сбор измерений, протоколирование, интеграция с Excel.",baseline={},baselineAt=None,createdAt=now-10*DAY*1000),
                dict(id="p5",code="П-2026-05",name="Мобильный узел связи МУС-5",cat="Разработка",status="Инициатива",pm="u3",start=D(14),end=D(150),budgetHours=2400,team=["u3"],desc="Инициативная проработка мобильного узла связи на базе унифицированных модулей.",baseline={},baselineAt=None,createdAt=now-4*DAY*1000),
                dict(id="p6",code="П-2025-07",name="Модернизация АРМ диспетчера",cat="Модернизация",status="Завершён",pm="u4",start=D(-160),end=D(-20),budgetHours=800,team=["u4","u5"],desc="Проект завершён: АРМ модернизированы, документация передана, акт подписан.",baseline={},baselineAt=None,createdAt=now-160*DAY*1000),
            ]
            projects[0]["baseline"]={"t3":{"start":D(-27),"end":D(-2)},"t4":{"start":D(-1),"end":D(8)},"t5":{"start":D(9),"end":D(22)},"t6":{"start":D(24),"end":D(24)}}
            projects[0]["baselineAt"]=now-12*DAY*1000
            projects[2]["baseline"]={"t16":{"start":D(-50),"end":D(-12)},"t17":{"start":D(-45),"end":D(-6)},"t18":{"start":D(-4),"end":D(5)},"t19":{"start":D(-10),"end":D(-10)}}
            projects[2]["baselineAt"]=now-20*DAY*1000
            for p in projects: db.save_ent(cur,"project",p)
            tn=[0]
            def T(**kw):
                tn[0]+=1
                t=dict(id="t%d"%tn[0],code="З-%03d"%tn[0],parent=None,deps=[],comments=[],result="",blockReason="",progress=0,
                       createdAt=now-20*DAY*1000,createdBy="u3",meetingId=None,riskId=None,type="task",desc="")
                t.update(kw); return t
            tasks=[
                T(projectId="p1",title="Обследование трассы РРЛ-240",status="Закрыта",priority="Высокий",assignee="u5",start=D(-45),end=D(-36),planHours=60,progress=100,result="Акт обследования №14 подписан"),
                T(projectId="p1",title="Разработка ТЗ на модернизацию",status="Принята",priority="Критический",assignee="u3",start=D(-35),end=D(-28),planHours=80,progress=100,result="ТЗ согласовано с заказчиком",deps=[{"id":"t1","type":"FS","lag":0}]),
                T(projectId="p1",title="Расчёт бюджета модернизированной радиолинии",status="В работе",priority="Высокий",assignee="u6",start=D(-27),end=D(4),planHours=64,progress=65,deps=[{"id":"t2","type":"FS","lag":0}],comments=[{"user":"u3","ts":now-3*DAY*1000,"text":"Мария, к пятнице нужен предварительный запас по чувствительности для совещания."},{"user":"u6","ts":now-2*DAY*1000,"text":"Расчёт по модели v2.1 выполнен, запас 12,3 дБ. Проверяю вариант с дождевыми замираниями."}]),
                T(projectId="p1",title="Выбор и заказ приёмопередатчиков",status="Готова к выполнению",priority="Высокий",assignee="u5",start=D(5),end=D(14),planHours=48,deps=[{"id":"t3","type":"FS","lag":0}]),
                T(projectId="p1",title="Монтаж и пусконаладка оборудования",status="Запланирована",priority="Средний",assignee="u7",start=D(15),end=D(28),planHours=120,deps=[{"id":"t4","type":"FS","lag":0}]),
                T(projectId="p1",title="Приёмочные испытания РРЛ-240",type="milestone",status="Запланирована",priority="Критический",assignee="u3",start=D(30),end=D(30),planHours=0,deps=[{"id":"t5","type":"FS","lag":0}]),
                T(projectId="p1",title="Комплект эксплуатационной документации",status="Черновик",priority="Низкий",assignee="u5",start=D(20),end=D(38),planHours=64),
                T(projectId="p1",title="Утверждение ТЗ с заказчиком",type="milestone",status="Закрыта",priority="Критический",assignee="u3",start=D(-28),end=D(-28),planHours=0,result="Протокол согласования №3"),
                T(projectId="p2",title="Схема структурная комплекса СМТП-3",status="Принята",priority="Высокий",assignee="u5",start=D(-60),end=D(-42),planHours=110,progress=100,result="Схема утверждена, выпущена КД"),
                T(projectId="p2",title="Разработка протокола обмена с контроллерами",status="В работе",priority="Высокий",assignee="u5",start=D(-40),end=D(6),planHours=80,progress=70,deps=[{"id":"t9","type":"FS","lag":0}]),
                T(projectId="p2",title="ПО верхнего уровня: сбор и архивирование",status="В работе",priority="Средний",assignee="u6",start=D(-30),end=D(12),planHours=120,progress=45,comments=[{"user":"u4","ts":now-1*DAY*1000,"text":"Прошу выделить модуль экспорта в Excel отдельной задачей."}]),
                T(projectId="p2",title="Интеграционные испытания с OPC-сервером",status="Готова к выполнению",priority="Высокий",assignee="u6",start=D(13),end=D(20),planHours=80,deps=[{"id":"t10","type":"FS","lag":0},{"id":"t11","type":"FS","lag":0}]),
                T(projectId="p2",title="Промежуточная демонстрация заказчику",type="milestone",status="Запланирована",priority="Критический",assignee="u4",start=D(7),end=D(7),planHours=0),
                T(projectId="p2",title="Руководство оператора",status="Черновик",priority="Низкий",assignee="u5",start=D(14),end=D(24),planHours=40),
                T(projectId="p3",title="Аванпроект МПП-7: выбор архитектуры",status="Принята",priority="Критический",assignee="u3",start=D(-80),end=D(-55),planHours=160,progress=100,result="Аванпроект защищён на НТС"),
                T(projectId="p3",title="Разработка алгоритма адаптивной фильтрации",status="В работе",priority="Критический",assignee="u6",start=D(-50),end=D(-5),planHours=260,progress=80,deps=[{"id":"t15","type":"FS","lag":0}],comments=[{"user":"u6","ts":now-4*DAY*1000,"text":"Сходимость алгоритма на реальной записи неустойчива, нужен дополнительный цикл отладки."},{"user":"u3","ts":now-4*DAY*1000,"text":"Зафиксировала отклонение. Выносим на технический совет."}]),
                T(projectId="p3",title="Макетирование приёмного тракта",status="На проверке",priority="Высокий",assignee="u7",start=D(-45),end=D(-2),planHours=180,progress=90,result="Макет собран, протокол измерений приложен к расчёту",deps=[{"id":"t15","type":"SS","lag":5}]),
                T(projectId="p3",title="Климатические испытания макета",status="Заблокирована",priority="Высокий",assignee="u7",start=D(1),end=D(10),planHours=48,blockReason="Камера тепла/холода занята до конца недели",deps=[{"id":"t17","type":"FS","lag":0}]),
                T(projectId="p3",title="Защита аванпроекта (контрольная точка)",type="milestone",status="Запланирована",priority="Критический",assignee="u3",start=D(-3),end=D(-3),planHours=0),
                T(projectId="p3",title="Корректировка ТЗ по результатам моделирования",status="Запланирована",priority="Средний",assignee="u6",start=D(2),end=D(12),planHours=32,deps=[{"id":"t16","type":"FS","lag":0}]),
                T(projectId="p4",title="Обследование стендов и сбор требований",status="Готова к выполнению",priority="Высокий",assignee="u5",start=D(-5),end=D(8),planHours=24),
                T(projectId="p4",title="Техническое предложение по АКИ-12",status="Запланирована",priority="Средний",assignee="u4",start=D(9),end=D(25),planHours=100,deps=[{"id":"t21","type":"FS","lag":0}]),
                T(projectId="p4",title="Оценка трудоёмкости и стоимости",status="Новая",priority="Средний",assignee="u4",start=D(10),end=D(18),planHours=40),
                T(projectId="p5",title="Проработка облика МУС-5",status="Новая",priority="Средний",assignee="u3",start=D(14),end=D(35),planHours=120),
                T(projectId="p6",title="Модернизация рабочих мест (12 АРМ)",status="Закрыта",priority="Высокий",assignee="u5",start=D(-160),end=D(-40),planHours=420,progress=100,result="Все АРМ модернизированы"),
                T(projectId="p6",title="Обучение диспетчерского состава",status="Закрыта",priority="Средний",assignee="u4",start=D(-38),end=D(-22),planHours=60,progress=100,result="Проведено 4 группы, ведомости подписаны"),
                T(projectId="p1",title="Подготовить сравнительную таблицу поставщиков ППМ",status="В работе",priority="Высокий",assignee="u5",start=D(-7),end=D(2),planHours=16,progress=50,type="directive",meetingId="m1",
                  desc="Поручение по решению совещания СОВ-2026-041.\nФормулировка решения: подготовить сравнительную таблицу поставщиков ППМ к техническому совету.",
                  directive={"source":"Решение №1 совещания СОВ-2026-041 · пункт 3 повестки","criteria":"Таблица содержит не менее 3 поставщиков, сроки, цены и риски поставки; согласована с РП.",
                             "coassignees":["u3"],"postedAt":now-7*DAY*1000,"postedBy":"u2","postedDate":D(-7),
                             "meetingId":"m1","meetingCode":"СОВ-2026-041","decisionId":"md1","decisionN":1,"agendaN":3,
                             "protocolV":1,"protocolDoc":None,"dueHistory":[],"acceptance":None,"docs":[],"factDate":None,"comment":""}),
                T(projectId="p3",title="Уточнить требования по ЭМС для МПП-7",status="Готова к выполнению",priority="Высокий",assignee="u6",start=D(-7),end=D(-1),planHours=12,type="directive",meetingId="m1",
                  desc="Поручение по решению совещания СОВ-2026-041.\nФормулировка решения: уточнить требования по ЭМС для МПП-7 и направить в НИИ-экспертизы.",
                  directive={"source":"Решение №2 совещания СОВ-2026-041 · пункт 2 повестки","criteria":"Получены письменные требования по ЭМС, направлены в НИИ-экспертизы.",
                             "coassignees":[],"postedAt":now-7*DAY*1000,"postedBy":"u2","postedDate":D(-7),
                             "meetingId":"m1","meetingCode":"СОВ-2026-041","decisionId":"md2","decisionN":2,"agendaN":2,
                             "protocolV":1,"protocolDoc":None,"dueHistory":[],"acceptance":None,"docs":[],"factDate":None,"comment":""}),
            ]
            meetings=[
                dict(id="m1",code="СОВ-2026-041",title="Еженедельное планирование подразделения",type="Оперативное",date=D(-7),time="10:00",chair="u2",secretary="u3",attendees=["u2","u3","u4","u5","u6"],status="Протокол утверждён",
                     agenda=[{"n":1,"title":"Статус проектов портфеля","reporter":"u3"},{"n":2,"title":"Отклонение по алгоритму МПП-7","reporter":"u6"},{"n":3,"title":"Загрузка на ближайшие 2 недели","reporter":"u2"}],
                     decisions=[{"id":"md1","text":"Подготовить сравнительную таблицу поставщиков ППМ к техническому совету","resp":"u5","due":D(2),"taskId":"t27","taskIds":["t27"],"agendaN":3},{"id":"md2","text":"Уточнить требования по ЭМС для МПП-7 и направить в НИИ-экспертизы","resp":"u6","due":D(-1),"taskId":"t28","taskIds":["t28"],"agendaN":2}]),
                dict(id="m2",code="СОВ-2026-042",title="Технический совет по МПП-7",type="Техническое",date=D(2),time="14:00",chair="u2",secretary="u3",attendees=["u2","u3","u6","u7"],status="Запланировано",
                     agenda=[{"n":1,"title":"Результаты моделирования адаптивной фильтрации","reporter":"u6"},{"n":2,"title":"Меры по риску Р-02","reporter":"u3"},{"n":3,"title":"Перенос климатических испытаний","reporter":"u7"}],decisions=[]),
                dict(id="m3",code="СОВ-2026-043",title="Приёмка этапа СМТП-3",type="Приёмочное",date=D(7),time="11:00",chair="u2",secretary="u4",attendees=["u2","u4","u5","u6","u8"],status="Запланировано",
                     agenda=[{"n":1,"title":"Демонстрация сбора и архивирования данных","reporter":"u5"},{"n":2,"title":"Протокол обмена: статус согласования","reporter":"u5"}],decisions=[]),
            ]
            # §2.5: связанные проекты, предварительные материалы и история планирования
            for _m, _prj, _mats, _log in (
                (meetings[0], ["p1","p3"],
                 [{"id":"mm1","kind":"doc","docId":"d3","name":"Д-003 · Расчётная записка: бюджет радиолинии","url":"","note":"К вопросу 2","addedAt":now-8*DAY*1000,"addedBy":"u3"},
                  {"id":"mm2","kind":"link","docId":None,"name":"Сводка загрузки подразделения","url":"\\\\srv-files\\plan\\svodka.xlsx","note":"Обновляется еженедельно","addedAt":now-8*DAY*1000,"addedBy":"u3"}],
                 [{"ts":now-9*DAY*1000,"by":"u2","action":"move","from":{"date":D(-9),"time":"10:00"},"to":{"date":D(-7),"time":"10:00"},"reason":"Перенос по просьбе председателя"}]),
                (meetings[1], ["p3"],
                 [{"id":"mm3","kind":"doc","docId":"d6","name":"Д-006 · Аванпроект МПП-7 (пояснительная записка)","url":"","note":"Материал к вопросу 1","addedAt":now-2*DAY*1000,"addedBy":"u3"}], []),
                (meetings[2], ["p2"],
                 [{"id":"mm4","kind":"doc","docId":"d4","name":"Д-004 · Схема структурная СМТП-3","url":"","note":"Демонстрация на приёмке","addedAt":now-1*DAY*1000,"addedBy":"u4"}], []),
            ):
                _m["projects"] = _prj; _m["materials"] = _mats; _m["planLog"] = _log
            for m in meetings: db.save_ent(cur,"meeting",m)
            for t in tasks: db.save_ent(cur,"task",t)
            # связи решений с поручениями (техпроект §4.1: meeting_tasks — связь, а не параллельная система задач)
            for m in meetings:
                for d in m.get("decisions") or []:
                    for tid in dec_task_ids(d):
                        cur.execute("INSERT INTO meeting_tasks(id,meeting_id,decision_id,task_id,ts,by_user) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                                    (uid("mt"), m["id"], d["id"], tid, now-7*DAY*1000, "u2"))
            risks=[
                dict(id="r1",code="Р-01",title="Срыв поставки радиомодулей основным поставщиком",cat="Снабжение",projectId="p1",p=4,i=5,strategy="Снижение",owner="u3",status="В обработке",review=D(7),residual={"p":3,"i":4},desc="Единственный поставщик ППМ подтверждает срок поставки с задержкой до 6 недель.",measures=[{"id":"rm1","title":"Провести квалификацию альтернативного поставщика","assignee":"u5","due":D(5),"done":False},{"id":"rm2","title":"Включить штрафные санкции в договор","assignee":"u3","due":D(-2),"done":True}],history=[{"ts":now-18*DAY*1000,"p":3,"i":5,"by":"u3"},{"ts":now-6*DAY*1000,"p":4,"i":5,"by":"u3"}],events=[]),
                dict(id="r2",code="Р-02",title="Недостижение требуемой чувствительности приёмного тракта",cat="Технические",projectId="p3",p=3,i=5,strategy="Снижение",owner="u6",status="В обработке",review=D(3),residual={"p":2,"i":5},desc="Моделирование показывает дефицит запаса по чувствительности при сильных помехах.",measures=[{"id":"rm3","title":"Отладка алгоритма адаптивной фильтрации на реальных записях","assignee":"u6","due":D(4),"done":False}],history=[{"ts":now-10*DAY*1000,"p":3,"i":5,"by":"u6"}],events=[{"ts":now-4*DAY*1000,"text":"На макете подтверждена неустойчивая сходимость алгоритма"}]),
                dict(id="r3",code="Р-03",title="Недостаточная точность модели распространения радиоволн",cat="Технические",projectId="p1",p=3,i=3,strategy="Снижение",owner="u6",status="Открыт",review=D(10),residual=None,desc="Модель не учитывает дифракцию на застройке вблизи трассы.",measures=[],history=[{"ts":now-9*DAY*1000,"p":3,"i":3,"by":"u6"}],events=[]),
                dict(id="r4",code="Р-04",title="Перегрузка инженера-испытателя в период приёмочных работ",cat="Ресурсные",projectId="p1",p=4,i=3,strategy="Снижение",owner="u2",status="В обработке",review=D(5),residual={"p":2,"i":3},desc="Пик испытаний РРЛ-240 совпадает с климатическими испытаниями МПП-7 и отпуском исполнителя.",measures=[{"id":"rm4","title":"Согласовать перенос климатических испытаний","assignee":"u3","due":D(2),"done":False}],history=[{"ts":now-7*DAY*1000,"p":4,"i":3,"by":"u2"}],events=[]),
                dict(id="r5",code="Р-05",title="Изменение требований заказчика к составу СМТП-3",cat="Организационные",projectId="p2",p=3,i=4,strategy="Принятие",owner="u4",status="Открыт",review=D(14),residual=None,desc="Заказчик анонсировал расширение перечня контролируемых параметров.",measures=[],history=[{"ts":now-12*DAY*1000,"p":3,"i":4,"by":"u4"}],events=[]),
                dict(id="r6",code="Р-06",title="Отказ оборудования при климатических испытаниях",cat="Технические",projectId="p3",p=2,i=4,strategy="Снижение",owner="u7",status="Открыт",review=D(8),residual=None,desc="Комплектующие индустриального диапазона могут не пройти испытания при −40 °C.",measures=[],history=[{"ts":now-8*DAY*1000,"p":2,"i":4,"by":"u7"}],events=[]),
                dict(id="r7",code="Р-07",title="Уязвимость протокола обмена при внешнем доступе",cat="Информационная безопасность",projectId="p2",p=2,i=4,strategy="Передача",owner="u5",status="В обработке",review=D(20),residual={"p":1,"i":4},desc="Штатный протокол контроллеров не имеет аутентификации.",measures=[{"id":"rm5","title":"Передать сегмент сбора в защищённый контур (зона ответственности ИТ-службы)","assignee":"u4","due":D(15),"done":False}],history=[{"ts":now-15*DAY*1000,"p":2,"i":4,"by":"u5"}],events=[]),
                dict(id="r8",code="Р-08",title="Задержка согласования конструкторской документации",cat="Организационные",projectId="p1",p=2,i=3,strategy="Снижение",owner="u3",status="Закрыт",review=D(-5),residual={"p":1,"i":2},desc="Риск снят: согласование КД проведено в нормативный срок.",measures=[{"id":"rm6","title":"Предварительное ревью КД до официальной подачи","assignee":"u3","due":D(-8),"done":True}],history=[{"ts":now-25*DAY*1000,"p":3,"i":3,"by":"u3"},{"ts":now-5*DAY*1000,"p":1,"i":2,"by":"u3"}],events=[]),
            ]
            for r in risks: db.save_ent(cur,"risk",r)
            docs=[
                ("d1","Д-001","ТЗ на модернизацию РРЛ-240","Техническое задание","p1","t2",None,"Утверждён","u3",[(1,-34,412000,"Первая редакция","TZ_RRL240_v1.docx"),(2,-30,436000,"Замечания заказчика учтены","TZ_RRL240_v2.docx"),(3,-28,441000,"Утверждённая редакция","TZ_RRL240_v3.docx")]),
                ("d2","Д-002","Программа и методика приёмочных испытаний","Программа испытаний","p1","t6",None,"На согласовании","u7",[(1,-9,280000,"Проект ПМИ","PMI_RRL240_v1.docx"),(2,-2,295000,"Добавлены проверки на замирания","PMI_RRL240_v2.docx")]),
                ("d3","Д-003","Расчётная записка: бюджет радиолинии","Расчётная записка","p1","t3","r3","Утверждён","u6",[(1,-3,150000,"По результатам расчёта Р-2026-014","RZ_raschet_radiolinii.pdf")]),
                ("d4","Д-004","Схема структурная СМТП-3","Конструкторская документация","p2","t9",None,"Утверждён","u5",[(1,-44,520000,"Выпущена по результатам этапа","SMTP3_shema_strukturnaya.pdf")]),
                ("d5","Д-005","Описание протокола обмена с контроллерами","Техническое описание","p2","t10","r7","На согласовании","u5",[(1,-5,190000,"Включён раздел по защищённому контуру","Protokol_obmena_v1.docx")]),
                ("d6","Д-006","Аванпроект МПП-7 (пояснительная записка)","Пояснительная записка","p3","t15","r2","Устарел","u3",[(1,-70,890000,"Редакция к защите аванпроекта","Avanproekt_MPP7_v1.pdf"),(2,-55,905000,"Замечания НТС","Avanproekt_MPP7_v2.pdf")]),
                ("d7","Д-007","Акт обследования трассы РРЛ-240 №14","Акт","p1","t1",None,"Утверждён","u5",[(1,-36,240000,"Подписан комиссией","Akt_obsledovaniya_14.pdf")]),
                ("d8","Д-008","Руководство оператора АРМ диспетчера","Эксплуатационная документация","p6",None,None,"Утверждён","u4",[(1,-24,610000,"Передано заказчику","RO_ARM_dispetchera.pdf")]),
                ("d9","Д-009","Перечень покупных изделий АКИ-12","Перечень","p4","t22",None,"Черновик","u5",[(1,-1,95000,"Предварительный состав","Perechen_AKI12.xlsx")]),
            ]
            for id_,code,title,typ,pid,tid,rid,st,author,vers in docs:
                db.save_ent(cur,"doc",dict(id=id_,code=code,title=title,type=typ,projectId=pid,taskId=tid,riskId=rid,status=st,author=author))
                for v,off,size,note,name in vers:
                    cur.execute("INSERT INTO doc_versions(doc_id,v,ts,by_user,size,note,file_name,stored) VALUES(%s,%s,%s,%s,%s,%s,%s,FALSE)",
                                (id_,v,now+off*DAY*1000,author,size,note,name))
            calcs=[
                dict(id="c1",code="Р-2026-014",title="Бюджет радиолинии РРЛ-240 (пролёт 25 км)",model="link",ver="2.1",projectId="p1",taskId="t3",by="u6",ts=now-3*DAY*1000,status="Проверен",verifiedBy="u3",verifiedAt=now-2*DAY*1000,inputs={"f":13000,"d":25,"ptx":25,"gtx":30,"grx":30,"lmisc":8,"sens":-78}),
                dict(id="c2",code="Р-2026-017",title="Надёжность приёмного тракта МПП-7",model="reliability",ver="1.0",projectId="p3",taskId="t17",by="u6",ts=now-2*DAY*1000,status="Выполнен",verifiedBy=None,verifiedAt=None,inputs={"n1":120,"l1":0.12,"n2":24,"l2":0.85,"n3":6,"l3":4.2,"t":720,"mttr":2}),
                dict(id="c3",code="Р-2026-019",title="Трудоёмкость ОКР по АКИ-12",model="labor",ver="2.0",projectId="p4",taskId="t23",by="u4",ts=now-1*DAY*1000,status="Выполнен",verifiedBy=None,verifiedAt=None,inputs={"base":1600,"k":1.25,"team":4,"fund":164}),
            ]
            for c in calcs:
                c["outputs"]=CALC_MODELS[c["model"]]["run"](c["inputs"])
                c["runs"]=[{"ts":c["ts"],"by":c["by"],"inputs":dict(c["inputs"]),"ok":c["outputs"]["ok"]}]
                db.save_ent(cur,"calc",c)
            acts=["Расчёты и моделирование","Инженерное проектирование","Разработка технической документации","Испытания","Совещания"]
            tmap={"u5":[("t10","p2",1),("t27","p1",3),("t21","p4",0)],"u6":[("t3","p1",0),("t16","p3",0),("t11","p2",1)],"u7":[("t17","p3",3),("t18","p3",3)]}
            ti=0
            for off in range(-14,0):
                day=D(off)
                if isWk(day): continue
                for u in ("u5","u6","u7"):
                    opts=tmap[u]; pick=opts[(-off)%len(opts)]
                    hrs=[6,4,5,7,3][(-off+ord(u[1]))%5]
                    ti+=1
                    st="Утверждена" if off<-4 else ("Отправлена" if off<-1 else "Черновик")
                    db.save_ent(cur,"time",dict(id="te%d"%ti,userId=u,taskId=pick[0],projectId=pick[1],date=day,hours=hrs,
                                act=acts[(pick[2]+(-off))%len(acts)],comment="",status=st,approvedBy="u2" if off<-4 else None))
            ti+=1
            db.save_ent(cur,"time",dict(id="te%d"%ti,userId="u3",taskId="t27",projectId="p1",date=D(-7),hours=2,act="Совещания",comment="Еженедельное планирование",status="Утверждена",approvedBy="u2"))
            db.save_ent(cur,"calEvent",dict(id="ce1",title="Согласование ТЗ АКИ-12 с заказчиком",date=D(4),time="15:00",type="Событие",participants=["u4","u2"],note="В переговорной №2"))
            db.save_ent(cur,"calEvent",dict(id="ce2",title="Инвентаризация измерительной лаборатории",date=D(12),time="09:00",type="Событие",participants=["u7","u1"],note=""))
            settings={"wip":8,"sessionMin":30,"loadWarn":85,"loadOver":100,
                      "dirControl":json.loads(json.dumps(DIR_CONTROL_DEF)),
                      "dicts":json.loads(json.dumps(DEFAULT_DICTS)),"perms":json.loads(json.dumps(BASE_PERMS)),
                      "widgets":{},"seq":{"proj":6,"task":28,"risk":8,"doc":9,"calc":20,"report":20,"meet":43}}
            db.save_settings(cur,settings)
            # демонстрационный утверждённый протокол (ТЗ §2.9): отдельная редакция с файлами DOCX+PDF
            m1 = next(m for m in meetings if m["id"]=="m1")
            if docgen is not None:
                u_adm = {"id":"u1","login":"admin","name":"Соколов Артём","role":"admin"}
                reg = _protocol_register(db, cur, u_adm, m1, ["docx","pdf"], "Утверждённый протокол (демо-набор)")
                pr = protocol_default()
                pr.update(status="Утверждён", version=1, sentAt=now-7*DAY*1000, sentBy="u3",
                          approvedAt=now-6*DAY*1000, approvedBy="u2",
                          route=[{"user":"u3","role":"Секретарь","state":"Согласовано","ts":now-7*DAY*1000,"comment":""},
                                 {"user":"u2","role":"Председатель","state":"Согласовано","ts":now-6*DAY*1000,"comment":""}],
                          comments=[{"ts":now-7*DAY*1000,"by":"u3","kind":"Направление","text":"Протокол подготовлен секретарём"}])
                pr["versions"] = [{"v":1,"ts":now-6*DAY*1000,"by":"u2","note":"Утверждённый протокол (демо-набор)",
                                   "docId":reg["docId"],"docCode":reg["docCode"],"docV":reg["docV"],"files":reg["files"],
                                   "agenda":m1["agenda"],"decisions":m1["decisions"]}]
                m1["protocol"] = pr
                db.save_ent(cur,"meeting",m1)
                for t in tasks:
                    if t.get("meetingId")=="m1" and t.get("directive"):
                        t["directive"]["protocolDoc"] = {"docId":reg["docId"],"docCode":reg["docCode"],"docV":reg["docV"]}
                        db.save_ent(cur,"task",t)

            seed_events=[
                (-9,"SECURITY","info","u2","admin","Вход в систему","Сессия","",{"ip":"192.168.10.21"}),
                (-8,"BUSINESS","info","u3","portfolio","Создан проект","Проект","p5",{"name":"Мобильный узел связи МУС-5"}),
                (-6,"AUDIT","warning","u3","risks","Изменена оценка риска","Риск","r1",{"before":{"p":3,"i":5},"after":{"p":4,"i":5}}),
                (-5,"BUSINESS","info","u2","meetings","Утверждён протокол совещания","Совещание","m1",{"code":"СОВ-2026-041"}),
                (-4,"TECH","info","u1","admin","Резервное копирование выполнено","БД","",{"size":"42 МБ","duration":"8 с"}),
                (-3,"INTEGRATION","info","u6","calcs","Расчёт выполнен","Расчёт","c1",{"model":"Бюджет радиолинии v2.1"}),
                (-3,"INTEGRATION","info","u6","calcs","Экспорт результатов в Excel","Расчёт","c1",{"format":"CSV (Excel)","file":"R-2026-014.csv"}),
                (-2,"AUDIT","info","u3","calcs","Расчёт проверен","Расчёт","c1",{"verdict":"Проверен"}),
                (-2,"BUSINESS","warning","u4","gantt","Сохранён базовый план","Проект","p1",{"tasks":4}),
                (-1,"SECURITY","warning","u8","tasks","Отказано в операции (недостаточно прав)","Задача","t16",{"op":"edit","role":"observer"}),
                (-1,"BUSINESS","info","u6","time","Трудозатраты отправлены на утверждение","Трудозатраты","",{"entries":5,"hours":26}),
            ]
            for off,cat,level,u,mod,action,obj,oid,det in seed_events:
                cur.execute("INSERT INTO events(id,ts,cat,level,user_id,module,action,obj,obj_id,details) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                            (uid("e"),now+off*DAY*1000,cat,level,u,mod,action,obj,oid,psycopg2.extras.Json(det)))
            db.bump(cur)
        conn.commit()
        return True
    finally: db.put(conn)

# ---------------- Ошибки и права ----------------
class RpcError(Exception):
    def __init__(self, message, title="Операция отклонена", status=400, code="VALIDATION"):
        super().__init__(message); self.title=title; self.status=status; self.code=code
class PermError(RpcError):
    def __init__(self, mod, op, role):
        super().__init__("Роль «%s» не имеет права «%s» в модуле %s. Проверка выполнена ядром." % (ROLES.get(role,role), {"view":"Просмотр","create":"Создание","edit":"Изменение","approve":"Утверждение","export":"Экспорт","delete":"Удаление"}.get(op,op), MODULES.get(mod,mod)),
                         title="403 · Недостаточно прав", status=403, code="FORBIDDEN")
        self.mod=mod; self.op=op

def can(db, cur, user, mod, op, obj=None):
    perms = db.settings(cur)["perms"]
    lst = perms.get(user["role"], {}).get(mod, [])
    if op not in lst: return False
    role = user["role"]
    if role == "pm" and obj:
        if obj.get("pm") and obj["pm"] != user["id"] and op in ("edit","approve","delete"): return False
        pid = obj.get("projectId")
        if pid and op in ("edit","approve","delete") and mod in ("portfolio","gantt","calcs","docs","risks","tasks","time"):
            pr = db.get_ent(cur,"project",pid)
            if pr and pr["pm"] != user["id"]: return False
    if role == "exec" and obj:
        if mod == "tasks" and op == "edit" and obj.get("assignee") != user["id"]: return False
        if mod == "time" and op == "edit" and obj.get("userId") != user["id"]: return False
    return True

def guard(db, cur, user, mod, op, obj=None):
    if not can(db, cur, user, mod, op, obj):
        raise PermError(mod, op, user["role"])

# ---------------- RPC: вспомогательные ----------------
HANDLERS = {}
def reg(name):
    def deco(fn): HANDLERS[name] = fn; return fn
    return deco
def req(p, *keys):
    for k in keys:
        v = p.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            raise RpcError("Не заполнено обязательное поле: %s" % k, "Проверка входных данных")
def get_req(db, cur, kind, id_):
    o = db.get_ent(cur, kind, id_)
    if not o: raise RpcError("Объект не найден: %s" % id_, "Объект не найден", 404, "NOT_FOUND")
    return o
def next_seq(db, cur, key):
    s = db.settings(cur); s["seq"][key] = s["seq"].get(key, 0) + 1; db.save_settings(cur, s)
    return s["seq"][key]
def check_time(s, name="время"):
    """Время события в формате ЧЧ:ММ с реальными часами и минутами (ТЗ §2.5)."""
    if not re.match(r"^\d{1,2}:\d{2}$", str(s or "")):
        raise RpcError("Некорректное %s: ожидается ЧЧ:ММ" % name)
    hh, mm = [int(x) for x in str(s).split(":")]
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise RpcError("Некорректное %s: часы 00–23, минуты 00–59" % name)
    return "%02d:%02d" % (hh, mm)

def check_date(s, name="дата"):
    try: parseD(s)
    except Exception: raise RpcError("Некорректная %s: %s" % (name, s))
def msg(title, sub="", tone="ok"): return {"tone": tone, "title": title, "sub": sub}
def trans_path(from_st, to_st, exec_mode=False):
    graph = EXEC_TRANS if exec_mode else T_TRANS
    if from_st == to_st: return []
    q = [[from_st]]; seen = {from_st}
    while q:
        path = q.pop(0); last = path[-1]
        if len(path) > 3: continue
        for nx in graph.get(last, []):
            if nx == to_st: return path[1:] + [nx]
            if nx not in seen: seen.add(nx); q.append(path + [nx])
    return None
def apply_task_status(t, s):
    t["status"] = s
    if s == "В работе" and t["progress"] == 0: t["progress"] = 10
    if s == "На проверке": t["progress"] = 90
    if s == "Возвращена на доработку": t["progress"] = 60
    if s in DONE_ST: t["progress"] = 100
    if t.get("type") == "directive":
        dr = t.get("directive") or {}
        dr["stateAt"] = now_ms()          # контроль «без обновления состояния» (ТЗ §2.11.1)
        t["directive"] = dr
WIDGET_KEYS = ["kpi_portfolio","kpi_tasks","kpi_overdue","kpi_load","kpi_risks","kpi_meetings","chart_proj_status",
               "chart_hours_trend","chart_load","list_my_tasks","list_deviations","list_milestones","list_directives",
               "list_meetings","matrix_risks","cal_week"]

# ---------------- M01: проекты ----------------
@reg("project.create")
def h_proj_create(db, cur, u, p):
    guard(db, cur, u, "portfolio", "create")
    req(p, "name", "start", "end", "budgetHours", "pm", "cat")
    check_date(p["start"]); check_date(p["end"])
    if p["end"] <= p["start"]: raise RpcError("Дата окончания должна быть позже даты начала")
    n = next_seq(db, cur, "proj")
    team_in = p.get("team") or []
    if isinstance(team_in, str): team_in = [team_in]
    team = [p["pm"]] + [x for x in team_in if x and x != p["pm"]]
    proj = dict(id=uid("p"), code="П-%d-%02d" % (today().year, n), name=p["name"].strip()[:300], cat=p["cat"],
                status="Инициатива", pm=p["pm"], start=p["start"], end=p["end"],
                budgetHours=max(1, int(p["budgetHours"])), team=team,
                desc=p.get("desc", "")[:4000], baseline={}, baselineAt=None, createdAt=now_ms())
    db.save_ent(cur, "project", proj)
    stages = PROJ_TEMPLATES.get(p.get("template", "none"), [])
    prev = None
    for i, (title, share, a, b) in enumerate(stages):
        tn = next_seq(db, cur, "task")
        t = dict(id=uid("t"), code="З-%03d" % tn, projectId=proj["id"], parent=None, title=title,
                 status="Новая" if i == 0 else "Черновик", priority="Высокий" if i < 2 else "Средний",
                 assignee=(team_in[0] if team_in else p["pm"]), start=D(a), end=D(b), planHours=round(proj["budgetHours"] * share),
                 progress=0, deps=[{"id": prev, "type": "FS", "lag": 0}] if prev else [], comments=[], result="",
                 blockReason="", createdAt=now_ms(), createdBy=u["id"], meetingId=None, riskId=None, type="task", desc="")
        db.save_ent(cur, "task", t); prev = t["id"]
    db.audit(cur, "BUSINESS", "info", u["id"], "portfolio", "Создан проект", "Проект", proj["id"],
             {"name": proj["name"], "template": p.get("template", "none"), "stages": len(stages)})
    return proj, msg("Проект создан" + (" · этапы шаблона добавлены в M02" if stages else ""), proj["code"])

@reg("project.update")
def h_proj_update(db, cur, u, p):
    pr = get_req(db, cur, "project", p["id"]); guard(db, cur, u, "portfolio", "edit", pr)
    before = {k: pr[k] for k in ("name", "start", "end", "status")}
    for k in ("name", "cat", "pm", "start", "end", "desc"):
        if k in p: pr[k] = p[k]
    if "budgetHours" in p: pr["budgetHours"] = max(1, int(p["budgetHours"]))
    check_date(pr["start"]); check_date(pr["end"])
    if pr["end"] <= pr["start"]: raise RpcError("Дата окончания должна быть позже даты начала")
    db.save_ent(cur, "project", pr)
    db.audit(cur, "AUDIT", "info", u["id"], "portfolio", "Проект изменён", "Проект", pr["id"], {"before": before, "after": {k: pr[k] for k in before}})
    return pr, msg("Проект обновлён", pr["code"])

@reg("project.status")
def h_proj_status(db, cur, u, p):
    pr = get_req(db, cur, "project", p["id"]); guard(db, cur, u, "portfolio", "edit", pr)
    to = p["to"]
    if to not in P_TRANS.get(pr["status"], []):
        raise RpcError("Workflow не разрешает переход «%s» → «%s»" % (pr["status"], to), "Недопустимый переход")
    if to == "Завершён":
        cur.execute("SELECT COUNT(*) AS n FROM tasks WHERE project_id=%s AND status NOT IN ('Принята','Закрыта','Отменена')", (pr["id"],))
        n = cur.fetchone()["n"]
        if n: raise RpcError("Открытых задач: %d. Закройте или отмените их (контроль целостности, ТЗ M01 §14)." % n, "Проект нельзя завершить")
    before = pr["status"]; pr["status"] = to
    db.save_ent(cur, "project", pr)
    db.audit(cur, "BUSINESS", "info", u["id"], "portfolio", "Статус проекта изменён", "Проект", pr["id"], {"before": before, "after": to, "code": pr["code"]})
    return pr, msg("Статус: «%s» → «%s»" % (before, to), pr["code"])

@reg("project.baseline")
def h_proj_baseline(db, cur, u, p):
    pr = get_req(db, cur, "project", p["id"]); guard(db, cur, u, "gantt", "edit", pr)
    cur.execute("SELECT id,start,\"end\" FROM tasks WHERE project_id=%s AND status<>'Отменена'", (pr["id"],))
    pr["baseline"] = {r["id"]: {"start": r["start"], "end": r["end"]} for r in cur.fetchall()}
    pr["baselineAt"] = now_ms()
    db.save_ent(cur, "project", pr)
    db.audit(cur, "BUSINESS", "info", u["id"], "gantt", "Сохранён базовый план", "Проект", pr["id"], {"code": pr["code"], "tasks": len(pr["baseline"])})
    return pr, msg("Базовый план сохранён", "Снимок сроков зафиксирован; сравнение доступно на ганте и в M07")

@reg("project.delete")
def h_proj_delete(db, cur, u, p):
    pr = get_req(db, cur, "project", p["id"]); guard(db, cur, u, "portfolio", "delete", pr)
    db.del_ent(cur, "project", pr["id"])
    db.audit(cur, "AUDIT", "warning", u["id"], "portfolio", "Проект удалён", "Проект", pr["id"], {"code": pr["code"], "name": pr["name"]})
    return True, msg("Проект удалён", pr["code"] + " · связанные задачи, риски, документы и расчёты удалены каскадно")

# ---------------- M02: задачи ----------------
def task_data_validate(db, cur, p, existing=None):
    req(p, "title", "projectId", "start", "end", "assignee", "priority")
    check_date(p["start"]); check_date(p["end"])
    get_req(db, cur, "project", p["projectId"])
    typ = p.get("type", "task")
    end = p["start"] if typ == "milestone" else p["end"]
    if end < p["start"]: raise RpcError("Окончание не может быть раньше начала")
    deps = p.get("deps", [])
    for d in deps:
        dt = get_req(db, cur, "task", d["id"])
        if dt["projectId"] != p["projectId"]: raise RpcError("Зависимость должна ссылаться на задачу того же проекта")
        if existing and d["id"] == existing["id"]: raise RpcError("Задача не может зависеть от самой себя")
    return dict(title=p["title"].strip()[:300], projectId=p["projectId"], type=typ, assignee=p["assignee"],
                priority=p["priority"], start=p["start"], end=end,
                planHours=0 if typ == "milestone" else max(0, int(p.get("planHours", 0))),
                desc=p.get("desc", "")[:4000], deps=deps, parent=p.get("parent"))

@reg("task.create")
def h_task_create(db, cur, u, p):
    data = task_data_validate(db, cur, p)
    guard(db, cur, u, "tasks", "create", {"projectId": p["projectId"]})
    st = p.get("initialStatus", "Черновик")
    if st not in ("Черновик", "Новая", "Запланирована", "Готова к выполнению"): st = "Черновик"
    tn = next_seq(db, cur, "task")
    t = dict(id=uid("t"), code="З-%03d" % tn, status=st, progress=0, result="", blockReason="", comments=[],
             createdAt=now_ms(), createdBy=u["id"], meetingId=None, riskId=None, **data)
    db.save_ent(cur, "task", t)
    if t.get("assignee") and t["assignee"] != u["id"]:
        notify(db, cur, t["assignee"], "task.assigned", "Назначена задача: %s" % t["title"],
               "%s · срок %s · постановщик %s" % (t["code"], _fd(t["end"]), u["name"]), "Задача", t["id"],
               dedupe="task.assigned:%s:%s" % (t["assignee"], t["id"]))
    db.audit(cur, "BUSINESS", "info", u["id"], "tasks", "Создана задача", "Задача", t["id"], {"title": t["title"], "project": p["projectId"]})
    return t, msg("Задача создана в M02", "Она сразу доступна в канбане, ганте и календаре")

@reg("task.update")
def h_task_update(db, cur, u, p):
    t = get_req(db, cur, "task", p["id"]); guard(db, cur, u, "tasks", "edit", t)
    data = task_data_validate(db, cur, p, t)
    before = {k: t[k] for k in ("title", "start", "end", "assignee", "priority")}
    t.update(data)
    db.save_ent(cur, "task", t)
    if t.get("assignee") and t["assignee"] != before.get("assignee") and t["assignee"] != u["id"]:
        notify(db, cur, t["assignee"], "task.assigned", "Вам передана задача: %s" % t["title"],
               "%s · срок %s · передал %s" % (t["code"], _fd(t["end"]), u["name"]), "Задача", t["id"],
               dedupe="task.assigned:%s:%s:%s" % (t["assignee"], t["id"], now_ms()))
    db.audit(cur, "AUDIT", "info", u["id"], "tasks", "Задача изменена", "Задача", t["id"], {"before": before, "after": {k: t[k] for k in before}})
    return t, msg("Задача обновлена", t["code"])

@reg("task.status")
def h_task_status(db, cur, u, p):
    t = get_req(db, cur, "task", p["id"]); guard(db, cur, u, "tasks", "edit", t)
    to = p["to"]
    if to not in T_TRANS.get(t["status"], []):
        raise RpcError("Workflow: «%s» → «%s» запрещён (ТЗ M02 §1.9.1)" % (t["status"], to), "Недопустимый переход")
    if u["role"] == "exec" and to not in EXEC_TRANS.get(t["status"], []):
        raise PermError("tasks", "edit", u["role"])
    if to == "Принята": guard(db, cur, u, "tasks", "approve", t)
    if to == "На проверке":
        res = (p.get("result") or t.get("result") or "").strip()
        if not res: raise RpcError("Перевод в «На проверке» требует описания результата — обязательное поле workflow.", "Результат выполнения обязателен", 400, "NEED_RESULT")
        t["result"] = res[:4000]
    if to == "Заблокирована":
        br = (p.get("blockReason") or t.get("blockReason") or "").strip()
        if not br: raise RpcError("Укажите причину блокировки.", "Причина блокировки обязательна", 400, "NEED_BLOCK_REASON")
        t["blockReason"] = br[:1000]
    before = t["status"]; apply_task_status(t, to)
    db.save_ent(cur, "task", t)
    db.audit(cur, "BUSINESS", "info", u["id"], "tasks", "Статус задачи изменён", "Задача", t["id"], {"code": t["code"], "before": before, "after": to})
    return t, msg("«%s» → «%s»" % (before, to), t["code"] + " · изменение видно в канбане, ганте и на дашборде")

@reg("task.progress")
def h_task_progress(db, cur, u, p):
    t = get_req(db, cur, "task", p["id"]); guard(db, cur, u, "tasks", "edit", t)
    t["progress"] = max(0, min(100, int(p["value"])))
    if t.get("type") == "directive":
        dr = dir_of(t); dr["stateAt"] = now_ms(); t["directive"] = dr
    db.save_ent(cur, "task", t)
    db.audit(cur, "AUDIT", "info", u["id"], "tasks", "Прогресс задачи изменён", "Задача", t["id"], {"code": t["code"], "progress": t["progress"]})
    return t, msg("Прогресс: %d%%" % t["progress"], t["code"])

@reg("task.comment")
def h_task_comment(db, cur, u, p):
    t = get_req(db, cur, "task", p["id"]); guard(db, cur, u, "tasks", "edit", t)
    req(p, "text")
    t["comments"].append({"user": u["id"], "ts": now_ms(), "text": p["text"][:2000]})
    db.save_ent(cur, "task", t)
    db.audit(cur, "AUDIT", "info", u["id"], "tasks", "Комментарий к задаче", "Задача", t["id"], {"code": t["code"]})
    return t, msg("Комментарий добавлен", t["code"])

@reg("task.delete")
def h_task_delete(db, cur, u, p):
    t = get_req(db, cur, "task", p["id"]); guard(db, cur, u, "tasks", "delete", t)
    cur.execute("SELECT id,deps FROM tasks WHERE project_id=%s AND id<>%s", (t["projectId"], t["id"]))
    for r in cur.fetchall():
        nd = [d for d in r["deps"] if d["id"] != t["id"]]
        if len(nd) != len(r["deps"]):
            ot = db.get_ent(cur, "task", r["id"]); ot["deps"] = nd; db.save_ent(cur, "task", ot)
    if t.get("meetingId"):
        cur.execute("DELETE FROM meeting_tasks WHERE task_id=%s", (t["id"],))
        m = db.get_ent(cur, "meeting", t["meetingId"])
        if m:
            for d in m.get("decisions") or []:
                ids = [x for x in dec_task_ids(d) if x != t["id"]]
                if ids != dec_task_ids(d):
                    d["taskIds"] = ids; d["taskId"] = ids[0] if ids else None
            db.save_ent(cur, "meeting", m)
    db.del_ent(cur, "task", t["id"])
    db.audit(cur, "AUDIT", "warning", u["id"], "tasks", "Задача удалена", "Задача", t["id"], {"code": t["code"], "title": t["title"]})
    return True, msg("Задача удалена", "Записи трудозатрат сохранены в M06 как история")

# ---------------- M03: канбан ----------------
@reg("kanban.move")
def h_kanban_move(db, cur, u, p):
    t = get_req(db, cur, "task", p["id"]); guard(db, cur, u, "tasks", "edit", t)
    col = KB_COLS.get(p["col"])
    if not col: raise RpcError("Неизвестная колонка")
    if t["status"] in col["sts"]: return t, None
    is_exec = u["role"] == "exec"
    path = trans_path(t["status"], col["target"], is_exec)
    if path is None:
        raise RpcError("«%s» → «%s»%s" % (t["status"], col["target"], " недоступен для роли Исполнитель" if is_exec else " не входит в допустимые переходы"), "Переход запрещён workflow", 400, "FORBIDDEN_TRANSITION")
    if "Принята" in path: guard(db, cur, u, "tasks", "approve", t)
    if col.get("wip"):
        s = db.settings(cur)
        cur.execute("SELECT COUNT(*) AS n FROM tasks WHERE status = ANY(%s) AND id<>%s", (col["sts"], t["id"]))
        n = cur.fetchone()["n"]
        if n >= s["wip"]:
            db.audit(cur, "BUSINESS", "warning", u["id"], "kanban", "Отклонено перемещение: WIP-лимит", "Задача", t["id"], {"limit": s["wip"]})
            raise RpcError("Уже %d из %d задач. Завершите начатое или измените лимит в M14." % (n, s["wip"]), "WIP-лимит колонки «В работе»", 400, "WIP_FULL")
    if "На проверке" in path:
        res = (p.get("result") or t.get("result") or "").strip()
        if not res: raise RpcError("Колонка «На проверке» требует описание результата.", "Результат выполнения обязателен", 400, "NEED_RESULT")
        t["result"] = res[:4000]
    before = t["status"]
    for s in path: apply_task_status(t, s)
    db.save_ent(cur, "task", t)
    db.audit(cur, "BUSINESS", "info", u["id"], "kanban", "Канбан: перемещение карточки", "Задача", t["id"],
             {"code": t["code"], "before": before, "after": t["status"], "chain": [before] + path})
    sub = ("Цепочка переходов: " + " → ".join([before] + path)) if len(path) > 1 else t["code"]
    return t, msg("«%s» → «%s»" % (before, t["status"]), sub)

# ---------------- M04: гант ----------------
def gantt_affected(db, cur, t, delta):
    new_end = dISO(parseD(t["end"]) + datetime.timedelta(days=delta))
    new_start = dISO(parseD(t["start"]) + datetime.timedelta(days=delta))
    out = []
    cur.execute("SELECT id FROM tasks WHERE project_id=%s", (t["projectId"],))
    for r in cur.fetchall():
        x = db.get_ent(cur, "task", r["id"])
        if not x.get("end"): continue
        for d in x["deps"]:
            if d["id"] != t["id"]: continue
            viol = False
            if d["type"] == "FS": viol = diffD(addWk(new_end, 1 + (d.get("lag") or 0)), x["start"]) > 0
            elif d["type"] == "SS": viol = diffD(new_start, x["start"]) > 0
            elif d["type"] == "FF": viol = diffD(new_end, x["end"]) > 0
            elif d["type"] == "SF": viol = diffD(new_start, x["end"]) > 0
            if viol: out.append({"taskId": x["id"], "dep": d})
    return out

@reg("gantt.impact")
def h_gantt_impact(db, cur, u, p):
    t = get_req(db, cur, "task", p["id"]); guard(db, cur, u, "gantt", "view")
    if not t.get("end"): raise RpcError("У поручения не установлен срок исполнения — задайте его в карточке (ТЗ §2.11.1)", "Гант")
    delta = int(p["delta"])
    if abs(delta) > 365: raise RpcError("Сдвиг слишком велик")
    aff = gantt_affected(db, cur, t, delta)
    return {"affected": aff, "newStart": dISO(parseD(t["start"]) + datetime.timedelta(days=delta)),
            "newEnd": dISO(parseD(t["end"]) + datetime.timedelta(days=delta))}, None

@reg("gantt.shift")
def h_gantt_shift(db, cur, u, p):
    t = get_req(db, cur, "task", p["id"])
    pr = get_req(db, cur, "project", t["projectId"]); guard(db, cur, u, "gantt", "edit", pr)
    if not t.get("end"): raise RpcError("У поручения не установлен срок исполнения — задайте его в карточке (ТЗ §2.11.1)", "Гант")
    delta = int(p["delta"])
    if abs(delta) > 365: raise RpcError("Сдвиг слишком велик")
    before = {"start": t["start"], "end": t["end"]}
    shifted = []
    def shift(task, dd):
        if not task.get("end"): return          # поручение без срока не сдвигается (контроль §2.11.1)
        task["start"] = dISO(parseD(task["start"]) + datetime.timedelta(days=dd))
        task["end"] = dISO(parseD(task["end"]) + datetime.timedelta(days=dd))
        db.save_ent(cur, "task", task); shifted.append(task["code"])
    shift(t, delta)
    if p.get("cascade"):
        queue = [t["id"]]; seen = {t["id"]}
        while queue:
            ct = db.get_ent(cur, "task", queue.pop(0))
            if not ct.get("end"): continue      # поручение без срока в каскаде не участвует
            cur.execute("SELECT id FROM tasks WHERE project_id=%s", (ct["projectId"],))
            for r in cur.fetchall():
                if r["id"] in seen: continue
                x = db.get_ent(cur, "task", r["id"])
                if not x.get("end"): continue
                for d in x["deps"]:
                    if d["id"] != ct["id"]: continue
                    need = 0; lag = d.get("lag") or 0
                    if d["type"] == "FS": need = diffD(addWk(ct["end"], 1 + lag), x["start"])
                    elif d["type"] == "SS": need = diffD(ct["start"], x["start"]) + lag
                    elif d["type"] == "FF": need = diffD(ct["end"], x["end"]) + lag
                    if need > 0:
                        shift(x, need); seen.add(x["id"]); queue.append(x["id"])
    db.audit(cur, "BUSINESS", "warning", u["id"], "gantt", "Сроки изменены на диаграмме Ганта", "Задача", t["id"],
             {"code": t["code"], "before": before, "after": {"start": t["start"], "end": t["end"]}, "delta": delta, "cascade": bool(p.get("cascade")), "shifted": shifted})
    return t, msg("Сроки сдвинуты на %+d дн" % delta, ("Каскад: " + ", ".join(shifted)) if len(shifted) > 1 else "Базовый план сохранён без изменений")

# ---------------- M06: трудозатраты ----------------
@reg("time.create")
def h_time_create(db, cur, u, p):
    req(p, "date", "hours", "taskId", "act")
    check_date(p["date"])
    hours = float(p["hours"])
    if not (0 < hours <= 12): raise RpcError("Часы: от 0,5 до 12 в одной записи")
    if p["date"] > dISO(today()): raise RpcError("Нельзя списывать часы на будущую дату")
    t = get_req(db, cur, "task", p["taskId"])
    guard(db, cur, u, "time", "create", {"projectId": t["projectId"]})
    e = dict(id=uid("te"), userId=u["id"], taskId=t["id"], projectId=t["projectId"], date=p["date"], hours=hours,
             act=p["act"], comment=p.get("comment", "")[:500], status="Черновик", approvedBy=None)
    db.save_ent(cur, "time", e)
    db.audit(cur, "BUSINESS", "info", u["id"], "time", "Списаны трудозатраты", "Трудозатраты", e["id"], {"hours": hours, "task": t["code"], "date": p["date"]})
    return e, msg("%s ч записано на %s" % (hours, t["code"]), "Черновик → отправьте на утверждение")

@reg("time.update")
def h_time_update(db, cur, u, p):
    e = get_req(db, cur, "time", p["id"]); guard(db, cur, u, "time", "edit", e)
    if e["status"] != "Черновик": raise RpcError("Изменять можно только записи в статусе «Черновик»")
    hours = float(p.get("hours", e["hours"]))
    if not (0 < hours <= 12): raise RpcError("Часы: от 0,5 до 12")
    if p.get("date"):
        check_date(p["date"])
        if p["date"] > dISO(today()): raise RpcError("Нельзя списывать часы на будущую дату")
        e["date"] = p["date"]
    if p.get("taskId"):
        t = get_req(db, cur, "task", p["taskId"]); e["taskId"] = t["id"]; e["projectId"] = t["projectId"]
    e["hours"] = hours
    if p.get("act"): e["act"] = p["act"]
    e["comment"] = p.get("comment", e["comment"])[:500]
    db.save_ent(cur, "time", e)
    db.audit(cur, "AUDIT", "info", u["id"], "time", "Запись трудозатрат изменена", "Трудозатраты", e["id"], {"hours": hours})
    return e, msg("Запись обновлена")

@reg("time.status")
def h_time_status(db, cur, u, p):
    e = get_req(db, cur, "time", p["id"]); to = p["to"]
    if to in ("Утверждена", "Отклонена"):
        if e["status"] != "Отправлена": raise RpcError("Утверждать можно записи в статусе «Отправлена»")
        guard(db, cur, u, "time", "approve", e)
        e["approvedBy"] = u["id"] if to == "Утверждена" else None
    elif to == "Отправлена":
        if e["status"] != "Черновик": raise RpcError("Отправить можно только черновик")
        guard(db, cur, u, "time", "edit", e)
    else: raise RpcError("Недопустимый статус")
    e["status"] = to
    db.save_ent(cur, "time", e)
    notify(db, cur, e["userId"], "time.result", "Трудозатраты %s" % to.lower(),
           "%s · %s ч · %s" % (_fd(e["date"]), e["hours"], u["name"]), "Трудозатраты", e["id"],
           dedupe="time.result:%s:%s" % (e["id"], to))
    db.audit(cur, "BUSINESS", "info", u["id"], "time", "Трудозатраты: " + to.lower(), "Трудозатраты", e["id"], {"hours": e["hours"], "status": to})
    return e, msg("Запись " + to.lower())

@reg("time.sendWeek")
def h_time_send_week(db, cur, u, p):
    guard(db, cur, u, "time", "edit", {"userId": u["id"]})
    req(p, "mon")
    check_date(p["mon"])
    we = dISO(parseD(p["mon"]) + datetime.timedelta(days=6))
    cur.execute("SELECT id FROM time_entries WHERE user_id=%s AND status='Черновик' AND date>=%s AND date<=%s", (u["id"], p["mon"], we))
    ids = [r["id"] for r in cur.fetchall()]
    if not ids: raise RpcError("Нет черновиков за выбранную неделю", "Нечего отправлять")
    total = 0
    for id_ in ids:
        e = db.get_ent(cur, "time", id_); e["status"] = "Отправлена"; total += e["hours"]; db.save_ent(cur, "time", e)
    notify_many(db, cur, [x for x in _approvers(db, cur, "time") if x != u["id"]], "time.approval",
                "Трудозатраты на утверждение: %s ч" % round(total, 1),
                "%s · %d записей за неделю %s" % (u["name"], len(ids), _fd(p["mon"])), "Очередь", "time",
                dedupe_suffix="week:%s:%s" % (u["id"], p["mon"]))
    db.audit(cur, "BUSINESS", "info", u["id"], "time", "Трудозатраты отправлены на утверждение", "Трудозатраты", "", {"entries": len(ids), "hours": total})
    return True, msg("%d записей отправлено (%s ч)" % (len(ids), total))

@reg("time.delete")
def h_time_delete(db, cur, u, p):
    e = get_req(db, cur, "time", p["id"]); guard(db, cur, u, "time", "edit", e)
    if e["status"] != "Черновик": raise RpcError("Удалять можно только черновики")
    db.del_ent(cur, "time", e["id"])
    db.audit(cur, "AUDIT", "warning", u["id"], "time", "Запись трудозатрат удалена", "Трудозатраты", e["id"], {"hours": e["hours"]})
    return True, msg("Запись удалена")

# ---------------- M08: риски ----------------
@reg("risk.create")
def h_risk_create(db, cur, u, p):
    guard(db, cur, u, "risks", "create", {"projectId": p.get("projectId")})
    req(p, "title", "projectId", "cat", "strategy", "owner", "review", "status")
    pv, iv = int(p["p"]), int(p["i"])
    if not (1 <= pv <= 5 and 1 <= iv <= 5): raise RpcError("P и I: значения 1–5")
    check_date(p["review"])
    n = next_seq(db, cur, "risk")
    r = dict(id=uid("r"), code="Р-%02d" % n, title=p["title"].strip()[:300], cat=p["cat"], projectId=p["projectId"],
             p=pv, i=iv, strategy=p["strategy"], owner=p["owner"], status=p["status"], review=p["review"],
             residual=None, desc=p.get("desc", "")[:4000], measures=[], events=[],
             history=[{"ts": now_ms(), "p": pv, "i": iv, "by": u["id"]}])
    db.save_ent(cur, "risk", r)
    db.audit(cur, "BUSINESS", "info", u["id"], "risks", "Зарегистрирован риск", "Риск", r["id"], {"title": r["title"], "score": pv * iv})
    return r, msg("Риск зарегистрирован", "Балл %d" % (pv * iv))

@reg("risk.update")
def h_risk_update(db, cur, u, p):
    r = get_req(db, cur, "risk", p["id"]); guard(db, cur, u, "risks", "edit", r)
    pv, iv = int(p.get("p", r["p"])), int(p.get("i", r["i"]))
    if not (1 <= pv <= 5 and 1 <= iv <= 5): raise RpcError("P и I: значения 1–5")
    changed = (pv != r["p"] or iv != r["i"])
    before = {"p": r["p"], "i": r["i"]}
    if changed: r["history"].append({"ts": now_ms(), "p": pv, "i": iv, "by": u["id"]})
    for k in ("title", "cat", "projectId", "strategy", "owner", "status", "review", "desc"):
        if k in p: r[k] = p[k]
    r["p"], r["i"] = pv, iv
    db.save_ent(cur, "risk", r)
    db.audit(cur, "AUDIT", "warning" if changed else "info", u["id"], "risks",
             "Изменена оценка риска" if changed else "Риск изменён", "Риск", r["id"],
             {"before": before, "after": {"p": pv, "i": iv}, "score": pv * iv} if changed else {"code": r["code"]})
    return r, msg("Риск обновлён", r["code"])

@reg("risk.measure.add")
def h_risk_measure_add(db, cur, u, p):
    r = get_req(db, cur, "risk", p["id"]); guard(db, cur, u, "risks", "edit", r)
    req(p, "title", "assignee", "due"); check_date(p["due"])
    r["measures"].append({"id": uid("rm"), "title": p["title"][:300], "assignee": p["assignee"], "due": p["due"], "done": False})
    db.save_ent(cur, "risk", r)
    db.audit(cur, "AUDIT", "info", u["id"], "risks", "Добавлено мероприятие по риску", "Риск", r["id"], {"code": r["code"]})
    return r, msg("Мероприятие добавлено")

@reg("risk.measure.toggle")
def h_risk_measure_toggle(db, cur, u, p):
    r = get_req(db, cur, "risk", p["id"]); guard(db, cur, u, "risks", "edit", r)
    m = next((x for x in r["measures"] if x["id"] == p["mid"]), None)
    if not m: raise RpcError("Мероприятие не найдено")
    m["done"] = not m["done"]
    db.save_ent(cur, "risk", r)
    db.audit(cur, "AUDIT", "info", u["id"], "risks", "Мероприятие выполнено" if m["done"] else "Мероприятие возвращено в работу",
             "Риск", r["id"], {"code": r["code"], "measure": m["title"]})
    return r, msg("Мероприятие выполнено" if m["done"] else "Мероприятие в работе", "Статус риска не меняется автоматически")

@reg("risk.event.add")
def h_risk_event_add(db, cur, u, p):
    r = get_req(db, cur, "risk", p["id"]); guard(db, cur, u, "risks", "edit", r)
    req(p, "text")
    r["events"].append({"ts": now_ms(), "text": p["text"][:1000]})
    db.save_ent(cur, "risk", r)
    db.audit(cur, "AUDIT", "warning", u["id"], "risks", "Зарегистрировано риск-событие", "Риск", r["id"], {"code": r["code"]})
    return r, msg("Риск-событие зарегистрировано")

@reg("risk.realize")
def h_risk_realize(db, cur, u, p):
    r = get_req(db, cur, "risk", p["id"]); guard(db, cur, u, "risks", "edit", r)
    r["status"] = "Реализовался"
    r["events"].append({"ts": now_ms(), "text": "Риск отмечен как реализовавшийся"})
    db.save_ent(cur, "risk", r)
    db.audit(cur, "BUSINESS", "error", u["id"], "risks", "Риск реализовался", "Риск", r["id"], {"code": r["code"], "title": r["title"]})
    return r, msg("Риск переведён в «Реализовался»", "", "warn")

@reg("risk.residual")
def h_risk_residual(db, cur, u, p):
    r = get_req(db, cur, "risk", p["id"]); guard(db, cur, u, "risks", "edit", r)
    pv, iv = int(p["p"]), int(p["i"])
    if not (1 <= pv <= 5 and 1 <= iv <= 5): raise RpcError("P и I: значения 1–5")
    r["residual"] = {"p": pv, "i": iv}
    db.save_ent(cur, "risk", r)
    db.audit(cur, "AUDIT", "info", u["id"], "risks", "Оценён остаточный риск", "Риск", r["id"], {"code": r["code"], "residual": r["residual"]})
    return r, msg("Остаточная оценка сохранена")

@reg("risk.delete")
def h_risk_delete(db, cur, u, p):
    r = get_req(db, cur, "risk", p["id"]); guard(db, cur, u, "risks", "delete", r)
    db.del_ent(cur, "risk", r["id"])
    db.audit(cur, "AUDIT", "warning", u["id"], "risks", "Риск удалён", "Риск", r["id"], {"code": r["code"]})
    return True, msg("Риск удалён")

# ---------------- M09: документы ----------------
@reg("doc.create")
def h_doc_create(db, cur, u, p):
    guard(db, cur, u, "docs", "create", {"projectId": p.get("projectId")})
    req(p, "title", "type", "projectId", "fileName")
    n = next_seq(db, cur, "doc")
    d = dict(id=uid("d"), code="Д-%03d" % n, title=p["title"].strip()[:300], type=p["type"], projectId=p["projectId"],
             taskId=p.get("taskId") or None, riskId=p.get("riskId") or None, status="Черновик", author=u["id"])
    db.save_ent(cur, "doc", d)
    cur.execute("INSERT INTO doc_versions(doc_id,v,ts,by_user,size,note,file_name,stored) VALUES(%s,1,%s,%s,%s,%s,%s,FALSE)",
                (d["id"], now_ms(), u["id"], int(p.get("fileSize", 0)), p.get("note", "Первая редакция")[:500], p["fileName"][:200]))
    db.audit(cur, "BUSINESS", "info", u["id"], "docs", "Документ загружен", "Документ", d["id"], {"title": d["title"], "file": p["fileName"]})
    return d, msg("Документ добавлен в хранилище", d["code"] + " · v1")

@reg("doc.version")
def h_doc_version(db, cur, u, p):
    d = get_req(db, cur, "doc", p["id"]); guard(db, cur, u, "docs", "create", d)
    req(p, "fileName")
    v = len(d["versions"]) + 1
    cur.execute("INSERT INTO doc_versions(doc_id,v,ts,by_user,size,note,file_name,stored) VALUES(%s,%s,%s,%s,%s,%s,%s,FALSE)",
                (d["id"], v, now_ms(), u["id"], int(p.get("fileSize", 0)), p.get("note", "Обновлённая редакция")[:500], p["fileName"][:200]))
    d["status"] = "Черновик"
    db.save_ent(cur, "doc", d)
    d = db.get_ent(cur, "doc", d["id"])
    db.audit(cur, "BUSINESS", "info", u["id"], "docs", "Загружена новая версия документа", "Документ", d["id"], {"code": d["code"], "v": v})
    return d, msg("Версия v%d загружена" % v, "Статус сброшен в «Черновик» — требуется повторное согласование")

@reg("doc.status")
def h_doc_status(db, cur, u, p):
    d = get_req(db, cur, "doc", p["id"]); to = p["to"]
    if to not in ("Черновик", "На согласовании", "Утверждён", "Устарел"): raise RpcError("Недопустимый статус")
    guard(db, cur, u, "docs", "approve" if to == "Утверждён" else "edit", d)
    d["status"] = to
    db.save_ent(cur, "doc", d)
    if to == "На согласовании":
        notify_many(db, cur, [x for x in _approvers(db, cur, "docs") if x != u["id"]], "doc.approval",
                    "На согласование: %s" % d["title"], "%s · %s" % (d["code"], u["name"]), "Документ", d["id"])
    elif to == "Утверждён" and d.get("author"):
        notify(db, cur, d["author"], "doc.approved", "Документ утверждён: %s" % d["title"],
               "%s · утвердил %s" % (d["code"], u["name"]), "Документ", d["id"],
               dedupe="doc.approved:%s" % d["id"])
    db.audit(cur, "BUSINESS", "info", u["id"], "docs", "Документ: " + to, "Документ", d["id"], {"code": d["code"], "status": to})
    return d, msg("Статус документа: «%s»" % to)

@reg("doc.delete")
def h_doc_delete(db, cur, u, p):
    d = get_req(db, cur, "doc", p["id"]); guard(db, cur, u, "docs", "delete", d)
    db.del_ent(cur, "doc", d["id"])
    db.audit(cur, "AUDIT", "warning", u["id"], "docs", "Документ удалён", "Документ", d["id"], {"code": d["code"]})
    return True, msg("Документ удалён")

# ---------------- M10: расчёты ----------------
def calc_validate(model_key, inputs):
    m = CALC_MODELS.get(model_key)
    if not m: raise RpcError("Неизвестная расчётная модель")
    vals = {}
    for k, name, lo, hi in m["inputs"]:
        try: v = float(inputs[k])
        except Exception: raise RpcError("Параметр «%s»: ожидается число" % name)
        if not (lo <= v <= hi): raise RpcError("Параметр «%s»: допустимый диапазон %s … %s" % (name, lo, hi))
        vals[k] = v
    return m, vals

@reg("calc.run")
def h_calc_run(db, cur, u, p):
    guard(db, cur, u, "calcs", "create", {"projectId": p.get("projectId")})
    req(p, "title", "model", "projectId")
    m, vals = calc_validate(p["model"], p.get("inputs", {}))
    outputs = m["run"](vals)
    n = next_seq(db, cur, "calc")
    c = dict(id=uid("c"), code="Р-%d-%03d" % (today().year, n), title=p["title"].strip()[:300], model=p["model"],
             ver=m["ver"], projectId=p["projectId"], taskId=p.get("taskId") or None, by=u["id"], ts=now_ms(),
             status="Выполнен", verifiedBy=None, verifiedAt=None, inputs=vals, outputs=outputs,
             runs=[{"ts": now_ms(), "by": u["id"], "inputs": dict(vals), "ok": outputs["ok"]}])
    db.save_ent(cur, "calc", c)
    notify_many(db, cur, [x for x in _approvers(db, cur, "calcs") if x != u["id"]], "calc.verify",
                "Расчёт ожидает проверки: %s" % c["title"], "%s · %s" % (c["code"], m["name"]), "Расчёт", c["id"])
    db.audit(cur, "INTEGRATION", "info", u["id"], "calcs", "Расчёт выполнен", "Расчёт", c["id"], {"model": m["name"] + " v" + m["ver"], "ok": outputs["ok"]})
    return c, msg("Расчёт выполнен", outputs["verdict"], "ok" if outputs["ok"] else "warn")

@reg("calc.verify")
def h_calc_verify(db, cur, u, p):
    c = get_req(db, cur, "calc", p["id"]); guard(db, cur, u, "calcs", "approve", c)
    if c["status"] != "Выполнен": raise RpcError("Проверять можно расчёты в статусе «Выполнен»")
    c["status"] = "Проверен"; c["verifiedBy"] = u["id"]; c["verifiedAt"] = now_ms()
    db.save_ent(cur, "calc", c)
    db.audit(cur, "AUDIT", "info", u["id"], "calcs", "Расчёт проверен", "Расчёт", c["id"], {"code": c["code"]})
    return c, msg("Расчёт проверен и принят", "Результат можно использовать как основание технических решений")

@reg("calc.rerun")
def h_calc_rerun(db, cur, u, p):
    c = get_req(db, cur, "calc", p["id"]); guard(db, cur, u, "calcs", "create", c)
    m, vals = calc_validate(c["model"], p.get("inputs", {}))
    c["inputs"] = vals; c["outputs"] = m["run"](vals); c["ts"] = now_ms(); c["by"] = u["id"]
    c["status"] = "Выполнен"; c["verifiedBy"] = None; c["verifiedAt"] = None
    c["runs"].append({"ts": now_ms(), "by": u["id"], "inputs": dict(vals), "ok": c["outputs"]["ok"]})
    db.save_ent(cur, "calc", c)
    db.audit(cur, "INTEGRATION", "info", u["id"], "calcs", "Повторный запуск расчёта", "Расчёт", c["id"], {"code": c["code"], "run": len(c["runs"])})
    return c, msg("Расчёт пересчитан", c["outputs"]["verdict"], "ok" if c["outputs"]["ok"] else "warn")

@reg("calc.delete")
def h_calc_delete(db, cur, u, p):
    c = get_req(db, cur, "calc", p["id"]); guard(db, cur, u, "calcs", "delete", c)
    db.del_ent(cur, "calc", c["id"])
    db.audit(cur, "AUDIT", "warning", u["id"], "calcs", "Расчёт удалён", "Расчёт", c["id"], {"code": c["code"]})
    return True, msg("Расчёт удалён")

# ---------------- M16: совещания ----------------
@reg("meeting.create")
def h_meeting_create(db, cur, u, p):
    guard(db, cur, u, "meetings", "create")
    req(p, "title", "type", "date", "time", "chair", "secretary")
    check_date(p["date"]); p["time"] = check_time(p["time"])
    rule = rec_validate(p.get("recurrence") or {}, p["date"])
    projects = _meet_projects_validate(db, cur, p.get("projects") or [])
    series_id = uid("ms") if rule["freq"] != "none" else ""
    rec = dict(rule, seriesId=series_id, seriesN=1, master=bool(series_id), skipped=[])
    n = next_seq(db, cur, "meet")
    m = dict(id=uid("m"), code="СОВ-%d-%03d" % (today().year, n), title=p["title"].strip()[:300], type=p["type"],
             date=p["date"], time=p["time"], chair=p["chair"], secretary=p["secretary"],
             attendees=p.get("attendees", []), status="Запланировано", agenda=[], decisions=[],
             protocol=protocol_default(), materials=[], projects=projects, recurrence=rec,
             seriesId=series_id, planLog=[])
    db.save_ent(cur, "meeting", m)
    db.audit(cur, "BUSINESS", "info", u["id"], "meetings", "Создано совещание", "Совещание", m["id"],
             {"title": m["title"], "date": m["date"], "projects": projects, "recurrence": rec_text(rec),
              "event": "MEETING_CREATED"})
    invite = list(dict.fromkeys([m["chair"], m["secretary"]] + (m["attendees"] or [])))
    notify_many(db, cur, [x for x in invite if x != u["id"]], "meeting.invite",
                "Приглашение: %s" % m["title"], "%s · %s %s" % (m["code"], _fd(m["date"]), m["time"]),
                "Совещание", m["id"], dedupe_suffix="invite")
    reminders_set(db, cur, "meeting", m, invite, DEFAULT_REMINDERS, u["id"])
    made = _series_generate(db, cur, m, horizon=D(MEET_SERIES_HORIZON), by=u["id"]) if series_id else []
    return m, msg("Совещание создано", "Приглашения разосланы, напоминания: за 1 день и за 1 час"
                  + (" · %s, создано карточек серии: %d" % (rec_text(rec), len(made)) if made else ""))

@reg("meeting.update")
def h_meeting_update(db, cur, u, p):
    m = get_req(db, cur, "meeting", p["id"]); guard(db, cur, u, "meetings", "edit", m); meet_active(m)
    before = {k: m[k] for k in ("date", "time", "chair", "secretary", "attendees")}
    if "date" in p: check_date(p["date"])
    if "time" in p: p["time"] = check_time(p["time"])
    for k in ("title", "type", "date", "time", "chair", "secretary", "attendees"):
        if k in p: m[k] = p[k]
    if (before["date"], before["time"]) != (m["date"], m["time"]):
        _meet_log(m, u["id"], "move", **{"from": {"date": before["date"], "time": before["time"]},
                                        "to": {"date": m["date"], "time": m["time"]},
                                        "reason": (p.get("reason") or "Изменение параметров совещания").strip()[:500]})
    elif any(before[k] != m[k] for k in ("chair", "secretary", "attendees")):
        _meet_log(m, u["id"], "params", note="Изменены председатель, секретарь или состав участников")
    db.save_ent(cur, "meeting", m)
    db.audit(cur, "AUDIT", "info", u["id"], "meetings", "Совещание изменено", "Совещание", m["id"], {"code": m["code"]})
    invite = list(dict.fromkeys([m["chair"], m["secretary"]] + (m.get("attendees") or [])))
    was = set([before["chair"], before["secretary"]] + (before["attendees"] or []))
    added = [x for x in invite if x not in was and x != u["id"]]
    if added:
        notify_many(db, cur, added, "meeting.invite", "Приглашение: %s" % m["title"],
                    "%s · %s %s" % (m["code"], _fd(m["date"]), m["time"]), "Совещание", m["id"],
                    dedupe_suffix="invite")
    moved = (before["date"], before["time"]) != (m["date"], m["time"])
    if moved or set(invite) != was:
        # перенос совещания или изменение состава: напоминания пересчитываются от новой даты (ТЗ §2.5, M12 §8.2)
        offs = sorted({r["offset_min"] for r in reminders_of(db, cur, "meeting", m["id"])}) or DEFAULT_REMINDERS
        reminders_set(db, cur, "meeting", m, invite, offs, u["id"])
    if moved:
        notify_many(db, cur, [x for x in invite if x != u["id"]], "meeting.reminder",
                    "Совещание перенесено: %s" % m["title"],
                    "%s · новое время %s %s" % (m["code"], _fd(m["date"]), m["time"]), "Совещание", m["id"],
                    dedupe_suffix="move:%s:%s" % (m["date"], m["time"]))
    return m, msg("Совещание обновлено", "Участники оповещены, напоминания пересчитаны" if (moved or added) else "")

@reg("meeting.conduct")
def h_meeting_conduct(db, cur, u, p):
    m = get_req(db, cur, "meeting", p["id"]); guard(db, cur, u, "meetings", "edit", m)
    if m["status"] == MEET_CANCELLED: raise RpcError("Отменённое совещание нельзя провести — возобновите его")
    if m["date"] > dISO(today()): raise RpcError("Нельзя провести совещание раньше его даты")
    m["status"] = "Проведено"
    pr = prot_of(m)
    if pr["status"] == "Подготовка": pr["status"] = "Черновик"   # ТЗ §2.9: черновик протокола после проведения
    m["protocol"] = pr
    db.save_ent(cur, "meeting", m)
    db.audit(cur, "BUSINESS", "info", u["id"], "meetings", "Совещание проведено", "Совещание", m["id"], {"code": m["code"]})
    return m, msg("Статус: «Проведено»", "Зафиксируйте решения и утвердите протокол")

@reg("meeting.approve")
def h_meeting_approve(db, cur, u, p):
    """Утверждение протокола (совместимость с M16 §2.8): единый контур §2.9 с версией и историей."""
    m = get_req(db, cur, "meeting", p["id"]); guard(db, cur, u, "meetings", "approve", m)
    if m["status"] != "Проведено": raise RpcError("Утверждать можно протокол проведённого совещания")
    _protocol_approve(db, cur, u, m, p.get("note", ""))
    return m, msg("Протокол утверждён", "Сохранена отдельная версия, участники оповещены")

@reg("meeting.agenda.add")
def h_meeting_agenda(db, cur, u, p):
    m = get_req(db, cur, "meeting", p["id"]); guard(db, cur, u, "meetings", "edit", m); meet_active(m)
    req(p, "title", "reporter")
    m["agenda"].append({"n": len(m["agenda"]) + 1, "title": p["title"][:300], "reporter": p["reporter"]})
    db.save_ent(cur, "meeting", m)
    db.audit(cur, "AUDIT", "info", u["id"], "meetings", "Добавлен вопрос повестки", "Совещание", m["id"], {"code": m["code"]})
    return m, msg("Вопрос добавлен")

DIRECTIVE_PRIORITIES = ("Критический", "Высокий", "Средний", "Низкий")

def dec_task_ids(d):
    """Связи решения с поручениями: одно решение может порождать несколько задач (ТЗ §2.10)."""
    ids = d.get("taskIds")
    if isinstance(ids, list) and ids: return [x for x in ids if x]
    return [d["taskId"]] if d.get("taskId") else []

def _directive_links(db, cur, m, d):
    """Ссылки поручения на совещание, протокол и пункт решения (ТЗ §2.10)."""
    pr = prot_of(m)
    n = next((i + 1 for i, x in enumerate(m.get("decisions") or []) if x["id"] == d["id"]), None)
    ver = next((v for v in reversed(pr["versions"]) if v["v"] == pr["version"]), None)
    return {"meetingId": m["id"], "meetingCode": m["code"], "decisionId": d["id"], "decisionN": n,
            "agendaN": d.get("agendaN"), "protocolV": pr["version"] or None,
            "protocolDoc": ({"docId": ver["docId"], "docCode": ver["docCode"], "docV": ver["docV"]} if ver else None)}

def _directive_from_decision(db, cur, u, m, d, p=None):
    """Создание поручения из решения совещания: задача M02 со всеми полями ТЗ §2.10."""
    guard(db, cur, u, "tasks", "create")
    p = p or {}
    assignee = p.get("assignee") or d.get("resp")
    if not assignee: raise RpcError("Не указан исполнитель поручения", "Поручение")
    cur.execute("SELECT id FROM users WHERE id=%s AND NOT blocked", (assignee,))
    if not cur.fetchone(): raise RpcError("Исполнитель не найден или заблокирован: %s" % assignee, "Поручение")
    co = []
    for uid_ in (p.get("coassignees") or [])[:10]:
        if uid_ == assignee or uid_ in co: continue
        cur.execute("SELECT id FROM users WHERE id=%s AND NOT blocked", (uid_,))
        if not cur.fetchone(): raise RpcError("Соисполнитель не найден: %s" % uid_, "Поручение")
        co.append(uid_)
    due = p.get("due") or d.get("due")
    if not due: raise RpcError("Не указан срок исполнения поручения", "Поручение")
    check_date(due, "дата срока поручения")
    priority = p.get("priority") or "Высокий"
    if priority not in DIRECTIVE_PRIORITIES: raise RpcError("Недопустимый приоритет: %s" % priority, "Поручение")
    pid = p.get("projectId")
    if pid:
        pr_ = get_req(db, cur, "project", pid)
        if pr_["status"] in ("Архивный", "Отменён", "Завершён"):
            raise RpcError("Проект закрыт для новых поручений: %s (%s)" % (pr_["code"], pr_["status"]), "Поручение")
    else:
        cur.execute("SELECT id FROM projects WHERE pm=%s AND status NOT IN ('Архивный','Отменён','Завершён') ORDER BY code LIMIT 1", (assignee,))
        r = cur.fetchone()
        if not r:
            cur.execute("SELECT id FROM projects WHERE status NOT IN ('Архивный','Отменён','Завершён') ORDER BY code LIMIT 1")
            r = cur.fetchone()
        if not r: raise RpcError("Нет доступного проекта для поручения")
        pid = r["id"]
    links = _directive_links(db, cur, m, d)
    title = (p.get("title") or d["text"]).strip()[:300]
    if not title: raise RpcError("Не заполнено наименование поручения", "Поручение")
    src = (p.get("source") or "Решение №%s совещания %s от %s%s" % (
        links["decisionN"], m["code"], _fd(m["date"]),
        " · пункт %s повестки" % links["agendaN"] if links.get("agendaN") else ""))[:500]
    desc = (p.get("desc") or ("Поручение по решению совещания %s.\nФормулировка решения: %s" % (m["code"], d["text"])))[:4000]
    criteria = (p.get("criteria") or "")[:2000]
    posted = today()
    tn = next_seq(db, cur, "task")
    t = dict(id=uid("t"), code="З-%03d" % tn, projectId=pid, parent=None, title=title, type="directive",
             status="Готова к выполнению", priority=priority, assignee=assignee, start=dISO(posted), end=due,
             planHours=max(0, int(p.get("planHours", 8) or 0)), progress=0, deps=[], comments=[], result="",
             blockReason="", createdAt=now_ms(), createdBy=u["id"], meetingId=m["id"], riskId=None, desc=desc,
             directive=dict(source=src, criteria=criteria, coassignees=co, postedAt=now_ms(), postedBy=u["id"],
                            postedDate=dISO(posted), dueHistory=[], acceptance=None, docs=[], factDate=None,
                            comment="", **links))
    db.save_ent(cur, "task", t)
    cur.execute("INSERT INTO meeting_tasks(id,meeting_id,decision_id,task_id,ts,by_user) VALUES(%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (decision_id,task_id) DO NOTHING",
                (uid("mt"), m["id"], d["id"], t["id"], now_ms(), u["id"]))
    ids = dec_task_ids(d) + [t["id"]]
    d["taskIds"] = ids
    d["taskId"] = ids[0]
    notify(db, cur, assignee, "directive.assigned", "Поручение: %s" % t["title"],
           "%s · по решению совещания %s · срок %s" % (t["code"], m["code"], _fd(t["end"])),
           "Поручение", t["id"], dedupe="directive.assigned:%s:%s" % (assignee, t["id"]))
    for uid_ in co:
        if uid_ != u["id"]:
            notify(db, cur, uid_, "directive.assigned", "Соисполнитель поручения: %s" % t["title"],
                   "%s · совещание %s · срок %s" % (t["code"], m["code"], _fd(t["end"])),
                   "Поручение", t["id"], dedupe="directive.coassigned:%s:%s" % (uid_, t["id"]))
    db.audit(cur, "BUSINESS", "info", u["id"], "meetings", "Создано поручение из решения совещания", "Совещание", m["id"],
             {"meeting": m["code"], "task": t["code"], "decision": links["decisionN"], "assignee": assignee,
              "coassignees": co, "due": due, "source": src, "event": "MEETING_DIRECTIVE_CREATED",
              "events": ["MEETING_DIRECTIVE_CREATED", "MEETING_TASK_CREATED"]})
    LOG.info("Поручение %s создано из решения №%s совещания %s (исполнитель %s, срок %s)",
             t["code"], links["decisionN"], m["code"], assignee, due)
    return t

@reg("meeting.decision.add")
def h_meeting_decision(db, cur, u, p):
    m = get_req(db, cur, "meeting", p["id"]); guard(db, cur, u, "meetings", "edit", m)
    req(p, "text", "resp", "due"); check_date(p["due"])
    agenda_n = p.get("agendaN")
    if agenda_n is not None:
        agenda_n = int(agenda_n)
        if not any(a["n"] == agenda_n for a in m["agenda"]): raise RpcError("Пункт повестки не найден: %s" % agenda_n)
    d = {"id": uid("md"), "text": p["text"][:1000], "resp": p["resp"], "due": p["due"],
         "taskId": None, "taskIds": [], "agendaN": agenda_n}
    m["decisions"].append(d)
    t = None
    if p.get("createTask"): t = _directive_from_decision(db, cur, u, m, d, p)
    db.save_ent(cur, "meeting", m)
    db.audit(cur, "AUDIT", "info", u["id"], "meetings", "Зафиксировано решение", "Совещание", m["id"], {"code": m["code"]})
    return m, msg("Поручение создано в M02" if t else "Решение зафиксировано", "Контроль срока — в M07 и на дашборде" if t else "")

@reg("meeting.decision.toTask")
def h_meeting_decision_task(db, cur, u, p):
    """Преобразование решения в поручение. Повторное создание — только с явным подтверждением (ТЗ §2.10)."""
    m = get_req(db, cur, "meeting", p["id"]); guard(db, cur, u, "meetings", "edit", m)
    d = next((x for x in m["decisions"] if x["id"] == p["did"]), None)
    if not d: raise RpcError("Решение не найдено")
    existing = [x for x in dec_task_ids(d) if db.get_ent(cur, "task", x)]
    if existing and not p.get("confirm"):
        codes = ", ".join(db.get_ent(cur, "task", x)["code"] for x in existing)
        raise RpcError("По этому решению уже создано поручение (%s). Создание ещё одного требует явного подтверждения."
                       % codes, "409 · Требуется подтверждение", 409, "NEED_CONFIRM")
    t = _directive_from_decision(db, cur, u, m, d, p)
    db.save_ent(cur, "meeting", m)
    return m, msg("Поручение создано в M02", "Контроль срока — в M07 и на дашборде")

@reg("meeting.decision.update")
def h_meeting_decision_update(db, cur, u, p):
    """Правка решения не создаёт поручение повторно (ТЗ §2.10): связи сохраняются."""
    m = get_req(db, cur, "meeting", p["id"]); guard(db, cur, u, "meetings", "edit", m)
    d = next((x for x in m["decisions"] if x["id"] == p["did"]), None)
    if not d: raise RpcError("Решение не найдено")
    if "text" in p: d["text"] = (p["text"] or "")[:1000]
    if "resp" in p: d["resp"] = p["resp"]
    if "due" in p: check_date(p["due"]); d["due"] = p["due"]
    if "agendaN" in p:
        n = p["agendaN"]
        if n is not None:
            n = int(n)
            if not any(a["n"] == n for a in m["agenda"]): raise RpcError("Пункт повестки не найден: %s" % n)
        d["agendaN"] = n
    d["taskIds"] = [x for x in dec_task_ids(d) if db.get_ent(cur, "task", x)]
    d["taskId"] = d["taskIds"][0] if d["taskIds"] else None
    db.save_ent(cur, "meeting", m)
    db.audit(cur, "AUDIT", "info", u["id"], "meetings", "Решение изменено", "Совещание", m["id"],
             {"code": m["code"], "decision": d["id"], "tasks": len(d["taskIds"]), "event": "MEETING_DECISION_UPDATED"})
    return m, msg("Решение изменено", "Созданные поручения сохранены, повторное создание не выполняется")
@reg("meeting.delete")
def h_meeting_delete(db, cur, u, p):
    m = get_req(db, cur, "meeting", p["id"]); guard(db, cur, u, "meetings", "delete", m)
    db.del_ent(cur, "meeting", m["id"])
    db.audit(cur, "AUDIT", "warning", u["id"], "meetings", "Совещание удалено", "Совещание", m["id"], {"code": m["code"]})
    return True, msg("Совещание удалено", "Созданные поручения остались в M02")

# ---------------- M16 §2.5: планирование совещаний ----------------
# ТЗ §2.5: создание совещания, дата и время, повестка, участники, председатель и секретарь,
# предварительные материалы, связанные проекты, изменение параметров, перенос, отмена,
# отображение в общем календаре и регулярные совещания с повторением по расписанию.
# Каждое фактически проведённое мероприятие серии имеет самостоятельную карточку
# и отдельный протокол (§2.5, абз. посл.).

MEET_FREQ = ("none", "daily", "weekly", "monthly")
MEET_FREQ_NAMES = {"none": "Без повторения", "daily": "Ежедневно", "weekly": "Еженедельно", "monthly": "Ежемесячно"}
MEET_MATERIALS_MAX = 20
MEET_PROJECTS_MAX = 20
MEET_SERIES_HORIZON = 30      # дней вперёд: горизонт автоматического создания дат серии
MEET_SERIES_MAX = 60          # предел карточек за одну операцию (защита от runaway)
MEET_CANCELLED = "Отменено"
WEEKDAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MEET_PLAN_ACT = {"move": "Перенос", "cancel": "Отмена", "restore": "Возобновление",
                 "params": "Изменение параметров", "series": "Регулярность",
                 "skip": "Пропуск даты", "projects": "Связанные проекты"}

def _ri(v, d=0):
    try: return int(v)
    except Exception: return d

def rec_of(m):
    """Правило повторения совещания (для записей прежних версий — значения по умолчанию)."""
    r = {"freq": "none", "interval": 1, "weekdays": [], "monthDay": 0, "until": "", "count": 0,
         "seriesId": "", "seriesN": 1, "master": False, "skipped": []}
    r.update(m.get("recurrence") or {})
    if r.get("freq") not in MEET_FREQ: r["freq"] = "none"
    r["interval"] = max(1, _ri(r.get("interval"), 1))
    r["weekdays"] = sorted({w for w in (_ri(x) for x in (r.get("weekdays") or [])) if 1 <= w <= 7})
    r["monthDay"] = _ri(r.get("monthDay"))
    r["count"] = _ri(r.get("count"))
    r["seriesN"] = max(1, _ri(r.get("seriesN"), 1))
    r["master"] = bool(r.get("master"))
    r["until"] = r.get("until") or ""
    r["seriesId"] = r.get("seriesId") or ""
    if not isinstance(r.get("skipped"), list): r["skipped"] = []
    return r

def plan_of(m):
    """Журнал планирования: переносы, отмены, возобновления, изменение параметров, серии."""
    return m.get("planLog") if isinstance(m.get("planLog"), list) else []

def materials_of(m):
    return m.get("materials") if isinstance(m.get("materials"), list) else []

def mprojects_of(m):
    return m.get("projects") if isinstance(m.get("projects"), list) else []

def meet_active(m):
    if m.get("status") == MEET_CANCELLED:
        raise RpcError("Совещание отменено — сначала возобновите его (кнопка «Возобновить»)", "Совещание отменено")
    return m

def rec_text(r):
    """Человекочитаемое описание расписания (карточка, реестр, протокол)."""
    if r["freq"] == "none": return MEET_FREQ_NAMES["none"]
    iv = r["interval"]
    if r["freq"] == "daily":
        s = "Каждые %d дн" % iv if iv > 1 else "Ежедневно"
    elif r["freq"] == "weekly":
        s = "Каждые %d нед" % iv if iv > 1 else "Еженедельно"
        if r["weekdays"]: s += ": " + ", ".join(WEEKDAY_NAMES[w - 1] for w in r["weekdays"])
    else:
        s = "Каждые %d мес" % iv if iv > 1 else "Ежемесячно"
        if r["monthDay"]: s += ", %d числа" % r["monthDay"]
    if r["until"]: s += " · до %s" % _fd(r["until"])
    if r["count"]: s += " · всего %d" % r["count"]
    return s

def rec_validate(p, base_date=None):
    """Проверка правила повторения (§2.5: повторение по заданному расписанию)."""
    if p is None: p = {}
    if not isinstance(p, dict): raise RpcError("Расписание задаётся объектом: периодичность, интервал, окончание")
    if p.get("freq") is not None and p.get("freq") not in MEET_FREQ:
        raise RpcError("Неизвестная периодичность: %s (допустимо: %s)" % (p.get("freq"), ", ".join(MEET_FREQ)))
    r = rec_of({"recurrence": p})
    if r["freq"] == "none":
        return {"freq": "none", "interval": 1, "weekdays": [], "monthDay": 0, "until": "", "count": 0}
    if r["interval"] > 30: raise RpcError("Интервал повторения — от 1 до 30")
    if r["freq"] == "weekly" and not r["weekdays"]:
        r["weekdays"] = [parseD(base_date).isoweekday()] if base_date else [1]
    if r["freq"] == "monthly":
        if not r["monthDay"]: r["monthDay"] = parseD(base_date).day if base_date else 1
        if not 1 <= r["monthDay"] <= 31: raise RpcError("День месяца — от 1 до 31")
    if r["until"]:
        check_date(r["until"], "дата окончания серии")
        if base_date and r["until"] < base_date:
            raise RpcError("Дата окончания серии раньше даты первого совещания")
    if r["count"] and not 1 <= r["count"] <= 104:
        raise RpcError("Число совещаний серии — от 1 до 104")
    return {"freq": r["freq"], "interval": r["interval"], "weekdays": r["weekdays"],
            "monthDay": r["monthDay"], "until": r["until"], "count": r["count"]}

def rec_next_date(date_s, r):
    """Следующая дата после date_s по правилу повторения."""
    f, iv = r["freq"], max(1, r["interval"])
    d = parseD(date_s)
    if f == "daily": return dISO(d + datetime.timedelta(days=iv))
    if f == "weekly":
        wds = sorted(set(r["weekdays"] or [d.isoweekday()]))
        cw = d.isoweekday()
        nxt = [w for w in wds if w > cw]
        if nxt: return dISO(d + datetime.timedelta(days=nxt[0] - cw))
        return dISO(d + datetime.timedelta(days=7 * iv - (cw - wds[0])))
    if f == "monthly":
        day = r["monthDay"] or d.day
        y, mo = d.year, d.month + iv
        while mo > 12: y += 1; mo -= 12
        last = calendar.monthrange(y, mo)[1]
        return dISO(datetime.date(y, mo, min(day, last)))
    return None

def rec_dates(start, r, existing, limit, horizon=None):
    """Даты серии после start: без повторов, с учётом пропуска, окончания и горизонта."""
    out, d, guard_n = [], start, 0
    skip = {s.get("date") for s in (r.get("skipped") or []) if isinstance(s, dict)}
    while len(out) < limit and guard_n < 500:
        guard_n += 1
        nd = rec_next_date(d, r)
        if not nd: break
        d = nd
        if r.get("until") and nd > r["until"]: break
        if horizon and nd > horizon: break
        if nd in existing or nd in skip: continue
        out.append(nd)
    return out

def series_cards(db, cur, series_id):
    if not series_id: return []
    cur.execute("SELECT * FROM meetings WHERE series_id=%s ORDER BY date, code", (series_id,))
    return [db.row2obj("meeting", r) for r in cur.fetchall()]

def _meet_occurrence(db, cur, master, date_s, n, by):
    """Самостоятельная карточка очередного совещания серии: своя повестка и свой протокол."""
    r = rec_of(master)
    seq = next_seq(db, cur, "meet")
    m = dict(id=uid("m"), code="СОВ-%d-%03d" % (parseD(date_s).year, seq), title=master["title"],
             type=master["type"], date=date_s, time=master["time"], chair=master["chair"],
             secretary=master["secretary"], attendees=list(master.get("attendees") or []),
             status="Запланировано", agenda=[dict(a) for a in (master.get("agenda") or [])],
             decisions=[], protocol=protocol_default(),
             materials=[dict(x) for x in materials_of(master)], projects=list(mprojects_of(master)),
             recurrence=dict(r, master=False, seriesN=n, skipped=[]), seriesId=r["seriesId"],
             planLog=[{"ts": now_ms(), "by": by, "action": "series", "date": date_s,
                       "note": "Совещание №%d серии %s" % (n, master["code"])}])
    db.save_ent(cur, "meeting", m)
    invite = list(dict.fromkeys([m["chair"], m["secretary"]] + m["attendees"]))
    notify_many(db, cur, [x for x in invite if x != by], "meeting.invite",
                "Приглашение: %s" % m["title"],
                "%s · %s %s · серия %s" % (m["code"], _fd(m["date"]), m["time"], master["code"]),
                "Совещание", m["id"], dedupe_suffix="invite")
    reminders_set(db, cur, "meeting", m, invite, DEFAULT_REMINDERS, by)
    db.audit(cur, "BUSINESS", "info", by, "meetings", "Создано очередное совещание серии",
             "Совещание", m["id"], {"code": m["code"], "series": r["seriesId"], "n": n,
                                    "date": date_s, "event": "MEETING_SERIES_OCCURRENCE"})
    return m

def _series_generate(db, cur, master, limit=None, horizon=None, by="system"):
    """Создание недостающих карточек серии (идемпотентно: существующие даты не дублируются)."""
    r = rec_of(master)
    if r["freq"] == "none" or not r["master"] or not r["seriesId"]: return []
    cards = series_cards(db, cur, r["seriesId"])
    have = {c["date"] for c in cards}
    cap = MEET_SERIES_MAX if limit is None else max(0, min(MEET_SERIES_MAX, _ri(limit)))
    if r["count"]: cap = min(cap, max(0, r["count"] - len(cards)))
    if cap <= 0: return []
    dates = rec_dates(master["date"], r, have, cap, horizon)
    n = max([rec_of(c)["seriesN"] for c in cards] or [1])
    made = []
    for d in dates:
        n += 1
        made.append(_meet_occurrence(db, cur, master, d, n, by))
    return made

def meeting_series_cycle(db, cur):
    """Планировщик: ближайшие даты регулярных совещаний материализуются отдельными карточками."""
    cur.execute("SELECT * FROM meetings WHERE series_id<>'' AND status<>%s ORDER BY date", (MEET_CANCELLED,))
    rows = [db.row2obj("meeting", r) for r in cur.fetchall()]
    made = 0
    for m in rows:
        r = rec_of(m)
        if not r["master"] or r["freq"] == "none": continue
        made += len(_series_generate(db, cur, m, horizon=D(MEET_SERIES_HORIZON), by="system"))
        if made >= MEET_SERIES_MAX: break
    if made:
        LOG.info("Регулярные совещания: создано карточек %d (горизонт %d дн)", made, MEET_SERIES_HORIZON)
        db.audit(cur, "BUSINESS", "info", "system", "meetings",
                 "Регулярные совещания: создано карточек %d" % made, "Совещание", "",
                 {"count": made, "horizon": MEET_SERIES_HORIZON, "event": "MEETING_SERIES_AUTO"})
    return {"created": made}

def _meet_projects_validate(db, cur, ids):
    out = []
    for pid in list(dict.fromkeys(ids or [])):
        if not pid: continue
        get_req(db, cur, "project", pid)
        out.append(pid)
    if len(out) > MEET_PROJECTS_MAX:
        raise RpcError("Связанных проектов — не более %d" % MEET_PROJECTS_MAX)
    return out

def _meet_log(m, by, action, **kw):
    log = plan_of(m)
    rec = {"ts": now_ms(), "by": by, "action": action}
    rec.update(kw)
    log.append(rec)
    m["planLog"] = log[-100:]
    return rec

@reg("meeting.materials.add")
def h_meeting_material_add(db, cur, u, p):
    """Предварительные материалы совещания (§2.5): документ M09 либо ссылка (ЛВС/внешний ресурс)."""
    m = get_req(db, cur, "meeting", p["id"]); guard(db, cur, u, "meetings", "edit", m); meet_active(m)
    mats = materials_of(m)
    if len(mats) >= MEET_MATERIALS_MAX:
        raise RpcError("Материалов к совещанию — не более %d" % MEET_MATERIALS_MAX)
    note = (p.get("note") or "").strip()[:300]
    if p.get("docId"):
        d = get_req(db, cur, "doc", p["docId"])
        if any(x.get("docId") == d["id"] for x in mats):
            raise RpcError("Этот документ уже прикреплён к совещанию")
        item = {"id": uid("mm"), "kind": "doc", "docId": d["id"], "name": "%s · %s" % (d["code"], d["title"]),
                "url": "", "note": note, "addedAt": now_ms(), "addedBy": u["id"]}
    else:
        req(p, "name", "url")
        url = p["url"].strip()[:500]
        if re.match(r"^\s*(javascript|data|vbscript):", url, re.I):
            raise RpcError("Недопустимая схема ссылки")
        if any(x.get("url") == url for x in mats):
            raise RpcError("Эта ссылка уже прикреплена к совещанию")
        item = {"id": uid("mm"), "kind": "link", "docId": None, "name": p["name"].strip()[:200],
                "url": url, "note": note, "addedAt": now_ms(), "addedBy": u["id"]}
    mats.append(item); m["materials"] = mats
    db.save_ent(cur, "meeting", m)
    db.audit(cur, "BUSINESS", "info", u["id"], "meetings", "Прикреплён материал к совещанию",
             "Совещание", m["id"], {"code": m["code"], "material": item["name"], "kind": item["kind"],
                                    "event": "MEETING_MATERIAL_ADDED"})
    invite = list(dict.fromkeys([m["chair"], m["secretary"]] + (m.get("attendees") or [])))
    notify_many(db, cur, [x for x in invite if x != u["id"]], "meeting.materials",
                "Материалы к совещанию: %s" % m["title"],
                "%s · %s · %s" % (m["code"], item["name"], _fd(m["date"])), "Совещание", m["id"],
                dedupe_suffix="mat:%s" % item["id"])
    return m, msg("Материал прикреплён", "Участники оповещены · всего материалов: %d" % len(mats))

@reg("meeting.materials.remove")
def h_meeting_material_remove(db, cur, u, p):
    m = get_req(db, cur, "meeting", p["id"]); guard(db, cur, u, "meetings", "edit", m)
    req(p, "materialId")
    mats = materials_of(m)
    keep = [x for x in mats if x.get("id") != p["materialId"]]
    if len(keep) == len(mats): raise RpcError("Материал не найден в списке совещания")
    m["materials"] = keep
    db.save_ent(cur, "meeting", m)
    db.audit(cur, "AUDIT", "info", u["id"], "meetings", "Материал откреплён от совещания",
             "Совещание", m["id"], {"code": m["code"], "materialId": p["materialId"],
                                    "event": "MEETING_MATERIAL_REMOVED"})
    return m, msg("Материал откреплён", "Осталось материалов: %d" % len(keep))

@reg("meeting.projects.set")
def h_meeting_projects_set(db, cur, u, p):
    """Регистрация связанных проектов совещания (§2.5)."""
    m = get_req(db, cur, "meeting", p["id"]); guard(db, cur, u, "meetings", "edit", m); meet_active(m)
    ids = _meet_projects_validate(db, cur, p.get("projects") or [])
    was = set(mprojects_of(m))
    m["projects"] = ids
    _meet_log(m, u["id"], "projects", note="Связанные проекты: %d" % len(ids))
    db.save_ent(cur, "meeting", m)
    db.audit(cur, "BUSINESS", "info", u["id"], "meetings", "Зарегистрированы связанные проекты",
             "Совещание", m["id"], {"code": m["code"], "projects": ids, "added": sorted(set(ids) - was),
                                    "event": "MEETING_PROJECTS"})
    return m, msg("Связанные проекты сохранены", "Проектов: %d" % len(ids))

@reg("meeting.reschedule")
def h_meeting_reschedule(db, cur, u, p):
    """Перенос совещания с основанием, историей и пересчётом напоминаний (§2.5, M12 §8.2)."""
    m = get_req(db, cur, "meeting", p["id"]); guard(db, cur, u, "meetings", "edit", m); meet_active(m)
    req(p, "date", "reason")
    if m["status"] != "Запланировано":
        raise RpcError("Переносить можно только запланированное совещание")
    check_date(p["date"])
    reason = p["reason"].strip()[:500]
    if len(reason) < 3: raise RpcError("Укажите основание переноса (не менее 3 символов)")
    new_time = check_time(p.get("time") or m["time"])
    if (p["date"], new_time) == (m["date"], m["time"]):
        raise RpcError("Дата и время не изменились — перенос не требуется")
    was = {"date": m["date"], "time": m["time"]}
    m["date"], m["time"] = p["date"], new_time
    _meet_log(m, u["id"], "move", **{"from": was, "to": {"date": m["date"], "time": m["time"]}, "reason": reason})
    db.save_ent(cur, "meeting", m)
    invite = list(dict.fromkeys([m["chair"], m["secretary"]] + (m.get("attendees") or [])))
    offs = sorted({r["offset_min"] for r in reminders_of(db, cur, "meeting", m["id"])}) or DEFAULT_REMINDERS
    reminders_set(db, cur, "meeting", m, invite, offs, u["id"])
    notify_many(db, cur, [x for x in invite if x != u["id"]], "meeting.moved",
                "Совещание перенесено: %s" % m["title"],
                "%s · %s %s (было %s %s) · %s" % (m["code"], _fd(m["date"]), m["time"],
                                                  _fd(was["date"]), was["time"], reason),
                "Совещание", m["id"], dedupe_suffix="move:%s:%s" % (m["date"], m["time"]))
    db.audit(cur, "BUSINESS", "warning", u["id"], "meetings", "Совещание перенесено",
             "Совещание", m["id"], {"code": m["code"], "from": was, "to": {"date": m["date"], "time": m["time"]},
                                    "reason": reason, "event": "MEETING_RESCHEDULED"})
    return m, msg("Совещание перенесено", "%s %s · участники оповещены, напоминания пересчитаны"
                  % (_fd(m["date"]), m["time"]))

@reg("meeting.cancel")
def h_meeting_cancel(db, cur, u, p):
    """Отмена совещания с основанием: статус, снятие напоминаний, оповещение (§2.5)."""
    m = get_req(db, cur, "meeting", p["id"]); guard(db, cur, u, "meetings", "edit", m)
    req(p, "reason")
    reason = p["reason"].strip()[:500]
    if len(reason) < 3: raise RpcError("Укажите основание отмены (не менее 3 символов)")
    if m["status"] == MEET_CANCELLED: raise RpcError("Совещание уже отменено")
    if m["status"] != "Запланировано":
        raise RpcError("Отменить можно только запланированное совещание")
    was = {"date": m["date"], "time": m["time"], "status": m["status"]}
    m["status"] = MEET_CANCELLED
    _meet_log(m, u["id"], "cancel", **{"from": was, "reason": reason})
    db.save_ent(cur, "meeting", m)
    cur.execute("DELETE FROM reminders WHERE owner_type='meeting' AND owner_id=%s", (m["id"],))
    invite = list(dict.fromkeys([m["chair"], m["secretary"]] + (m.get("attendees") or [])))
    notify_many(db, cur, [x for x in invite if x != u["id"]], "meeting.cancelled",
                "Совещание отменено: %s" % m["title"],
                "%s · %s %s · %s" % (m["code"], _fd(m["date"]), m["time"], reason),
                "Совещание", m["id"], dedupe_suffix="cancel")
    db.audit(cur, "BUSINESS", "warning", u["id"], "meetings", "Совещание отменено",
             "Совещание", m["id"], {"code": m["code"], "date": m["date"], "reason": reason,
                                    "event": "MEETING_CANCELLED"})
    return m, msg("Совещание отменено", "Напоминания сняты, участники оповещены · основание в истории планирования")

@reg("meeting.restore")
def h_meeting_restore(db, cur, u, p):
    """Возобновление отменённого совещания (§2.5: изменение параметров и возврат в расписание)."""
    m = get_req(db, cur, "meeting", p["id"]); guard(db, cur, u, "meetings", "edit", m)
    if m["status"] != MEET_CANCELLED: raise RpcError("Возобновить можно только отменённое совещание")
    reason = (p.get("reason") or "Возобновлено по решению председателя").strip()[:500]
    if p.get("date"):
        check_date(p["date"]); m["date"] = p["date"]
    if p.get("time"):
        m["time"] = check_time(p["time"])
    m["status"] = "Запланировано"
    _meet_log(m, u["id"], "restore", reason=reason, date=m["date"])
    db.save_ent(cur, "meeting", m)
    invite = list(dict.fromkeys([m["chair"], m["secretary"]] + (m.get("attendees") or [])))
    reminders_set(db, cur, "meeting", m, invite, DEFAULT_REMINDERS, u["id"])
    notify_many(db, cur, [x for x in invite if x != u["id"]], "meeting.moved",
                "Совещание возобновлено: %s" % m["title"],
                "%s · %s %s" % (m["code"], _fd(m["date"]), m["time"]), "Совещание", m["id"],
                dedupe_suffix="restore:%s" % m["date"])
    db.audit(cur, "BUSINESS", "info", u["id"], "meetings", "Совещание возобновлено",
             "Совещание", m["id"], {"code": m["code"], "date": m["date"], "reason": reason,
                                    "event": "MEETING_RESTORED"})
    return m, msg("Совещание возобновлено", "Напоминания восстановлены, участники оповещены")

@reg("meeting.series.update")
def h_meeting_series_update(db, cur, u, p):
    """Изменение расписания регулярного совещания (головная карточка серии)."""
    m = get_req(db, cur, "meeting", p["id"]); guard(db, cur, u, "meetings", "edit", m); meet_active(m)
    r = rec_of(m)
    if r["seriesId"] and not r["master"]:
        raise RpcError("Расписание изменяется в головной карточке серии %s" % r["seriesId"])
    rule = rec_validate(p.get("rule"), m["date"])
    if rule["freq"] == "none":
        r.update(rule); r["master"] = False
        m["recurrence"] = r
        _meet_log(m, u["id"], "series", note="Повторение отключено")
        db.save_ent(cur, "meeting", m)
        db.audit(cur, "AUDIT", "info", u["id"], "meetings", "Повторение совещания отключено",
                 "Совещание", m["id"], {"code": m["code"], "event": "MEETING_SERIES_OFF"})
        return m, msg("Повторение отключено", "Созданные совещания серии сохранены")
    if not r["seriesId"]:
        r["seriesId"] = uid("ms"); m["seriesId"] = r["seriesId"]
    r.update(rule); r["master"] = True; r.setdefault("seriesN", 1)
    m["recurrence"] = r
    _meet_log(m, u["id"], "series", note="Расписание: " + rec_text(r))
    changed = []
    if p.get("applyToFuture"):
        for c in series_cards(db, cur, r["seriesId"]):
            if c["id"] == m["id"] or c["date"] <= m["date"] or c["status"] != "Запланировано": continue
            cr = rec_of(c); cr.update(rule); cr["master"] = False
            c["recurrence"] = cr
            c["time"] = m["time"]; c["chair"] = m["chair"]; c["secretary"] = m["secretary"]
            c["attendees"] = list(m.get("attendees") or [])
            _meet_log(c, u["id"], "series", note="Расписание серии изменено: " + rec_text(r))
            db.save_ent(cur, "meeting", c)
            changed.append(c["code"])
    db.save_ent(cur, "meeting", m)
    made = _series_generate(db, cur, m, by=u["id"])
    db.audit(cur, "BUSINESS", "info", u["id"], "meetings", "Изменено расписание регулярного совещания",
             "Совещание", m["id"], {"code": m["code"], "rule": rule, "created": len(made),
                                    "updated": changed, "event": "MEETING_SERIES_UPDATED"})
    return m, msg("Расписание сохранено", rec_text(r) + (" · создано карточек: %d" % len(made) if made else "")
                  + (" · обновлено будущих: %d" % len(changed) if changed else ""))

@reg("meeting.series.generate")
def h_meeting_series_generate(db, cur, u, p):
    """Создание очередных совещаний серии отдельными карточками (§2.5)."""
    m = get_req(db, cur, "meeting", p["id"]); guard(db, cur, u, "meetings", "edit", m); meet_active(m)
    r = rec_of(m)
    if not r["master"] or r["freq"] == "none":
        raise RpcError("Совещание не является головной карточкой регулярной серии")
    cnt = p.get("count")
    if cnt is not None and _ri(cnt) < 1: raise RpcError("Число создаваемых совещаний — не менее 1")
    made = _series_generate(db, cur, m, limit=cnt,
                            horizon=None if cnt is not None else D(MEET_SERIES_HORIZON), by=u["id"])
    db.audit(cur, "BUSINESS", "info", u["id"], "meetings", "Созданы очередные совещания серии",
             "Совещание", m["id"], {"code": m["code"], "count": len(made),
                                    "dates": [x["date"] for x in made], "event": "MEETING_SERIES_GENERATED"})
    return {"meeting": m, "created": [{"id": x["id"], "code": x["code"], "date": x["date"]} for x in made]}, \
        msg("Совещания серии созданы", "Новых карточек: %d · у каждой своя повестка и протокол" % len(made))

@reg("meeting.series.skip")
def h_meeting_series_skip(db, cur, u, p):
    """Пропуск даты серии: существующая карточка отменяется, будущая дата не создаётся (§2.5)."""
    m = get_req(db, cur, "meeting", p["id"]); guard(db, cur, u, "meetings", "edit", m)
    r = rec_of(m)
    if not r["master"]: raise RpcError("Пропуск даты выполняется в головной карточке серии")
    req(p, "date", "reason")
    check_date(p["date"])
    reason = p["reason"].strip()[:500]
    if len(reason) < 3: raise RpcError("Укажите основание пропуска (не менее 3 символов)")
    if p["date"] <= m["date"]: raise RpcError("Пропустить можно только дату после головного совещания")
    cards = [c for c in series_cards(db, cur, r["seriesId"]) if c["date"] == p["date"] and c["id"] != m["id"]]
    skipped = [s for s in (r.get("skipped") or []) if isinstance(s, dict) and s.get("date") != p["date"]]
    skipped.append({"date": p["date"], "reason": reason, "ts": now_ms(), "by": u["id"]})
    r["skipped"] = skipped[-100:]
    m["recurrence"] = r
    _meet_log(m, u["id"], "skip", date=p["date"], reason=reason)
    db.save_ent(cur, "meeting", m)
    cancelled = []
    for c in cards:
        if c["status"] != "Запланировано": continue
        c["status"] = MEET_CANCELLED
        _meet_log(c, u["id"], "cancel", reason="Пропуск по расписанию: " + reason, date=c["date"])
        db.save_ent(cur, "meeting", c)
        cur.execute("DELETE FROM reminders WHERE owner_type='meeting' AND owner_id=%s", (c["id"],))
        inv = list(dict.fromkeys([c["chair"], c["secretary"]] + (c.get("attendees") or [])))
        notify_many(db, cur, [x for x in inv if x != u["id"]], "meeting.cancelled",
                    "Совещание отменено: %s" % c["title"],
                    "%s · %s · пропуск по расписанию: %s" % (c["code"], _fd(c["date"]), reason),
                    "Совещание", c["id"], dedupe_suffix="skip")
        cancelled.append(c["code"])
    db.audit(cur, "BUSINESS", "warning", u["id"], "meetings", "Пропущена дата регулярного совещания",
             "Совещание", m["id"], {"code": m["code"], "date": p["date"], "reason": reason,
                                    "cancelled": cancelled, "event": "MEETING_SERIES_SKIP"})
    return m, msg("Дата серии пропущена", p["date"] + (" · отменено карточек: %d" % len(cancelled) if cancelled
                                                       else " · совещание на эту дату создаваться не будет"))

@reg("meeting.series.list")
def h_meeting_series_list(db, cur, u, p):
    """Состав серии: каждое проведённое мероприятие — самостоятельная карточка и протокол."""
    m = get_req(db, cur, "meeting", p["id"]); guard(db, cur, u, "meetings", "view", m)
    r = rec_of(m)
    sid = r["seriesId"] or m.get("seriesId") or ""
    cards = series_cards(db, cur, sid)
    items = [{"id": c["id"], "code": c["code"], "date": c["date"], "time": c["time"], "status": c["status"],
              "n": rec_of(c)["seriesN"], "master": rec_of(c)["master"], "protocol": prot_of(c)["status"],
              "protocolVersion": prot_of(c)["version"], "decisions": len(c.get("decisions") or []),
              "agenda": len(c.get("agenda") or [])} for c in cards]
    return {"seriesId": sid, "rule": r, "text": rec_text(r), "items": items,
            "planned": len([x for x in items if x["status"] == "Запланировано"]),
            "held": len([x for x in items if x["status"] != "Запланировано" and x["status"] != MEET_CANCELLED]),
            "cancelled": len([x for x in items if x["status"] == MEET_CANCELLED])}, None
# ---------------- Реестр поручений и контроль исполнения (ТЗ M16 §2.11, §19.5–19.6) ----------------
# Поручение — задача M02 (единая система задач). Реестр является представлением задач типа
# «поручение» с производным статусом, синхронизированным со статусом задачи, и отдельным
# контролем принятия результата руководителем: технически закрытая задача не означает,
# что решение совещания исполнено и принято.

DIRECTIVE_STATUSES = ["Не начато", "В работе", "Ожидает информации", "На проверке",
                      "Ожидает подтверждения", "Выполнено", "Просрочено", "Приостановлено", "Отменено"]
DIRECTIVE_ACCEPT = ("Принято", "На доработку")

def dir_of(t):
    """Реквизиты поручения из карточки задачи (для записей прежних версий — значения по умолчанию)."""
    d = {"source": "", "criteria": "", "coassignees": [], "postedAt": None, "postedBy": None,
         "postedDate": None, "meetingId": None, "meetingCode": "", "decisionId": None, "decisionN": None,
         "agendaN": None, "protocolV": None, "protocolDoc": None, "dueHistory": [], "acceptance": None,
         "docs": [], "factDate": None, "comment": "", "controller": None, "stateAt": None}
    d.update(t.get("directive") or {})
    for k in ("coassignees", "dueHistory", "docs"):
        if not isinstance(d.get(k), list): d[k] = []
    return d

def directive_overdue(t):
    """Просрочка вычисляется автоматически (ТЗ §19.6): срок прошёл, работа не завершена."""
    return bool(t.get("end")) and t["end"] < dISO(today()) and t["status"] not in DONE_ST + ["Отменена"]

def directive_status(t):
    """Статус поручения в реестре — производный от статуса задачи и приёмки результата (§2.11)."""
    dr = dir_of(t)
    acc = dr.get("acceptance") or {}
    if t["status"] == "Отменена": return "Отменено"
    if acc.get("verdict") == "Принято": return "Выполнено"
    if t["status"] == "На проверке": return "На проверке"
    if t["status"] in DONE_ST: return "Ожидает подтверждения"
    if directive_overdue(t): return "Просрочено"
    if t["status"] == "Приостановлена": return "Приостановлено"
    if t["status"] == "Заблокирована": return "Ожидает информации"
    if t["status"] in ("В работе", "Возвращена на доработку"): return "В работе"
    return "Не начато"

def directive_row(db, cur, t):
    """Строка реестра поручений со всеми полями ТЗ §2.11."""
    dr = dir_of(t)
    m = db.get_ent(cur, "meeting", dr.get("meetingId")) if dr.get("meetingId") else None
    dec = next((x for x in (m.get("decisions") or []) if x["id"] == dr.get("decisionId")), None) if m else None
    pr = prot_of(m) if m else {}
    return {"id": t["id"], "code": t["code"], "meetingId": dr.get("meetingId"),
            "meetingCode": (m or {}).get("code") or dr.get("meetingCode") or "",
            "meetingTitle": (m or {}).get("title", ""), "meetingDate": (m or {}).get("date"),
            "protocolV": dr.get("protocolV") or pr.get("version"), "protocolStatus": pr.get("status"),
            "decisionN": dr.get("decisionN"), "decisionText": (dec or {}).get("text", ""),
            "agendaN": dr.get("agendaN"), "title": t["title"], "projectId": t["projectId"],
            "assignee": t["assignee"], "coassignees": dr["coassignees"], "postedDate": dr.get("postedDate"),
            "postedBy": dr.get("postedBy"), "due": t["end"], "status": directive_status(t),
            "taskStatus": t["status"], "progress": 100 if t["status"] in DONE_ST else t["progress"],
            "factDate": dr.get("factDate"), "result": t.get("result", ""), "comment": dr.get("comment", ""),
            "criteria": dr.get("criteria", ""), "source": dr.get("source", ""), "priority": t["priority"],
            "docs": dr["docs"], "dueHistory": dr["dueHistory"], "acceptance": dr.get("acceptance"),
            "controller": dr.get("controller"), "stateAt": directive_state_ts(t),
            "idleDays": int((now_ms() - directive_state_ts(t)) / (DAY * 1000)),
            "overdueDays": diffD(dISO(today()), t["end"]) if directive_overdue(t) and t.get("end") else 0}

def _directive_req(db, cur, u, p, op="edit"):
    t = get_req(db, cur, "task", p["id"])
    if t.get("type") != "directive": raise RpcError("Задача не является поручением совещания", "Поручение")
    guard(db, cur, u, "tasks", op, t)
    return t

def _directive_notify(db, cur, t, dr, type_, title, body, extra=()):
    """Состав получателей определяется настройкой контроля поручений (ТЗ §2.11.1)."""
    rec = directive_recipients(db, cur, t, dr) + list(extra)
    for uid_ in dict.fromkeys([x for x in rec if x]):
        notify(db, cur, uid_, type_, title, body, "Поручение", t["id"],
               dedupe="%s:%s:%s:%s" % (type_, uid_, t["id"], now_ms()))

@reg("directive.list")
def h_directive_list(db, cur, u, p):
    """Реестр поручений (ТЗ §2.11): фильтры по совещанию, исполнителю, проекту, статусу, срокам."""
    guard(db, cur, u, "meetings", "view")
    rows = [directive_row(db, cur, t) for t in db.all_ent(cur, "task", order="code") if t.get("type") == "directive"]
    f = p or {}
    if f.get("meetingId"): rows = [r for r in rows if r["meetingId"] == f["meetingId"]]
    if f.get("projectId"): rows = [r for r in rows if r["projectId"] == f["projectId"]]
    if f.get("assignee"): rows = [r for r in rows if r["assignee"] == f["assignee"] or f["assignee"] in r["coassignees"]]
    if f.get("status"): rows = [r for r in rows if r["status"] == f["status"]]
    if f.get("overdue"): rows = [r for r in rows if r["overdueDays"] > 0]
    if f.get("from"): rows = [r for r in rows if (r["due"] or "") >= f["from"]]
    if f.get("to"): rows = [r for r in rows if (r["due"] or "") <= f["to"]]
    if f.get("q"):
        q = f["q"].lower()
        rows = [r for r in rows if q in (r["title"] + " " + r["decisionText"] + " " + r["code"]).lower()]
    return {"rows": rows, "total": len(rows),
            "byStatus": {s: sum(1 for r in rows if r["status"] == s) for s in DIRECTIVE_STATUSES}}, None

@reg("directive.update")
def h_directive_update(db, cur, u, p):
    """Карточка поручения: соисполнители, критерии, комментарий, результат, дата фактического исполнения."""
    t = _directive_req(db, cur, u, p)
    dr = dir_of(t)
    touched = False
    if "assignee" in p:
        aid = p["assignee"] or None
        if aid:
            cur.execute("SELECT id FROM users WHERE id=%s AND NOT blocked", (aid,))
            if not cur.fetchone(): raise RpcError("Исполнитель не найден или заблокирован: %s" % aid, "Поручение")
            dr["coassignees"] = [c for c in dr["coassignees"] if c != aid]
        if aid != t.get("assignee"): touched = True
        t["assignee"] = aid          # снятие исполнителя выявляется контролем §2.11.1
    if "due" in p:
        nd = p["due"] or None
        if nd:
            check_date(nd, "дата срока поручения")
            if t.get("end") and nd != t["end"]:
                raise RpcError("Изменение установленного срока выполняется продлением с основанием (directive.extend)",
                               "Поручение")
            if not t.get("end"):
                dr["dueHistory"].append({"ts": now_ms(), "by": u["id"], "from": None, "to": nd,
                                         "reason": "Установлен срок исполнения поручения"})
        elif t.get("end"):
            dr["dueHistory"].append({"ts": now_ms(), "by": u["id"], "from": t["end"], "to": None,
                                     "reason": "Срок исполнения снят — поручение подлежит контролю §2.11.1"})
        if (nd or "") != (t.get("end") or ""): touched = True
        t["end"] = nd or ""            # снятый срок хранится пустым значением (NOT NULL в схеме)
        if nd and (t.get("start") or "") > nd: t["start"] = nd
    if "controller" in p:
        cid = p["controller"] or None
        if cid:
            cur.execute("SELECT id FROM users WHERE id=%s AND NOT blocked", (cid,))
            if not cur.fetchone(): raise RpcError("Контролирующее лицо не найдено: %s" % cid, "Поручение")
        dr["controller"] = cid
    if "coassignees" in p:
        co = []
        for uid_ in (p.get("coassignees") or [])[:10]:
            if uid_ == t["assignee"] or uid_ in co: continue
            cur.execute("SELECT id FROM users WHERE id=%s AND NOT blocked", (uid_,))
            if not cur.fetchone(): raise RpcError("Соисполнитель не найден: %s" % uid_, "Поручение")
            co.append(uid_)
        dr["coassignees"] = co
    if "criteria" in p: dr["criteria"] = (p["criteria"] or "")[:2000]
    if "comment" in p: dr["comment"] = (p["comment"] or "")[:2000]
    if "result" in p: t["result"] = (p["result"] or "")[:4000]
    if "factDate" in p:
        fd = p["factDate"] or None
        if fd:
            check_date(fd, "дата фактического исполнения")
            if fd > dISO(today()): raise RpcError("Дата фактического исполнения не может быть в будущем", "Поручение")
        dr["factDate"] = fd
    if "progress" in p:
        v = int(p["progress"])
        if not 0 <= v <= 100: raise RpcError("Процент выполнения: 0…100", "Поручение")
        if v != t["progress"]: touched = True
        t["progress"] = v
    if touched or any(k in p for k in ("result", "factDate")): dr["stateAt"] = now_ms()
    t["directive"] = dr
    db.save_ent(cur, "task", t)
    db.audit(cur, "AUDIT", "info", u["id"], "meetings", "Карточка поручения изменена", "Поручение", t["id"],
             {"code": t["code"], "status": directive_status(t), "event": "MEETING_DIRECTIVE_UPDATED"})
    return directive_row(db, cur, t), msg("Поручение обновлено", "Реестр M16 и карточка задачи M02 синхронизированы")

@reg("directive.extend")
def h_directive_extend(db, cur, u, p):
    """Продление срока: фиксируется в истории с инициатором и основанием (ТЗ §19.6)."""
    t = _directive_req(db, cur, u, p)
    dr = dir_of(t)
    req(p, "due", "reason"); check_date(p["due"], "дата нового срока")
    reason = (p["reason"] or "").strip()
    if not reason: raise RpcError("Основание продления обязательно", "Поручение")
    if p["due"] <= t["end"]: raise RpcError("Новый срок должен быть позже текущего (%s)" % _fd(t["end"]), "Поручение")
    if t["status"] in DONE_ST + ["Отменена"]: raise RpcError("Срок завершённого поручения не продлевается", "Поручение")
    old = t["end"]
    dr["dueHistory"].append({"ts": now_ms(), "by": u["id"], "from": old, "to": p["due"], "reason": reason[:1000]})
    t["end"] = p["due"]; t["directive"] = dr
    db.save_ent(cur, "task", t)
    _directive_notify(db, cur, t, dr, "directive.extended", "Срок поручения продлён: %s" % t["title"],
                      "%s · %s → %s · %s" % (t["code"], _fd(old), _fd(p["due"]), reason[:150]))
    db.audit(cur, "BUSINESS", "warning", u["id"], "meetings", "Срок поручения продлён", "Поручение", t["id"],
             {"code": t["code"], "from": old, "to": p["due"], "reason": reason[:300], "event": "MEETING_DIRECTIVE_EXTENDED"})
    return directive_row(db, cur, t), msg("Срок продлён до %s" % _fd(p["due"]), "Продление зафиксировано в истории с основанием")

@reg("directive.accept")
def h_directive_accept(db, cur, u, p):
    """Отдельный контроль результата: принятие исполнения решения руководителем (ТЗ §2.11)."""
    t = get_req(db, cur, "task", p["id"])
    if t.get("type") != "directive": raise RpcError("Задача не является поручением совещания", "Поручение")
    guard(db, cur, u, "meetings", "approve", t)
    dr = dir_of(t)
    verdict = p.get("verdict")
    if verdict not in DIRECTIVE_ACCEPT: raise RpcError("Укажите результат приёмки: Принято или На доработку", "Поручение")
    comment = (p.get("comment") or "").strip()[:2000]
    if verdict == "На доработку" and not comment:
        raise RpcError("Возврат на доработку требует комментария", "Поручение")
    if t["status"] not in ("На проверке",) + tuple(DONE_ST) and verdict == "Принято":
        raise RpcError("Принять можно поручение, переданное на проверку (статус задачи «%s»)" % t["status"], "Поручение")
    dr["acceptance"] = {"verdict": verdict, "by": u["id"], "ts": now_ms(), "comment": comment}
    if verdict == "Принято":
        if not dr.get("factDate"): dr["factDate"] = dISO(today())
        apply_task_status(t, "Закрыта")
        t["result"] = t.get("result") or comment
        ev, tone = "MEETING_DIRECTIVE_ACCEPTED", "ok"
        title = "Поручение принято: %s" % t["title"]
        sub = "%s · решение совещания исполнено и принято" % t["code"]
        ntype = "directive.accepted"
    else:
        apply_task_status(t, "Возвращена на доработку")
        dr["factDate"] = None
        ev, tone = "MEETING_DIRECTIVE_REWORK", "warn"
        title = "Поручение возвращено на доработку: %s" % t["title"]
        sub = "%s · %s" % (t["code"], comment[:150])
        ntype = "directive.rework"
    t["directive"] = dr
    db.save_ent(cur, "task", t)
    _directive_notify(db, cur, t, dr, ntype, title, sub)
    db.audit(cur, "BUSINESS", "info" if verdict == "Принято" else "warning", u["id"], "meetings",
             "Приёмка результата поручения: " + verdict, "Поручение", t["id"],
             {"code": t["code"], "verdict": verdict, "comment": comment, "event": ev,
              "events": [ev] + (["MEETING_TASK_COMPLETED"] if verdict == "Принято" else [])})
    return directive_row(db, cur, t), msg("Результат поручения: " + verdict, sub, tone)

@reg("directive.attach")
def h_directive_attach(db, cur, u, p):
    """Документы, подтверждающие выполнение поручения (связь с M09, без копирования данных)."""
    t = _directive_req(db, cur, u, p)
    dr = dir_of(t)
    d = get_req(db, cur, "doc", p["docId"]); guard(db, cur, u, "docs", "view", d)
    if any(x["docId"] == d["id"] for x in dr["docs"]): raise RpcError("Документ уже приложен к поручению", "Поручение")
    if len(dr["docs"]) >= 20: raise RpcError("Не более 20 подтверждающих документов", "Поручение")
    v = max([x["v"] for x in (d.get("versions") or [])] or [0])
    dr["docs"].append({"docId": d["id"], "code": d["code"], "title": d["title"], "v": v, "ts": now_ms(), "by": u["id"]})
    t["directive"] = dr
    db.save_ent(cur, "task", t)
    db.audit(cur, "AUDIT", "info", u["id"], "meetings", "К поручению приложен подтверждающий документ", "Поручение", t["id"],
             {"code": t["code"], "doc": d["code"], "event": "MEETING_DIRECTIVE_DOC_ATTACHED"})
    return directive_row(db, cur, t), msg("Документ приложен", d["code"] + " · " + d["title"])

@reg("directive.detach")
def h_directive_detach(db, cur, u, p):
    t = _directive_req(db, cur, u, p)
    dr = dir_of(t)
    n = len(dr["docs"])
    dr["docs"] = [x for x in dr["docs"] if x["docId"] != p.get("docId")]
    if len(dr["docs"]) == n: raise RpcError("Документ не найден среди приложенных", "Поручение")
    t["directive"] = dr
    db.save_ent(cur, "task", t)
    db.audit(cur, "AUDIT", "warning", u["id"], "meetings", "Подтверждающий документ откреплён от поручения", "Поручение", t["id"],
             {"code": t["code"], "doc": p.get("docId")})
    return directive_row(db, cur, t), msg("Документ откреплён", "Сам документ остался в M09")
# ---------------- Контроль сроков и отчётность по поручениям (ТЗ M16 §2.11.1–2.11.2) ----------------
# Система автоматически выявляет шесть категорий отклонений по поручениям и рассылает уведомления
# настраиваемому кругу получателей: исполнитель, соисполнители, председатель, секретарь,
# руководитель подразделения, контролирующее лицо. Отчётность — семь видов отчётов (§2.11.2),
# каждый выгружается в DOCX/XLSX/PDF/CSV/HTML и сохраняется снимком в реестре отчётов M13.

DIR_CHECKS = [("dueSoon", "Приближается срок исполнения"), ("overdue", "Просрочено"),
              ("noAssignee", "Без исполнителя"), ("noDue", "Без установленного срока"),
              ("stale", "Без обновления состояния"), ("needReview", "Требует проверки результата")]
DIR_RECIPIENTS = [("assignee", "Исполнитель"), ("coassignees", "Соисполнители"),
                  ("chair", "Председатель совещания"), ("secretary", "Секретарь совещания"),
                  ("head", "Руководитель подразделения"), ("controller", "Контролирующее лицо")]
DIR_CHECK_NOTIF = {"dueSoon": "directive.due", "overdue": "directive.overdue",
                   "noAssignee": "directive.noassignee", "noDue": "directive.nodue",
                   "stale": "directive.stale", "needReview": "directive.review"}
DIR_CONTROL_DEF = {"dueSoonDays": 3, "staleDays": 7, "controller": None,
                   "recipients": {k: True for k, _ in DIR_RECIPIENTS},
                   "checks": {k: True for k, _ in DIR_CHECKS}}

def dir_control_cfg(db, cur):
    """Действующая настройка контроля сроков (значения по умолчанию — для записей прежних версий)."""
    c = (db.settings(cur) or {}).get("dirControl") or {}
    rec, chk = c.get("recipients") or {}, c.get("checks") or {}
    try: soon = int(c.get("dueSoonDays", DIR_CONTROL_DEF["dueSoonDays"]))
    except Exception: soon = DIR_CONTROL_DEF["dueSoonDays"]
    try: stale = int(c.get("staleDays", DIR_CONTROL_DEF["staleDays"]))
    except Exception: stale = DIR_CONTROL_DEF["staleDays"]
    return {"dueSoonDays": max(1, min(30, soon)), "staleDays": max(1, min(90, stale)),
            "controller": c.get("controller") or None,
            "recipients": {k: bool(rec.get(k, True)) for k, _ in DIR_RECIPIENTS},
            "checks": {k: bool(chk.get(k, True)) for k, _ in DIR_CHECKS}}

def _active_ids(db, cur):
    cur.execute("SELECT id FROM users WHERE NOT blocked")
    return {r["id"] for r in cur.fetchall()}

def _head_ids(db, cur):
    cur.execute("SELECT id FROM users WHERE role='head' AND NOT blocked ORDER BY id")
    return [r["id"] for r in cur.fetchall()]

def directive_state_ts(t):
    """Момент последнего обновления состояния поручения: статус, процент, результат, срок, приёмка."""
    dr = dir_of(t)
    ts = [t.get("createdAt") or 0, dr.get("stateAt") or 0, (dr.get("acceptance") or {}).get("ts") or 0]
    ts += [h.get("ts") or 0 for h in dr["dueHistory"]]
    return max(ts)

def directive_issues(db, cur, t, cfg=None, active=None):
    """Категории отклонений поручения (ТЗ §2.11.1). Завершённые и отменённые не проверяются."""
    st = directive_status(t)
    if st in ("Выполнено", "Отменено"): return []
    cfg = cfg or dir_control_cfg(db, cur)
    active = _active_ids(db, cur) if active is None else active
    dr = dir_of(t)
    today_s = dISO(today())
    found = []
    if not t.get("assignee") or t["assignee"] not in active: found.append("noAssignee")
    if not t.get("end"):
        found.append("noDue")
    elif directive_overdue(t):
        found.append("overdue")
    elif diffD(t["end"], today_s) <= cfg["dueSoonDays"]:
        found.append("dueSoon")
    if now_ms() - directive_state_ts(t) > cfg["staleDays"] * DAY * 1000: found.append("stale")
    if st in ("На проверке", "Ожидает подтверждения"): found.append("needReview")
    return [k for k, _ in DIR_CHECKS if k in found and cfg["checks"].get(k, True)]

def directive_recipients(db, cur, t, dr, cfg=None, active=None):
    """Круг получателей уведомлений по поручению — согласно настройке (ТЗ §2.11.1)."""
    cfg = cfg or dir_control_cfg(db, cur)
    active = _active_ids(db, cur) if active is None else active
    rec = cfg["recipients"]
    m = db.get_ent(cur, "meeting", dr.get("meetingId")) if dr.get("meetingId") else None
    out = []
    if rec.get("assignee"): out.append(t.get("assignee"))
    if rec.get("coassignees"): out += list(dr.get("coassignees") or [])
    if rec.get("chair") and m: out.append(m.get("chair"))
    if rec.get("secretary") and m: out.append(m.get("secretary"))
    heads = _head_ids(db, cur)
    if rec.get("head"): out += heads
    if rec.get("controller"): out.append(dr.get("controller") or cfg.get("controller") or (heads[0] if heads else None))
    return [x for x in dict.fromkeys([y for y in out if y]) if x in active]

def _directive_control_msg(db, cur, t, check):
    """Заголовок и текст уведомления для категории отклонения."""
    dr = dir_of(t)
    if check == "dueSoon":
        return ("Приближается срок поручения: %s" % t["title"],
                "%s · срок %s · осталось %d дн" % (t["code"], _fd(t["end"]), diffD(t["end"], dISO(today()))))
    if check == "overdue":
        return ("Поручение просрочено: %s" % t["title"],
                "%s · срок %s · просрочка %d дн" % (t["code"], _fd(t["end"]), diffD(dISO(today()), t["end"])))
    if check == "noAssignee":
        return ("Поручение без исполнителя: %s" % t["title"],
                "%s · требуется назначить ответственного исполнителя" % t["code"])
    if check == "noDue":
        return ("Поручение без срока исполнения: %s" % t["title"],
                "%s · требуется установить срок исполнения" % t["code"])
    if check == "stale":
        d = int((now_ms() - directive_state_ts(t)) / (DAY * 1000))
        return ("Поручение без обновления состояния: %s" % t["title"],
                "%s · состояние не обновлялось %d дн · требуется контроль исполнения" % (t["code"], d))
    return ("Поручение требует проверки результата: %s" % t["title"],
            "%s · результат заявлен исполнителем и ожидает приёмки руководителем" % t["code"])

def _audit_overdue_once(db, cur, t, today_s):
    """Событие MEETING_TASK_OVERDUE (ТЗ §2.13) — не чаще одного раза в сутки на поручение."""
    day_start = int(time.mktime(today().timetuple())) * 1000
    cur.execute("SELECT 1 FROM events WHERE obj_id=%s AND action=%s AND ts>=%s LIMIT 1",
                (t["id"], "Выявлена просрочка поручения", day_start))
    if cur.fetchone(): return False
    db.audit(cur, "BUSINESS", "warning", "system", "meetings", "Выявлена просрочка поручения", "Поручение", t["id"],
             {"code": t["code"], "due": t["end"], "days": diffD(today_s, t["end"]),
              "assignee": t.get("assignee"), "event": "MEETING_TASK_OVERDUE"})
    return True

def directive_control_cycle(db, cur):
    """Проход контроля сроков поручений: выявление отклонений и рассылка (ТЗ §2.11.1)."""
    cfg = dir_control_cfg(db, cur)
    active = _active_ids(db, cur)
    today_s = dISO(today())
    by = {k: 0 for k, _ in DIR_CHECKS}
    found = {k: 0 for k, _ in DIR_CHECKS}
    delivered = 0
    tasks = 0
    for t in db.all_ent(cur, "task", order="code"):
        if t.get("type") != "directive": continue
        tasks += 1
        iss = directive_issues(db, cur, t, cfg, active)
        if not iss: continue
        dr = dir_of(t)
        rec = directive_recipients(db, cur, t, dr, cfg, active)
        for k in iss:
            found[k] += 1
            title, body = _directive_control_msg(db, cur, t, k)
            n = notify_many(db, cur, rec, DIR_CHECK_NOTIF[k], title, body, "Поручение", t["id"], dedupe_suffix=today_s)
            by[k] += n
            delivered += n
            if k == "overdue" and n: _audit_overdue_once(db, cur, t, today_s)
    return {"delivered": delivered, "byCheck": by, "found": found, "tasks": tasks,
            "recipients": cfg["recipients"], "dueSoonDays": cfg["dueSoonDays"], "staleDays": cfg["staleDays"]}

@reg("directive.control")
def h_directive_control(db, cur, u, p):
    """Контроль сроков (ТЗ §2.11.1): шесть категорий отклонений с указанием получателей уведомления."""
    guard(db, cur, u, "meetings", "view")
    cfg = dir_control_cfg(db, cur)
    active = _active_ids(db, cur)
    f = p or {}
    rows = []
    for t in db.all_ent(cur, "task", order="code"):
        if t.get("type") != "directive": continue
        iss = directive_issues(db, cur, t, cfg, active)
        if not iss: continue
        if f.get("check") and f["check"] not in iss: continue
        if f.get("meetingId") and t.get("meetingId") != f["meetingId"]: continue
        if f.get("projectId") and t.get("projectId") != f["projectId"]: continue
        if f.get("assignee") and t.get("assignee") != f["assignee"] and f["assignee"] not in dir_of(t)["coassignees"]: continue
        r = directive_row(db, cur, t)
        r["issues"] = iss
        r["stateAt"] = directive_state_ts(t)
        r["idleDays"] = int((now_ms() - r["stateAt"]) / (DAY * 1000))
        r["recipients"] = directive_recipients(db, cur, t, dir_of(t), cfg, active)
        rows.append(r)
    return {"rows": rows, "total": len(rows), "cfg": cfg,
            "checks": [{"key": k, "name": n, "on": cfg["checks"][k],
                        "count": sum(1 for r in rows if k in r["issues"])} for k, n in DIR_CHECKS],
            "recipientKinds": [{"key": k, "name": n, "on": cfg["recipients"][k]} for k, n in DIR_RECIPIENTS]}, None

@reg("directive.controlConfig")
def h_directive_control_config(db, cur, u, p):
    """Настройка контроля: пороги, состав проверок и состав получателей уведомлений (ТЗ §2.11.1)."""
    guard(db, cur, u, "meetings", "view")
    cfg = dir_control_cfg(db, cur)
    if not p: return cfg, None
    guard(db, cur, u, "admin", "edit")
    s = db.settings(cur)
    c = dict(cfg)
    if "dueSoonDays" in p:
        try: v = int(p["dueSoonDays"])
        except Exception: raise RpcError("Порог приближения срока: целое число дней", "Контроль поручений")
        if not 1 <= v <= 30: raise RpcError("Порог приближения срока: 1…30 дней", "Контроль поручений")
        c["dueSoonDays"] = v
    if "staleDays" in p:
        try: v = int(p["staleDays"])
        except Exception: raise RpcError("Порог отсутствия обновлений: целое число дней", "Контроль поручений")
        if not 1 <= v <= 90: raise RpcError("Порог отсутствия обновлений: 1…90 дней", "Контроль поручений")
        c["staleDays"] = v
    if "recipients" in p:
        rec = dict(c["recipients"])
        for k, v in (p["recipients"] or {}).items():
            if k not in c["recipients"]: raise RpcError("Неизвестный получатель: %s" % k, "Контроль поручений")
            rec[k] = bool(v)
        if not any(rec.values()): raise RpcError("Должен остаться хотя бы один получатель уведомлений", "Контроль поручений")
        c["recipients"] = rec
    if "checks" in p:
        chk = dict(c["checks"])
        for k, v in (p["checks"] or {}).items():
            if k not in chk: raise RpcError("Неизвестная проверка: %s" % k, "Контроль поручений")
            chk[k] = bool(v)
        if not any(chk.values()): raise RpcError("Должна остаться хотя бы одна проверка контроля сроков", "Контроль поручений")
        c["checks"] = chk
    if "controller" in p:
        cid = p["controller"] or None
        if cid:
            cur.execute("SELECT id FROM users WHERE id=%s AND NOT blocked", (cid,))
            if not cur.fetchone(): raise RpcError("Контролирующее лицо не найдено: %s" % cid, "Контроль поручений")
        c["controller"] = cid
    s["dirControl"] = c
    db.save_settings(cur, s)
    db.audit(cur, "AUDIT", "info", u["id"], "meetings", "Изменены настройки контроля поручений", "Система", "",
             {"dueSoonDays": c["dueSoonDays"], "staleDays": c["staleDays"], "controller": c["controller"],
              "recipients": [k for k, v in c["recipients"].items() if v],
              "checks": [k for k, v in c["checks"].items() if v], "event": "MEETING_DIRECTIVE_CONTROL_CONFIG"})
    return c, msg("Настройки контроля сохранены", "Пороги и состав получателей применяются планировщиком немедленно")

@reg("directive.controlNotify")
def h_directive_control_notify(db, cur, u, p):
    """Внеочередной проход контроля сроков с рассылкой (руководитель, председатель)."""
    guard(db, cur, u, "meetings", "approve")
    res = directive_control_cycle(db, cur)
    db.audit(cur, "BUSINESS", "info", u["id"], "meetings", "Выполнен контроль сроков поручений", "Система", "",
             {"delivered": res["delivered"], "found": res["found"], "event": "MEETING_DIRECTIVE_CONTROL_RUN"})
    return res, msg("Контроль сроков выполнен", "Отклонений выявлено: %d · уведомлений доставлено: %d"
                    % (sum(res["found"].values()), res["delivered"]))

# ---- Отчётность по поручениям (ТЗ §2.11.2): семь видов отчётов ----
DIRECTIVE_REPORTS = [("registry", "Реестр всех поручений"), ("overdue", "Перечень просроченных поручений"),
                     ("assignees", "Отчёт по исполнителям"), ("meetings", "Отчёт по совещаниям"),
                     ("projects", "Отчёт по проектам"), ("period", "Отчёт по исполнению за период"),
                     ("unfulfilled", "Отчёт по неисполненным решениям")]
DIR_REPORT_NAMES = dict(DIRECTIVE_REPORTS)

def _dir_report_filter(db, cur, rows, f):
    if f.get("meetingId"): rows = [r for r in rows if r["meetingId"] == f["meetingId"]]
    if f.get("projectId"): rows = [r for r in rows if r["projectId"] == f["projectId"]]
    if f.get("assignee"): rows = [r for r in rows if r["assignee"] == f["assignee"] or f["assignee"] in r["coassignees"]]
    if f.get("from"): rows = [r for r in rows if (r["due"] or "") >= f["from"]]
    if f.get("to"): rows = [r for r in rows if (r["due"] or "") <= f["to"]]
    return rows

def _dir_pct(done, total):
    return int(round(100.0 * done / total)) if total else 0

def _dir_hours(db, cur, ids):
    if not ids: return 0.0
    cur.execute("SELECT COALESCE(SUM(hours),0) AS h FROM time_entries WHERE task_id = ANY(%s) AND status<>'Отклонена'",
                (list(ids),))
    return round(float(cur.fetchone()["h"]), 1)

def _dir_report_meta(db, cur, f):
    m = []
    if f.get("from") or f.get("to"): m.append(("Период", "%s — %s" % (_fd(f.get("from")), _fd(f.get("to")))))
    if f.get("meetingId"):
        mm = db.get_ent(cur, "meeting", f["meetingId"])
        m.append(("Совещание", (mm or {}).get("code", f["meetingId"])))
    if f.get("projectId"): m.append(("Проект", _pname(db, cur, f["projectId"])))
    if f.get("assignee"): m.append(("Исполнитель", _uname(db, cur, f["assignee"])))
    return m or [("Отбор", "Все поручения")]

def directive_report_data(db, cur, u, kind, params=None):
    """Данные отчёта по поручениям: сводные показатели, таблица и диаграмма (ТЗ §2.11.2)."""
    if kind not in DIR_REPORT_NAMES:
        raise RpcError("Неизвестный вид отчёта по поручениям: %s" % kind, "Отчётность", 404, "UNKNOWN_REPORT")
    f = dict(params or {})
    if kind == "period":
        f.setdefault("from", D(-30)); f.setdefault("to", dISO(today()))
        if f.get("from") and f.get("to"):
            check_date(f["from"], "дата начала периода"); check_date(f["to"], "дата окончания периода")
            if f["from"] > f["to"]: raise RpcError("Начало периода позже его окончания", "Отчётность")
    tasks = [t for t in db.all_ent(cur, "task", order="code") if t.get("type") == "directive"]
    rows = [directive_row(db, cur, t) for t in tasks]
    tmap = {t["id"]: t for t in tasks}
    name = DIR_REPORT_NAMES[kind]
    columns, data, summary, chart = [], [], [], None
    today_s = dISO(today())

    if kind in ("registry", "overdue"):
        sel = _dir_report_filter(db, cur, rows, f)
        if kind == "overdue":
            sel = sorted([r for r in sel if r["overdueDays"] > 0], key=lambda r: -r["overdueDays"])
            columns = ["№", "Поручение", "Совещание", "Исполнитель", "Срок", "Просрочка, дн", "Статус", "%", "Контролирующее лицо"]
            for r in sel:
                ctl = r.get("controller") or dir_control_cfg(db, cur).get("controller")
                data.append([r["code"], r["title"], r["meetingCode"], _uname(db, cur, r["assignee"]) or "— не назначен —",
                             _fd(r["due"]), r["overdueDays"], r["status"], r["progress"], _uname(db, cur, ctl)])
            days = [r["overdueDays"] for r in sel]
            summary = [("Просроченных поручений", len(sel)),
                       ("Максимальная просрочка, дн", max(days) if days else 0),
                       ("Средняя просрочка, дн", round(sum(days) / len(days), 1) if days else 0),
                       ("Без исполнителя", sum(1 for r in sel if not r["assignee"])),
                       ("Срок продлевался", sum(1 for r in sel if r["dueHistory"]))]
            if sel:
                top = sel[:8]
                chart = {"type": "bar", "labels": [r["code"] for r in top],
                         "values": [r["overdueDays"] for r in top], "unit": "дн", "title": "Наибольшие просрочки"}
        else:
            columns = ["№", "Совещание", "Протокол", "Решение", "Поручение", "Исполнитель", "Соисполнители",
                       "Срок", "Статус", "%", "Дата факта", "Результат", "Документы"]
            for r in sel:
                data.append([r["code"], r["meetingCode"], ("ред. %s" % r["protocolV"]) if r["protocolV"] else "—",
                             (("№%s · " % r["decisionN"]) if r["decisionN"] else "") + (r["decisionText"] or r["title"]),
                             r["title"], _uname(db, cur, r["assignee"]) or "— не назначен —",
                             ", ".join(_uname(db, cur, c) for c in r["coassignees"]) or "—",
                             _fd(r["due"]) or "— не установлен —", r["status"], r["progress"],
                             _fd(r["factDate"]) or "—", r["result"] or "—",
                             ", ".join(x["code"] for x in r["docs"]) or "—"])
            summary = [("Всего поручений", len(sel))] + \
                      [(s, sum(1 for r in sel if r["status"] == s)) for s in DIRECTIVE_STATUSES
                       if any(r["status"] == s for r in sel)] + \
                      [("Средний процент выполнения", _dir_pct(sum(r["progress"] for r in sel), 100 * len(sel)) if sel else 0)]
            chart = {"type": "pie", "labels": [s for s in DIRECTIVE_STATUSES if any(r["status"] == s for r in sel)],
                     "values": [sum(1 for r in sel if r["status"] == s) for s in DIRECTIVE_STATUSES
                                if any(r["status"] == s for r in sel)], "unit": "шт", "title": "Состояния поручений"}

    elif kind == "assignees":
        sel = _dir_report_filter(db, cur, rows, f)
        columns = ["Исполнитель", "Должность", "Всего", "Выполнено", "В работе", "На проверке", "Просрочено",
                   "Не начато", "Дисциплина, %", "Средняя просрочка, дн"]
        groups = {}
        for r in sel: groups.setdefault(r["assignee"] or "", []).append(r)
        for uid_, gr in sorted(groups.items(), key=lambda kv: _uname(db, cur, kv[0]) or "я"):
            done = sum(1 for r in gr if r["status"] == "Выполнено")
            over = [r["overdueDays"] for r in gr if r["overdueDays"] > 0]
            data.append([_uname(db, cur, uid_) or "— без исполнителя —", _role_of(db, cur, uid_), len(gr), done,
                         sum(1 for r in gr if r["status"] == "В работе"),
                         sum(1 for r in gr if r["status"] in ("На проверке", "Ожидает подтверждения")),
                         len(over), sum(1 for r in gr if r["status"] == "Не начато"),
                         _dir_pct(done, len(gr)), round(sum(over) / len(over), 1) if over else 0])
        summary = [("Исполнителей в отчёте", len(data)), ("Поручений", len(sel)),
                   ("Исполнено и принято", sum(1 for r in sel if r["status"] == "Выполнено")),
                   ("Исполнительская дисциплина, %", _dir_pct(sum(1 for r in sel if r["status"] == "Выполнено"), len(sel)))]
        if data:
            chart = {"type": "bar", "labels": [r[0] for r in data], "values": [r[2] for r in data],
                     "unit": "шт", "title": "Поручений по исполнителям"}

    elif kind == "meetings":
        sel = _dir_report_filter(db, cur, rows, f)
        columns = ["Совещание", "Дата", "Протокол", "Решений", "Поручений", "Выполнено", "Просрочено", "Не начато", "Исполнение, %"]
        meetings = [m for m in db.all_ent(cur, "meeting", order="code")
                    if not f.get("meetingId") or m["id"] == f["meetingId"]]
        no_task = 0
        for m in meetings:
            gr = [r for r in sel if r["meetingId"] == m["id"]]
            decs = m.get("decisions") or []
            no_task += sum(1 for d in decs if not [x for x in dec_task_ids(d) if x in tmap])
            if not gr and not decs: continue
            if f.get("from") and (m.get("date") or "") < f["from"]: continue
            if f.get("to") and (m.get("date") or "") > f["to"]: continue
            done = sum(1 for r in gr if r["status"] == "Выполнено")
            pr = prot_of(m)
            data.append([m["code"] + " · " + m["title"], _fd(m["date"]),
                         ("ред. %s" % pr["version"]) if pr.get("version") else "не утверждён",
                         len(decs), len(gr), done, sum(1 for r in gr if r["overdueDays"] > 0),
                         sum(1 for r in gr if r["status"] == "Не начато"), _dir_pct(done, len(gr))])
        summary = [("Совещаний в отчёте", len(data)), ("Поручений", len(sel)),
                   ("Решений без поручений", no_task),
                   ("Исполнено и принято", sum(1 for r in sel if r["status"] == "Выполнено"))]

    elif kind == "projects":
        sel = _dir_report_filter(db, cur, rows, f)
        columns = ["Проект", "Наименование", "Поручений", "Выполнено", "В работе", "Просрочено", "Исполнение, %", "Трудозатраты, ч"]
        groups = {}
        for r in sel: groups.setdefault(r["projectId"] or "", []).append(r)
        for pid, gr in sorted(groups.items(), key=lambda kv: (db.get_ent(cur, "project", kv[0]) or {}).get("code", "")):
            pj = db.get_ent(cur, "project", pid) or {}
            done = sum(1 for r in gr if r["status"] == "Выполнено")
            data.append([pj.get("code", "—"), pj.get("name", "Проект не указан"), len(gr), done,
                         sum(1 for r in gr if r["status"] == "В работе"),
                         sum(1 for r in gr if r["overdueDays"] > 0), _dir_pct(done, len(gr)),
                         _dir_hours(db, cur, [r["id"] for r in gr])])
        summary = [("Проектов в отчёте", len(data)), ("Поручений", len(sel)),
                   ("Трудозатраты по поручениям, ч", _dir_hours(db, cur, [r["id"] for r in sel])),
                   ("Исполнение, %", _dir_pct(sum(1 for r in sel if r["status"] == "Выполнено"), len(sel)))]

    elif kind == "period":
        lo, hi = f["from"], f["to"]
        sel = [r for r in rows if (lo <= (r["postedDate"] or "") <= hi) or (lo <= (r["factDate"] or "") <= hi)
               or (lo <= (r["due"] or "") <= hi)]
        sel = _dir_report_filter(db, cur, sel, {"meetingId": f.get("meetingId"), "projectId": f.get("projectId"),
                                               "assignee": f.get("assignee")})
        columns = ["№", "Поручение", "Исполнитель", "Постановка", "Срок", "Дата факта", "Статус", "%", "Продлений срока"]
        for r in sel:
            data.append([r["code"], r["title"], _uname(db, cur, r["assignee"]) or "— не назначен —",
                         _fd(r["postedDate"]), _fd(r["due"]) or "—", _fd(r["factDate"]) or "—",
                         r["status"], r["progress"], len(r["dueHistory"])])
        posted = [r for r in rows if lo <= (r["postedDate"] or "") <= hi]
        fact = [r for r in rows if lo <= (r["factDate"] or "") <= hi]
        accepted = [r for r in rows if (r.get("acceptance") or {}).get("verdict") == "Принято"
                    and lo <= dISO(datetime.date.fromtimestamp((r["acceptance"]["ts"] or 0) / 1000)) <= hi]
        ext = sum(1 for r in rows for h in r["dueHistory"]
                  if lo <= dISO(datetime.date.fromtimestamp((h.get("ts") or 0) / 1000)) <= hi)
        in_time = sum(1 for r in fact if (r["due"] or "") >= (r["factDate"] or ""))
        late = [r for r in rows if r["overdueDays"] > 0 and (r["due"] or "") <= hi]
        summary = [("Период", "%s — %s" % (_fd(lo), _fd(hi))), ("Поставлено поручений", len(posted)),
                   ("Исполнено (дата факта)", len(fact)), ("Принято руководителем", len(accepted)),
                   ("Исполнено в срок", in_time), ("Дисциплина исполнения, %", _dir_pct(in_time, len(fact))),
                   ("Просрочено на конец периода", len(late)), ("Продлений срока за период", ext)]
        if fact or posted:
            chart = {"type": "bar", "labels": ["Поставлено", "Исполнено", "Принято", "Просрочено"],
                     "values": [len(posted), len(fact), len(accepted), len(late)], "unit": "шт",
                     "title": "Исполнение за период"}

    else:  # unfulfilled — неисполненные решения совещаний (в том числе без созданных поручений)
        columns = ["Совещание", "Дата", "№ решения", "Решение", "Ответственный", "Срок решения",
                   "Поручение", "Состояние поручения", "Просрочка, дн"]
        meetings = [m for m in db.all_ent(cur, "meeting", order="code")
                    if not f.get("meetingId") or m["id"] == f["meetingId"]]
        no_task = 0
        for m in meetings:
            if f.get("from") and (m.get("date") or "") < f["from"]: continue
            if f.get("to") and (m.get("date") or "") > f["to"]: continue
            for i, d in enumerate(m.get("decisions") or []):
                ts = [tmap[x] for x in dec_task_ids(d) if x in tmap]
                if f.get("projectId") and not any(t["projectId"] == f["projectId"] for t in ts): continue
                if f.get("assignee") and not any(t["assignee"] == f["assignee"] or
                                                 f["assignee"] in dir_of(t)["coassignees"] for t in ts): continue
                if not ts:
                    if f.get("projectId") or f.get("assignee"): continue
                    no_task += 1
                    data.append([m["code"], _fd(m["date"]), i + 1, d.get("text", ""),
                                 _uname(db, cur, d.get("resp")) or "— не назначен —", _fd(d.get("due")),
                                 "— не создано —", "Поручение не создано",
                                 diffD(today_s, d["due"]) if d.get("due") and d["due"] < today_s else 0])
                    continue
                for t in ts:
                    st = directive_status(t)
                    if st in ("Выполнено", "Отменено"): continue
                    r = directive_row(db, cur, t)
                    data.append([m["code"], _fd(m["date"]), i + 1, d.get("text", ""),
                                 _uname(db, cur, t["assignee"]) or "— не назначен —", _fd(d.get("due")),
                                 t["code"] + " · " + t["title"], st, r["overdueDays"]])
        summary = [("Неисполненных решений", len(data)), ("Из них без поручения", no_task),
                   ("Просроченных поручений", sum(1 for r in data if isinstance(r[8], int) and r[8] > 0)),
                   ("Совещаний в отчёте", len(meetings))]

    return {"kind": kind, "name": name, "title": name, "params": f, "columns": columns, "rows": data,
            "summary": summary, "chart": chart, "total": len(data), "ts": now_ms(), "by": u["id"],
            "meta": _dir_report_meta(db, cur, f)}

def directive_report_html(rep):
    """HTML-снимок отчёта для реестра M13 (данные фиксируются на момент формирования)."""
    def esc(s):
        return (str("" if s is None else s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    h = ["<h1>%s</h1>" % esc(rep["title"])]
    h.append('<div class="rmeta">СУП-ИП · сформировано %s</div>'
             % datetime.datetime.fromtimestamp(rep["ts"] / 1000).strftime("%d.%m.%Y %H:%M"))
    h.append("<h2>Сводные показатели</h2><table><tbody>")
    for k, v in rep["summary"]: h.append("<tr><td>%s</td><td>%s</td></tr>" % (esc(k), esc(v)))
    h.append("</tbody></table>")
    h.append("<h2>%s</h2><table><thead><tr>" % esc(rep["name"]))
    h += ["<th>%s</th>" % esc(c) for c in rep["columns"]]
    h.append("</tr></thead><tbody>")
    for r in rep["rows"]:
        h.append("<tr>" + "".join("<td>%s</td>" % esc(c) for c in r) + "</tr>")
    h.append("</tbody></table>")
    if not rep["rows"]: h.append("<p>По заданному отбору записей нет.</p>")
    return "".join(h)

@reg("directive.report")
def h_directive_report(db, cur, u, p):
    """Отчёт по поручениям (ТЗ §2.11.2). save=true — снимок в реестре отчётов M13."""
    guard(db, cur, u, "meetings", "view")
    kind = (p or {}).get("kind") or "registry"
    rep = directive_report_data(db, cur, u, kind, p or {})
    saved = None
    if (p or {}).get("save"):
        guard(db, cur, u, "reports", "create")
        n = next_seq(db, cur, "report")
        saved = dict(id=uid("rp"), code="ОТ-%03d" % n, type=rep["name"], title=rep["title"], params=rep["params"],
                     ts=now_ms(), by=u["id"], status="Сформирован", html=directive_report_html(rep))
        db.save_ent(cur, "report", saved)
        db.audit(cur, "BUSINESS", "info", u["id"], "reports", "Сформирован отчёт по поручениям", "Отчёт", saved["id"],
                 {"kind": kind, "code": saved["code"], "rows": rep["total"]})
    db.audit(cur, "AUDIT", "info", u["id"], "meetings", "Сформирован отчёт по поручениям", "Поручение", kind,
             {"kind": kind, "name": rep["name"], "rows": rep["total"], "params": rep["params"],
              "event": "MEETING_DIRECTIVE_REPORT"})
    return {"report": rep, "saved": saved, "kinds": [{"key": k, "name": n} for k, n in DIRECTIVE_REPORTS]}, \
        msg("Отчёт сформирован: %s" % rep["name"], "Записей: %d%s" % (rep["total"], " · снимок сохранён в M13" if saved else ""))

@reg("directive.reportKinds")
def h_directive_report_kinds(db, cur, u, p):
    guard(db, cur, u, "meetings", "view")
    return {"kinds": [{"key": k, "name": n} for k, n in DIRECTIVE_REPORTS]}, None

# ---------------- M13: отчёты ----------------
@reg("report.save")
def h_report_save(db, cur, u, p):
    guard(db, cur, u, "reports", "create")
    req(p, "type", "title", "html")
    if len(p["html"]) > 2_000_000: raise RpcError("Отчёт слишком велик")
    n = next_seq(db, cur, "report")
    r = dict(id=uid("rp"), code="ОТ-%03d" % n, type=p["type"], title=p["title"][:300], params=p.get("params", {}),
             ts=now_ms(), by=u["id"], status="Сформирован", html=p["html"])
    db.save_ent(cur, "report", r)
    db.audit(cur, "BUSINESS", "info", u["id"], "reports", "Сформирован отчёт", "Отчёт", r["id"], {"type": p["title"]})
    return r, msg("Отчёт сформирован", "Сохранён в реестре как снимок данных")

@reg("report.approve")
def h_report_approve(db, cur, u, p):
    r = get_req(db, cur, "report", p["id"]); guard(db, cur, u, "reports", "approve", r)
    r["status"] = "Утверждён"
    db.save_ent(cur, "report", r)
    db.audit(cur, "AUDIT", "info", u["id"], "reports", "Отчёт утверждён", "Отчёт", r["id"], {"code": r["code"]})
    return r, msg("Отчёт утверждён")

# ---------------- M12: календарь ----------------
@reg("calendar.create")
def h_cal_create(db, cur, u, p):
    guard(db, cur, u, "calendar", "create")
    req(p, "title", "date"); check_date(p["date"])
    e = dict(id=uid("ce"), title=p["title"][:300], date=p["date"], time=p.get("time", ""), type="Событие",
             participants=p.get("participants", [u["id"]]), note=p.get("note", "")[:500])
    db.save_ent(cur, "calEvent", e)
    db.audit(cur, "BUSINESS", "info", u["id"], "calendar", "Создано событие календаря", "Событие", e["id"], {"title": e["title"]})
    reminders_set(db, cur, "calEvent", e, e["participants"] or [u["id"]], DEFAULT_REMINDERS, u["id"])
    return e, msg("Событие добавлено в календарь", "Напоминания участникам: за 1 день и за 1 час")

@reg("calendar.delete")
def h_cal_delete(db, cur, u, p):
    guard(db, cur, u, "calendar", "edit")
    e = get_req(db, cur, "calEvent", p["id"])
    db.del_ent(cur, "calEvent", e["id"])
    db.audit(cur, "AUDIT", "info", u["id"], "calendar", "Событие календаря удалено", "Событие", e["id"], {})
    return True, msg("Событие удалено")

# ---------------- M05: ресурсы ----------------
@reg("resource.update")
def h_resource_update(db, cur, u, p):
    guard(db, cur, u, "resources", "edit")
    req(p, "userId")
    cur.execute("SELECT * FROM users WHERE id=%s", (p["userId"],))
    row = cur.fetchone()
    if not row: raise RpcError("Пользователь не найден")
    cap = max(1, min(12, int(p.get("capacity", row["capacity"]))))
    absences = row["absences"]
    if p.get("clearAbsences"): absences = []
    elif p.get("absence"):
        a = p["absence"]
        check_date(a["from"]); check_date(a["to"])
        if a["to"] < a["from"]: raise RpcError("Период недоступности задан неверно")
        absences = absences + [{"from": a["from"], "to": a["to"], "reason": a.get("reason", "Недоступен")[:100]}]
    cur.execute("UPDATE users SET capacity=%s, absences=%s WHERE id=%s", (cap, psycopg2.extras.Json(absences), p["userId"]))
    db.audit(cur, "AUDIT", "info", u["id"], "resources", "Параметры ресурса изменены", "Ресурс", p["userId"], {"capacity": cap, "absence": p.get("absence")})
    return True, msg("Параметры ресурса сохранены", "Матрица загрузки пересчитана")

# ---------------- M14: администрирование ----------------
@reg("admin.user.create")
def h_user_create(db, cur, u, p):
    guard(db, cur, u, "admin", "create")
    req(p, "name", "login", "role")
    if p["role"] not in ROLES: raise RpcError("Неизвестная роль")
    cur.execute("SELECT COUNT(*) AS n FROM users")
    if cur.fetchone()["n"] >= MAX_USERS_ONLINE: raise RpcError("Лимит 10 пользователей (ограничение лицензии по ТЗ)")
    cur.execute("SELECT 1 FROM users WHERE login=%s", (p["login"].strip().lower(),))
    if cur.fetchone(): raise RpcError("Логин уже занят")
    colors = ["#2563eb", "#16a34a", "#ea580c", "#7c3aed", "#0891b2", "#c026d3", "#ca8a04"]
    salt, h = pass_hash(p.get("password") or "demo")
    id_ = uid("u")
    cur.execute("INSERT INTO users(id,login,name,role,position,capacity,color,pass_salt,pass_hash) VALUES(%s,%s,%s,%s,%s,8,%s,%s,%s)",
                (id_, p["login"].strip().lower(), p["name"].strip()[:100], p["role"], p.get("position", "Инженер")[:100],
                 colors[secrets.randbelow(len(colors))], salt, h))
    db.audit(cur, "SECURITY", "info", u["id"], "admin", "Создана учётная запись", "Пользователь", id_, {"login": p["login"], "role": p["role"]})
    return True, msg("Пользователь создан", "Пароль по умолчанию: demo")

@reg("admin.user.role")
def h_user_role(db, cur, u, p):
    guard(db, cur, u, "admin", "edit")
    if p["id"] == u["id"]: raise RpcError("Нельзя менять роль собственной учётной записи")
    if p["role"] not in ROLES: raise RpcError("Неизвестная роль")
    cur.execute("UPDATE users SET role=%s WHERE id=%s", (p["role"], p["id"]))
    db.audit(cur, "SECURITY", "warning", u["id"], "admin", "Изменена роль пользователя", "Пользователь", p["id"], {"role": p["role"]})
    return True, msg("Роль изменена: " + ROLES[p["role"]])

@reg("admin.user.block")
def h_user_block(db, cur, u, p):
    guard(db, cur, u, "admin", "edit")
    if p["id"] == u["id"]: raise RpcError("Нельзя блокировать собственную учётную запись")
    cur.execute("UPDATE users SET blocked=NOT blocked WHERE id=%s RETURNING blocked, name", (p["id"],))
    r = cur.fetchone()
    if not r: raise RpcError("Пользователь не найден")
    if r["blocked"]: cur.execute("DELETE FROM sessions WHERE user_id=%s", (p["id"],))
    db.audit(cur, "SECURITY", "warning", u["id"], "admin",
             "Учётная запись заблокирована" if r["blocked"] else "Учётная запись разблокирована", "Пользователь", p["id"], {"user": r["name"]})
    return True, msg(r["name"] + ": " + ("заблокирована" if r["blocked"] else "разблокирована"))

@reg("admin.user.password")
def h_user_password(db, cur, u, p):
    guard(db, cur, u, "admin", "edit")
    req(p, "id", "password")
    if len(p["password"]) < 4: raise RpcError("Пароль: минимум 4 символа")
    salt, h = pass_hash(p["password"])
    cur.execute("UPDATE users SET pass_salt=%s, pass_hash=%s, failed=0, lock_until=0 WHERE id=%s", (salt, h, p["id"]))
    db.audit(cur, "SECURITY", "warning", u["id"], "admin", "Сброшен пароль пользователя", "Пользователь", p["id"], {})
    return True, msg("Пароль сброшен")

@reg("auth.password")
def h_auth_password(db, cur, u, p):
    req(p, "old", "new")
    if len(p["new"]) < 4: raise RpcError("Новый пароль: минимум 4 символа")
    cur.execute("SELECT pass_salt, pass_hash FROM users WHERE id=%s", (u["id"],))
    r = cur.fetchone()
    if not pass_verify(p["old"], r["pass_salt"], r["pass_hash"]): raise RpcError("Текущий пароль указан неверно")
    salt, h = pass_hash(p["new"])
    cur.execute("UPDATE users SET pass_salt=%s, pass_hash=%s WHERE id=%s", (salt, h, u["id"]))
    db.audit(cur, "SECURITY", "info", u["id"], "admin", "Пользователь сменил пароль", "Пользователь", u["id"], {})
    return True, msg("Пароль изменён")

@reg("admin.perm")
def h_admin_perm(db, cur, u, p):
    guard(db, cur, u, "admin", "edit")
    role, mod, op, on = p["role"], p["module"], p["op"], bool(p["on"])
    if role == "admin": raise RpcError("Роль Администратор неизменяема")
    if role not in ROLES or mod not in MODULES or op not in OPS: raise RpcError("Некорректные параметры")
    s = db.settings(cur)
    cur_set = set(s["perms"][role].get(mod, []))
    cur_set.add(op) if on else cur_set.discard(op)
    if cur_set & {"create", "edit", "approve"}: cur_set.add("view")
    s["perms"][role][mod] = sorted(cur_set, key=OPS.index)
    db.save_settings(cur, s)
    db.audit(cur, "SECURITY", "warning", u["id"], "admin", "Изменена матрица прав", "Права", role, {"role": role, "module": mod, "op": op, "on": on})
    return True, msg("Права обновлены: %s · %s" % (ROLES[role], MODULES[mod]))

@reg("admin.dict.add")
def h_dict_add(db, cur, u, p):
    guard(db, cur, u, "admin", "edit")
    s = db.settings(cur)
    if p["dict"] not in s["dicts"]: raise RpcError("Неизвестный справочник")
    req(p, "value")
    s["dicts"][p["dict"]].append(p["value"][:100])
    db.save_settings(cur, s)
    db.audit(cur, "AUDIT", "info", u["id"], "admin", "Справочник пополнен", "Справочник", p["dict"], {"value": p["value"]})
    return True, msg("Элемент добавлен")

@reg("admin.dict.remove")
def h_dict_remove(db, cur, u, p):
    guard(db, cur, u, "admin", "edit")
    s = db.settings(cur)
    if p["dict"] not in s["dicts"]: raise RpcError("Неизвестный справочник")
    v = s["dicts"][p["dict"]].pop(int(p["index"]))
    db.save_settings(cur, s)
    db.audit(cur, "AUDIT", "warning", u["id"], "admin", "Элемент справочника удалён", "Справочник", p["dict"], {"value": v})
    return True, msg("Элемент удалён", "Существующие записи сохраняют прежнее значение")

@reg("admin.sys")
def h_admin_sys(db, cur, u, p):
    guard(db, cur, u, "admin", "edit")
    s = db.settings(cur)
    s["wip"] = max(1, min(20, int(p.get("wip", s["wip"]))))
    s["sessionMin"] = max(5, min(480, int(p.get("sessionMin", s["sessionMin"]))))
    s["loadWarn"] = max(50, min(100, int(p.get("loadWarn", s["loadWarn"]))))
    s["loadOver"] = max(80, min(200, int(p.get("loadOver", s["loadOver"]))))
    db.save_settings(cur, s)
    db.audit(cur, "AUDIT", "info", u["id"], "admin", "Системные параметры изменены", "Система", "", {"wip": s["wip"], "sessionMin": s["sessionMin"]})
    return True, msg("Параметры сохранены", "WIP-лимит и пороги загрузки применяются немедленно")

@reg("admin.logs")
def h_admin_logs(db, cur, u, p):
    """Файловые журналы для M14: список, последние строки, каталог и уровень (ТЗ §15)."""
    guard(db, cur, u, "admin", "view")
    files = log_files()
    name = p.get("name") or "supip.log"
    lines = log_tail(name, p.get("lines", 300)) if any(f["name"] == name for f in files) else []
    return {"files": files, "name": name, "lines": lines, "dir": LOG_DIR,
            "level": LOG_STATE["level"], "exists": any(f["name"] == name for f in files)}, None

@reg("admin.diag")
def h_admin_diag(db, cur, u, p):
    guard(db, cur, u, "admin", "view")
    cur.execute("SELECT COUNT(*) AS n FROM tasks t LEFT JOIN projects pr ON t.project_id=pr.id WHERE t.project_id IS NOT NULL AND pr.id IS NULL")
    orphan_tasks = cur.fetchone()["n"]
    cur.execute("SELECT COUNT(*) AS n FROM time_entries")
    ntime = cur.fetchone()["n"]
    checks = [("Соединение с PostgreSQL", True), ("Ссылочная целостность задач", orphan_tasks == 0),
              ("Записи трудозатрат доступны", ntime >= 0), ("Матрица прав заполнена", len(db.settings(cur)["perms"]) == 5)]
    for name, okv in checks:
        db.audit(cur, "TECH", "info" if okv else "error", u["id"], "admin", "Диагностика: " + name, "Система", "", {"ok": okv})
    bad = sum(1 for _, okv in checks if not okv)
    return {"checks": checks}, msg("Диагностика завершена", "%d проверок, ошибок: %d · отчёт в журнале TECH" % (len(checks), bad), "ok" if not bad else "warn")

@reg("admin.reset")
def h_admin_reset(db, cur, u, p):
    guard(db, cur, u, "admin", "delete")
    cur.execute("SELECT token, user_id, created FROM sessions")
    keep_sessions = cur.fetchall()
    for tbl in ("notifications","reminders","meeting_tasks","doc_versions","docs","calcs","time_entries","tasks","risks","meetings","projects","cal_events","reports","events","settings","users"):
        cur.execute("DELETE FROM " + tbl)
    cur.execute("DELETE FROM sessions WHERE user_id NOT IN (SELECT id FROM users)")
    conn = cur.connection
    conn.commit()
    seed(db)
    for s in keep_sessions:
        cur.execute("SELECT 1 FROM users WHERE id=%s AND NOT blocked", (s["user_id"],))
        if cur.fetchone():
            cur.execute("INSERT INTO sessions(token,user_id,created,last_seen) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                        (s["token"], s["user_id"], s["created"], now_ms()))
    cur.execute("SELECT 1 FROM users WHERE id=%s", (u["id"],))
    if not cur.fetchone(): raise RpcError("Демо-данные восстановлены; войдите заново", "Сессия сброшена", 401, "RESEED")
    db.audit(cur, "TECH", "warning", u["id"], "admin", "Демо-данные восстановлены из эталонного набора", "БД", "", {})
    return True, msg("Демо-данные восстановлены", "База пересоздана из эталонного набора")

# ---------------- M17: конструктор дашбордов ----------------
@reg("builder.set")
def h_builder_set(db, cur, u, p):
    guard(db, cur, u, "builder", "edit")
    keys = p.get("keys", [])
    if not isinstance(keys, list) or any(k not in WIDGET_KEYS for k in keys): raise RpcError("Некорректный состав виджетов")
    s = db.settings(cur); s["widgets"][u["id"]] = keys; db.save_settings(cur, s)
    db.audit(cur, "AUDIT", "info", u["id"], "builder", "Изменён состав дашборда", "Дашборд", u["id"], {"keys": keys})
    return True, msg("Панель сохранена", "Главный дашборд M00 обновлён")

@reg("builder.preset")
def h_builder_preset(db, cur, u, p):
    guard(db, cur, u, "builder", "edit")
    s = db.settings(cur); s["widgets"].pop(u["id"], None); db.save_settings(cur, s)
    db.audit(cur, "AUDIT", "info", u["id"], "builder", "Восстановлен профиль роли", "Дашборд", u["id"], {"role": u["role"]})
    return True, msg("Применён профиль роли «%s»" % ROLES[u["role"]], "Персональные настройки сброшены")

# ---------------- Журналирование клиентских событий (экспорт, печать, ошибки UI) ----------------
@reg("client.log")
def h_client_log(db, cur, u, p):
    cat = p.get("cat", "INTEGRATION")
    if cat not in ("TECH", "INTEGRATION", "AUDIT"): raise RpcError("Категория недоступна клиенту")
    level = p.get("level", "info")
    if level not in ("info", "warning", "error"): level = "info"
    mod = p.get("module", "dash")
    if mod not in MODULES: mod = "dash"
    db.audit(cur, cat, level, u["id"], mod, str(p.get("action", ""))[:300], str(p.get("obj", ""))[:100], str(p.get("objId", ""))[:50], p.get("details") or {})
    return True, None

# ---------------- Экспорт документов: DOCX / XLSX / PDF / CSV / HTML (ТЗ §25) ----------------
# Генератор — docgen.py (только stdlib, встроенные шрифты, кириллица). Право «export»
# проверяется сервером для каждого документа; событие фиксируется в M15.

EXPORT_KINDS = {
    "portfolio": ("portfolio", "Портфель проектов"),
    "project":   ("portfolio", "Паспорт проекта"),
    "tasks":     ("tasks", "Реестр задач"),
    "gantt":     ("gantt", "Календарный график"),
    "resources": ("resources", "Загрузка ресурсов"),
    "time":      ("time", "Табель трудозатрат"),
    "risks":     ("risks", "Реестр рисков"),
    "calc":      ("calcs", "Протокол расчёта"),
    "meeting":   ("meetings", "Протокол совещания"),
    "directives": ("meetings", "Отчёт по поручениям"),
    "report":    ("reports", "Отчёт"),
    "journal":   ("journal", "Журнал событий"),
    "dashboard": ("analytics", "Панель показателей"),
}
EXPORT_FORMATS = ("docx", "xlsx", "pdf", "csv", "html")

def _fd(s):
    """Дата в русском формате для документов."""
    try: return datetime.date.fromisoformat(str(s)).strftime("%d.%m.%Y")
    except Exception: return str(s or "")

def _uname(db, cur, user_id):
    if not user_id: return ""
    cur.execute("SELECT name FROM users WHERE id=%s", (user_id,))
    r = cur.fetchone()
    return r["name"] if r else ""

def _pname(db, cur, pid):
    if not pid: return ""
    cur.execute("SELECT code, name FROM projects WHERE id=%s", (pid,))
    r = cur.fetchone()
    return "%s · %s" % (r["code"], r["name"]) if r else ""

def _proj_progress(db, cur, pid):
    cur.execute("SELECT COALESCE(SUM(plan_hours*progress),0) AS a, COALESCE(SUM(plan_hours),0) AS b FROM tasks WHERE project_id=%s", (pid,))
    r = cur.fetchone()
    return int(round(r["a"] / r["b"])) if r["b"] else 0

def _proj_hours(db, cur, pid):
    cur.execute("SELECT COALESCE(SUM(hours),0) AS h FROM time_entries WHERE project_id=%s AND status<>'Отклонена'", (pid,))
    return round(float(cur.fetchone()["h"]), 1)

def _export_meta(db, cur, u, extra=None):
    m = [("Система", "%s v%s" % (APP_NAME, VERSION)),
         ("Сформировал", "%s (%s)" % (u["name"], ROLES.get(u["role"], u["role"]))),
         ("Дата формирования", datetime.datetime.now().strftime("%d.%m.%Y %H:%M"))]
    return m + list(extra or [])

def build_export(db, cur, u, kind, id_="", params=None):
    """Строит документ в единой модели docgen.Document. Право export проверяется здесь же."""
    params = params or {}
    if kind not in EXPORT_KINDS: raise RpcError("Неизвестный вид документа: %s" % kind, "Экспорт", 404, "UNKNOWN_EXPORT")
    mod, label = EXPORT_KINDS[kind]
    guard(db, cur, u, mod, "export")
    if docgen is None: raise RpcError("Генератор документов недоступен (docgen.py)", "Экспорт", 500, "NO_DOCGEN")
    D = docgen.Document

    if kind == "portfolio":
        projects = db.all_ent(cur, "project")
        d = D("Портфель проектов инженерного подразделения", subtitle="Реестр проектов с показателями хода выполнения",
              meta=_export_meta(db, cur, u, [("Проектов", len(projects))]))
        d.toc = True
        rows = []
        for p in projects:
            rows.append([p["code"], p["name"], p["cat"], p["status"], _uname(db, cur, p["pm"]),
                         _fd(p["start"]), _fd(p["end"]), _proj_progress(db, cur, p["id"]),
                         p["budgetHours"], _proj_hours(db, cur, p["id"])])
        d.h1("1. Реестр проектов")
        d.table(["Код", "Наименование", "Категория", "Статус", "Руководитель", "Начало", "Окончание",
                 "Прогресс, %", "План, чел·ч", "Факт, чел·ч"], rows,
                widths=[1, 3, 1.4, 1.4, 1.6, 1, 1, 0.9, 0.9, 0.9],
                aligns=[None, None, None, None, None, None, None, "right", "right", "right"],
                title="Портфель", total=["Итого", "", "", "", "", "", "", "",
                                         sum(p["budgetHours"] for p in projects),
                                         round(sum(_proj_hours(db, cur, p["id"]) for p in projects), 1)])
        act = [p for p in projects if p["status"] in ("Выполняется", "Требует внимания", "Приостановлен")]
        d.h1("2. Структура портфеля")
        by_status = {}
        for p in projects: by_status[p["status"]] = by_status.get(p["status"], 0) + 1
        d.chart("pie", list(by_status), list(by_status.values()), title="Проекты по статусам", unit="шт")
        d.chart("bar", [p["code"] for p in act], [_proj_progress(db, cur, p["id"]) for p in act],
                title="Прогресс активных проектов", unit="%")
        d.h1("3. Основания и примечания")
        d.p("Данные выгружены из единой базы СУП-ИП (PostgreSQL). Прогресс проекта рассчитан как средневзвешенный "
            "по плановой трудоёмкости задач; фактические трудозатраты — сумма подтверждённых списаний M06.")
        return d, "portfel_proektov"

    if kind == "project":
        p = db.get_ent(cur, "project", id_)
        if not p: raise RpcError("Проект не найден", "Экспорт", 404, "NOT_FOUND")
        guard(db, cur, u, "portfolio", "export", p)
        tasks = [t for t in db.all_ent(cur, "task") if t["projectId"] == id_]
        risks = [r for r in db.all_ent(cur, "risk") if r["projectId"] == id_]
        docs_ = [x for x in db.all_ent(cur, "doc") if x["projectId"] == id_]
        calcs = [c for c in db.all_ent(cur, "calc") if c["projectId"] == id_]
        d = D("Паспорт проекта %s" % p["code"], subtitle=p["name"],
              meta=_export_meta(db, cur, u, [("Код проекта", p["code"]), ("Категория", p["cat"]), ("Статус", p["status"])]))
        d.toc = True
        d.h1("1. Общие сведения")
        d.kv([("Наименование", p["name"]), ("Категория", p["cat"]), ("Статус", p["status"]),
              ("Руководитель проекта", _uname(db, cur, p["pm"])),
              ("Начало", _fd(p["start"])), ("Окончание", _fd(p["end"])),
              ("Бюджет трудоёмкости, чел·ч", p["budgetHours"]),
              ("Команда", ", ".join(_uname(db, cur, x) for x in (p["team"] or []))),
              ("Прогресс, %", _proj_progress(db, cur, id_)), ("Факт трудозатрат, чел·ч", _proj_hours(db, cur, id_))])
        if p["desc"]: d.p(p["desc"])
        if p.get("baseline"):
            b = p["baseline"]
            d.h2("1.1. Базовый план")
            d.kv([("Начало (базовый план)", _fd(b.get("start"))), ("Окончание (базовый план)", _fd(b.get("end"))),
                  ("Зафиксирован", datetime.datetime.fromtimestamp((p.get("baselineAt") or 0) / 1000).strftime("%d.%m.%Y %H:%M") if p.get("baselineAt") else "")])
        d.h1("2. Задачи проекта")
        d.table(["Код", "Задача", "Тип", "Статус", "Исполнитель", "Начало", "Окончание", "План, ч", "Прогресс, %"],
                [[t["code"], t["title"], t["type"], t["status"], _uname(db, cur, t["assignee"]),
                  _fd(t["start"]), _fd(t["end"]), t["planHours"], t["progress"]] for t in tasks],
                widths=[1, 3, 1, 1.4, 1.6, 1, 1, 0.8, 0.9],
                aligns=[None, None, None, None, None, None, None, "right", "right"],
                total=["Итого", "", "", "", "", "", "", sum(t["planHours"] for t in tasks), ""])
        d.h1("3. Риски проекта")
        d.table(["Код", "Риск", "P", "I", "Балл", "Стратегия", "Владелец", "Статус"],
                [[r["code"], r["title"], r["p"], r["i"], r["p"] * r["i"], r["strategy"],
                  _uname(db, cur, r["owner"]), r["status"]] for r in risks],
                widths=[1, 3, 0.5, 0.5, 0.6, 1.4, 1.6, 1.2],
                aligns=[None, None, "right", "right", "right", None, None, None])
        d.h1("4. Документы и расчёты")
        d.table(["Код", "Наименование", "Тип", "Статус", "Версий"],
                [[x["code"], x["title"], x["type"], x["status"], len(x.get("versions") or [])] for x in docs_],
                widths=[1, 3.4, 1.4, 1.4, 0.7], title="Документы (M09)")
        d.table(["Код", "Расчёт", "Модель", "Версия", "Статус"],
                [[c["code"], c["title"], c["model"], c["ver"], c["status"]] for c in calcs],
                widths=[1, 3, 2, 0.8, 1.2], title="Инженерные расчёты (M10)")
        d.signatures([("Руководитель проекта", _uname(db, cur, p["pm"])), ("Согласовано", "")])
        return d, "passport_%s" % p["code"]

    if kind == "tasks":
        tasks = db.all_ent(cur, "task")
        if params.get("projectId"): tasks = [t for t in tasks if t["projectId"] == params["projectId"]]
        if params.get("status"): tasks = [t for t in tasks if t["status"] == params["status"]]
        if params.get("assignee"): tasks = [t for t in tasks if t["assignee"] == params["assignee"]]
        scope = _pname(db, cur, params.get("projectId")) if params.get("projectId") else "Все доступные проекты"
        d = D("Реестр задач", subtitle=scope, meta=_export_meta(db, cur, u, [("Задач", len(tasks))]))
        d.h1("1. Задачи")
        d.table(["Код", "Задача", "Проект", "Тип", "Статус", "Приоритет", "Исполнитель",
                 "Начало", "Окончание", "План, ч", "Прогресс, %"],
                [[t["code"], t["title"], (db.get_ent(cur, "project", t["projectId"]) or {}).get("code", ""),
                  t["type"], t["status"], t["priority"], _uname(db, cur, t["assignee"]),
                  _fd(t["start"]), _fd(t["end"]), t["planHours"], t["progress"]] for t in tasks],
                widths=[0.9, 3, 0.9, 0.9, 1.3, 1, 1.5, 0.9, 0.9, 0.7, 0.8],
                aligns=[None] * 9 + ["right", "right"],
                total=["Итого", "", "", "", "", "", "", "", "", sum(t["planHours"] for t in tasks), ""])
        by_status = {}
        for t in tasks: by_status[t["status"]] = by_status.get(t["status"], 0) + 1
        d.h1("2. Распределение по статусам")
        d.chart("bar", list(by_status), list(by_status.values()), title="Задачи по статусам", unit="шт")
        overdue = [t for t in tasks if t.get("end") and t["end"] < dISO(today()) and t["status"] not in ("Завершена", "Отменена", "Проверена")]
        if overdue:
            d.h1("3. Просроченные задачи")
            d.table(["Код", "Задача", "Исполнитель", "Срок", "Просрочка, дн"],
                    [[t["code"], t["title"], _uname(db, cur, t["assignee"]), _fd(t["end"]),
                      diffD(dISO(today()), t["end"])] for t in overdue],
                    widths=[1, 3.4, 1.8, 1, 1], aligns=[None, None, None, None, "right"])
        return d, "reestr_zadach"

    if kind == "gantt":
        pid = params.get("projectId") or id_
        projects = db.all_ent(cur, "project")
        if pid: projects = [p for p in projects if p["id"] == pid]
        d = D("Календарный график работ", subtitle="Диаграмма Ганта · план и факт",
              meta=_export_meta(db, cur, u, [("Проектов", len(projects))]))
        for p in projects:
            tasks = sorted([t for t in db.all_ent(cur, "task") if t["projectId"] == p["id"]], key=lambda t: t["start"])
            if not tasks: continue
            d.h1("%s · %s" % (p["code"], p["name"]))
            d.kv([("Сроки проекта", "%s — %s" % (_fd(p["start"]), _fd(p["end"]))),
                  ("Статус", p["status"]), ("Прогресс, %", _proj_progress(db, cur, p["id"]))])
            d.table(["Код", "Работа", "Исполнитель", "Начало", "Окончание", "Длит., дн", "Прогресс, %", "Предшественники"],
                    [[t["code"], t["title"], _uname(db, cur, t["assignee"]), _fd(t["start"]), _fd(t["end"]),
                      max(1, diffD(t["end"], t["start"]) + 1) if t.get("end") else "—", t["progress"],
                      ", ".join((db.get_ent(cur, "task", x["id"] if isinstance(x, dict) else x) or {}).get("code", "")
                                for x in (t["deps"] or []))] for t in tasks],
                    widths=[0.9, 3, 1.5, 0.9, 0.9, 0.7, 0.8, 1.2],
                    aligns=[None, None, None, None, None, "right", "right", None],
                    title="График работ")
            d.chart("bar", [t["code"] for t in tasks], [max(1, diffD(t["end"], t["start"]) + 1) if t.get("end") else 0 for t in tasks],
                    title="Длительность работ", unit="дн")
            d.pagebreak()
        return d, "kalendarnyy_grafik"

    if kind == "resources":
        users = [x for x in db.users_public(cur) if x["role"] in ("exec", "pm", "head")]
        d = D("Загрузка ресурсов подразделения", subtitle="Плановая загрузка и фактические трудозатраты",
              meta=_export_meta(db, cur, u, [("Сотрудников", len(users))]))
        rows = []
        for x in users:
            cur.execute("SELECT COALESCE(SUM(plan_hours),0) AS p FROM tasks WHERE assignee=%s AND status NOT IN ('Завершена','Отменена')", (x["id"],))
            plan = int(cur.fetchone()["p"])
            cur.execute("SELECT COALESCE(SUM(hours),0) AS h FROM time_entries WHERE user_id=%s", (x["id"],))
            fact = round(float(cur.fetchone()["h"]), 1)
            cur.execute("SELECT COUNT(*) AS n FROM tasks WHERE assignee=%s AND status NOT IN ('Завершена','Отменена')", (x["id"],))
            n = cur.fetchone()["n"]
            rows.append([x["name"], x["position"], ROLES.get(x["role"], x["role"]), x["capacity"], n, plan, fact])
        d.h1("1. Ресурсный лист")
        d.table(["Сотрудник", "Должность", "Роль", "Норма, ч/дн", "Активных задач", "План, чел·ч", "Факт, чел·ч"], rows,
                widths=[2, 2, 1.6, 1, 1, 1, 1], aligns=[None, None, None, "right", "right", "right", "right"],
                total=["Итого", "", "", "", sum(r[4] for r in rows), sum(r[5] for r in rows), round(sum(r[6] for r in rows), 1)])
        d.chart("bar", [r[0].split()[0] for r in rows], [r[5] for r in rows], title="Плановая загрузка", unit="чел·ч")
        return d, "zagruzka_resursov"

    if kind == "time":
        entries = db.all_ent(cur, "time", order="id")
        if params.get("from"): entries = [e for e in entries if e["date"] >= params["from"]]
        if params.get("to"): entries = [e for e in entries if e["date"] <= params["to"]]
        if params.get("userId"): entries = [e for e in entries if e["userId"] == params["userId"]]
        if u["role"] == "exec": entries = [e for e in entries if e["userId"] == u["id"]]
        entries.sort(key=lambda e: (e["date"], e["userId"]))
        period = "%s — %s" % (_fd(params.get("from") or (entries[0]["date"] if entries else dISO(today()))),
                              _fd(params.get("to") or dISO(today())))
        d = D("Табель учёта трудозатрат", subtitle="Период: " + period,
              meta=_export_meta(db, cur, u, [("Записей", len(entries)),
                                             ("Всего часов", round(sum(e["hours"] for e in entries), 1))]))
        d.h1("1. Списания трудозатрат")
        d.table(["Дата", "Сотрудник", "Проект", "Задача", "Вид деятельности", "Часы", "Статус", "Комментарий"],
                [[_fd(e["date"]), _uname(db, cur, e["userId"]),
                  (db.get_ent(cur, "project", e["projectId"]) or {}).get("code", ""),
                  (db.get_ent(cur, "task", e["taskId"]) or {}).get("code", ""),
                  e["act"], e["hours"], e["status"], e["comment"]] for e in entries],
                widths=[1, 1.8, 0.9, 0.9, 1.8, 0.6, 1.2, 2],
                aligns=[None, None, None, None, None, "right", None, None],
                total=["Итого", "", "", "", "", round(sum(e["hours"] for e in entries), 1), "", ""])
        by_user = {}
        for e in entries: by_user[_uname(db, cur, e["userId"])] = by_user.get(_uname(db, cur, e["userId"]), 0) + e["hours"]
        d.h1("2. Распределение по сотрудникам")
        d.chart("bar", [k.split()[0] for k in by_user], [round(v, 1) for v in by_user.values()],
                title="Трудозатраты по сотрудникам", unit="ч")
        d.signatures([("Руководитель подразделения", ""), ("Бухгалтерия", "")])
        return d, "tabel_trudozatrat"

    if kind == "risks":
        risks = db.all_ent(cur, "risk")
        if params.get("projectId"): risks = [r for r in risks if r["projectId"] == params["projectId"]]
        d = D("Реестр рисков", subtitle="Инженерные и организационные риски подразделения",
              meta=_export_meta(db, cur, u, [("Рисков", len(risks))]))
        d.h1("1. Реестр")
        d.table(["Код", "Риск", "Категория", "Проект", "P", "I", "Балл", "Стратегия", "Владелец", "Статус", "Пересмотр"],
                [[r["code"], r["title"], r["cat"], (db.get_ent(cur, "project", r["projectId"]) or {}).get("code", ""),
                  r["p"], r["i"], r["p"] * r["i"], r["strategy"], _uname(db, cur, r["owner"]), r["status"], _fd(r["review"])] for r in risks],
                widths=[0.9, 3, 1.2, 0.9, 0.4, 0.4, 0.5, 1.2, 1.5, 1.2, 0.9],
                aligns=[None, None, None, None, "right", "right", "right", None, None, None, None])
        crit = [r for r in risks if r["p"] * r["i"] >= 15]
        d.h1("2. Критические риски (балл ≥ 15)")
        if crit:
            for r in crit:
                d.h2("%s · %s (балл %d)" % (r["code"], r["title"], r["p"] * r["i"]))
                if r["desc"]: d.p(r["desc"])
                ms = r.get("measures") or []
                if ms:
                    d.table(["Мероприятие", "Ответственный", "Срок", "Состояние"],
                            [[m.get("text", ""), _uname(db, cur, m.get("resp")), _fd(m.get("due")),
                              "выполнено" if m.get("done") else "в работе"] for m in ms],
                            widths=[3.4, 1.6, 1, 1])
        else:
            d.p("Критических рисков не зарегистрировано.")
        d.chart("pie", ["Низкий", "Средний", "Высокий", "Критический"],
                [sum(1 for r in risks if r["p"] * r["i"] < 6),
                 sum(1 for r in risks if 6 <= r["p"] * r["i"] < 12),
                 sum(1 for r in risks if 12 <= r["p"] * r["i"] < 15),
                 sum(1 for r in risks if r["p"] * r["i"] >= 15)],
                title="Профиль рисков", unit="шт")
        return d, "reestr_riskov"

    if kind == "calc":
        c = db.get_ent(cur, "calc", id_)
        if not c: raise RpcError("Расчёт не найден", "Экспорт", 404, "NOT_FOUND")
        model = CALC_MODELS.get(c["model"], {})
        labels = {k: n for k, n, lo, hi in model.get("inputs", [])}
        ranges = {k: (lo, hi) for k, n, lo, hi in model.get("inputs", [])}
        outs = c["outputs"] or {}
        d = D("Протокол инженерного расчёта %s" % c["code"], subtitle=c["title"],
              meta=_export_meta(db, cur, u, [("Методика", model.get("name", c["model"])),
                                             ("Версия методики", c["ver"]),
                                             ("Проект", _pname(db, cur, c["projectId"])),
                                             ("Статус", c["status"])]))
        d.toc = True
        d.h1("1. Исходные данные")
        d.table(["Параметр", "Обозначение", "Значение", "Допустимый диапазон"],
                [[labels.get(k, k), k, v, "%s … %s" % ranges[k] if k in ranges else ""]
                 for k, v in (c["inputs"] or {}).items()],
                widths=[3, 1, 1, 1.4], aligns=[None, None, "right", None])
        d.h1("2. Результаты расчёта")
        d.table(["Показатель", "Обозначение", "Значение", "Единица"],
                [[o.get("n", o.get("k")), o.get("k"), o.get("v"), o.get("u", "")] for o in outs.get("outs", [])],
                widths=[3, 1, 1, 0.8], aligns=[None, None, "right", None])
        d.kv([("Заключение методики", outs.get("verdict", "")),
              ("Условие выполнено", "да" if outs.get("ok") else "нет")])
        if outs.get("formula"):
            d.h1("3. Формулы методики")
            d.p(outs["formula"])
        runs = c.get("runs") or []
        if runs:
            d.h1("4. История выполнения")
            d.table(["№", "Дата", "Пользователь", "Примечание"],
                    [[i + 1, datetime.datetime.fromtimestamp(r.get("ts", 0) / 1000).strftime("%d.%m.%Y %H:%M"),
                      _uname(db, cur, r.get("by")), r.get("note", "")] for i, r in enumerate(runs)],
                    widths=[0.5, 1.5, 2, 3])
        d.h1("5. Проверка и подписи")
        d.kv([("Проверил", _uname(db, cur, c.get("verifiedBy"))),
              ("Дата проверки", datetime.datetime.fromtimestamp(c["verifiedAt"] / 1000).strftime("%d.%m.%Y %H:%M")
               if c.get("verifiedAt") else "не проверен")])
        d.signatures([("Выполнил", _uname(db, cur, c.get("by"))), ("Проверил", _uname(db, cur, c.get("verifiedBy")))])
        return d, "raschet_%s" % c["code"]

    if kind == "meeting":
        m = db.get_ent(cur, "meeting", id_)
        if not m: raise RpcError("Совещание не найдено", "Экспорт", 404, "NOT_FOUND")
        guard(db, cur, u, "meetings", "export", m)
        d = meeting_protocol_doc(db, cur, u, m)
        return d, "protokol_%s" % m["code"]

    if kind == "directives":
        rep = directive_report_data(db, cur, u, params.get("kind") or "registry", params)
        d = D(rep["title"], subtitle="Отчётность по поручениям совещаний (ТЗ M16 §2.11.2)",
              meta=_export_meta(db, cur, u, [("Вид отчёта", rep["name"]), ("Записей", rep["total"])] + rep["meta"]))
        d.toc = True
        d.h1("1. Сводные показатели")
        d.kv(rep["summary"])
        d.h1("2. " + rep["name"])
        if rep["rows"]:
            d.table(rep["columns"], rep["rows"], title=rep["name"])
        else:
            d.p("По заданному отбору записей нет.")
        if rep.get("chart"):
            ch = rep["chart"]
            d.h1("3. Графическое представление")
            d.chart(ch["type"], ch["labels"], ch["values"], title=ch.get("title", ""), unit=ch.get("unit", ""))
        d.note("Отчёт сформирован из реестра поручений M16. Статус поручения синхронизирован со связанной задачей M02; "
               "статус «Выполнено» устанавливается только после приёмки результата руководителем (ТЗ §2.11).")
        d.signatures([("Составил", u["name"]), ("Проверил", "")])
        return d, "poryucheniya_%s" % rep["kind"]

    if kind == "report":
        r = db.get_ent(cur, "report", id_)
        if not r: raise RpcError("Отчёт не найден", "Экспорт", 404, "NOT_FOUND")
        d = D(r["title"], subtitle="Отчёт %s · снимок данных от %s" %
              (r["code"], datetime.datetime.fromtimestamp(r["ts"] / 1000).strftime("%d.%m.%Y %H:%M")),
              meta=_export_meta(db, cur, u, [("Вид отчёта", r["type"]), ("Код отчёта", r["code"]),
                                             ("Статус", r["status"]), ("Автор снимка", _uname(db, cur, r["by"]))]))
        d.blocks.extend(docgen.blocks_from_html(r["html"]))
        d.note("Документ сформирован из сохранённого снимка отчёта (M13). Данные соответствуют моменту формирования снимка.")
        return d, "otchet_%s" % r["code"]

    if kind == "journal":
        if u["role"] not in ("admin", "head"): raise RpcError("Выгрузка журнала доступна администратору и руководителю", "Экспорт", 403, "FORBIDDEN")
        cur.execute("SELECT ts,cat,level,user_id,module,action,obj,obj_id,details FROM events ORDER BY ts DESC LIMIT 5000")
        rows = cur.fetchall()
        d = D("Журнал событий системы", subtitle="Выгрузка M15 · последние %d событий" % len(rows),
              meta=_export_meta(db, cur, u, [("Событий", len(rows))]))
        d.table(["Время", "Категория", "Уровень", "Пользователь", "Модуль", "Действие", "Объект", "ID"],
                [[datetime.datetime.fromtimestamp(e["ts"] / 1000).strftime("%d.%m.%Y %H:%M:%S"),
                  e["cat"], e["level"], _uname(db, cur, e["user_id"]) or e["user_id"],
                  MODULES.get(e["module"], e["module"]), e["action"], e["obj"], e["obj_id"]] for e in rows],
                widths=[1.5, 1, 0.7, 1.5, 0.6, 3, 1, 0.9], title="События")
        return d, "zhurnal_sobytiy"

    if kind == "dashboard":
        widgets = params.get("widgets") or []
        title = params.get("title") or "Панель показателей"
        d = D(title, subtitle="Выгрузка настроенной панели (M17)",
              meta=_export_meta(db, cur, u, [("Виджетов", len(widgets))]))
        for w in widgets:
            wt = w.get("type")
            d.h1(w.get("title") or "Виджет")
            if wt == "kpi":
                d.kv([(w.get("title") or "Показатель", w.get("value"))])
            elif wt in ("bar", "line", "pie"):
                d.chart(wt, w.get("labels") or [], w.get("values") or [], title=w.get("title") or "", unit=w.get("unit") or "")
            elif wt == "table":
                d.table(w.get("head") or [], w.get("rows") or [], title="")
            elif wt == "html":
                d.blocks.extend(docgen.blocks_from_html(w.get("html") or ""))
            else:
                d.p(w.get("text") or "")
        d.note("Значения рассчитаны из единого состояния СУП-ИП с учётом прав пользователя.")
        return d, "panel_pokazateley"

    raise RpcError("Вид документа не реализован: %s" % kind, "Экспорт", 500, "NOT_IMPLEMENTED")


def meeting_protocol_doc(db, cur, u, m):
    """Протокол совещания по структуре ТЗ §2.8 (M16): 8 обязательных разделов."""
    att = m.get("attendees") or []
    decs = m.get("decisions") or []
    d = docgen.Document("Протокол совещания %s" % m["code"], subtitle=m["title"],
                        meta=[("Вид совещания", m["type"]), ("Дата", _fd(m["date"])), ("Время", m.get("time") or ""),
                              ("Председатель", _uname(db, cur, m["chair"])), ("Секретарь", _uname(db, cur, m["secretary"])),
                              ("Статус", m["status"]), ("Сформировал", u["name"]),
                              ("Дата формирования", datetime.datetime.now().strftime("%d.%m.%Y %H:%M"))])
    d.toc = True
    d.h1("1. Общие сведения")
    prj_codes = []
    for pid in mprojects_of(m):
        pr = db.get_ent(cur, "project", pid)
        prj_codes.append("%s · %s" % (pr["code"], pr["name"]) if pr else pid)
    d.kv([("Наименование", m["title"]), ("Вид совещания", m["type"]),
          ("Дата и время", "%s %s" % (_fd(m["date"]), m.get("time") or "")),
          ("Председатель", _uname(db, cur, m["chair"])), ("Секретарь", _uname(db, cur, m["secretary"])),
          ("Связанные проекты", "; ".join(prj_codes) or "не зарегистрированы"),
          ("Повторение", rec_text(rec_of(m)))])
    mats = materials_of(m)
    if mats:
        d.h2("Предварительные материалы (§2.5)")
        d.table(["Материал", "Вид / ссылка", "Примечание"],
                [[x.get("name", ""), "Документ M09" if x.get("kind") == "doc" else x.get("url", ""),
                  x.get("note") or "—"] for x in mats], widths=[3, 2.2, 1.6])
    pl = plan_of(m)
    if pl:
        d.h2("Планирование: переносы, отмены, изменения (§2.5)")
        d.table(["Дата и время", "Кто", "Действие", "Основание"],
                [[datetime.datetime.fromtimestamp(x["ts"] / 1000).strftime("%d.%m.%Y %H:%M"),
                  _uname(db, cur, x.get("by")), MEET_PLAN_ACT.get(x.get("action"), x.get("action", "")),
                  (x.get("reason") or x.get("note") or "—") +
                  (" (%s %s → %s %s)" % (_fd(x["from"]["date"]), x["from"].get("time", ""),
                                         _fd(x["to"]["date"]), x["to"].get("time", ""))
                   if x.get("from") and x.get("to") else "")] for x in pl],
                widths=[1.3, 1.5, 1.4, 2.6])
    d.h1("2. Присутствовали")
    if att: d.list(["%s — %s" % (_uname(db, cur, a), _role_of(db, cur, a)) for a in att], numbered=True)
    else: d.p("Состав участников не зарегистрирован.")
    d.h1("3. Повестка дня")
    ag = m.get("agenda") or []
    if ag:
        d.table(["№", "Вопрос", "Докладчик"],
                [[a.get("n", i + 1), a.get("title", ""), _uname(db, cur, a.get("reporter"))] for i, a in enumerate(ag)],
                widths=[0.5, 4, 1.6])
    else: d.p("Вопросы повестки не зарегистрированы.")
    d.h1("4. Рассмотрение вопросов")
    if ag:
        for a in ag:
            d.h2("Вопрос %s. %s" % (a.get("n", ""), a.get("title", "")))
            d.p("Докладчик: %s." % _uname(db, cur, a.get("reporter")))
            d.p(a.get("discussion") or "Обсуждение зафиксировано в решениях по вопросу.")
    else:
        d.p("Материалы обсуждения не зарегистрированы.")
    d.h1("5. Принятые решения")
    if decs:
        d.table(["№", "Решение", "Ответственный", "Срок", "Поручения"],
                [[i + 1, x.get("text", ""), _uname(db, cur, x.get("resp")), _fd(x.get("due")),
                  ", ".join((db.get_ent(cur, "task", tid) or {}).get("code", "") for tid in dec_task_ids(x)) or "—"]
                 for i, x in enumerate(decs)],
                widths=[0.5, 3.6, 1.6, 1, 0.9], title="Реестр решений")
    else: d.p("Решения не зарегистрированы.")
    d.h1("6. Контроль исполнения")
    rows = []
    for x in decs:
        ts = [db.get_ent(cur, "task", tid) for tid in dec_task_ids(x)]
        ts = [t for t in ts if t]
        if ts:
            for t in ts:
                dr = t.get("directive") or {}
                over = diffD(dISO(today()), t["end"]) if t.get("end") and t["end"] < dISO(today()) and t["status"] not in DONE_ST + ["Отменена"] else 0
                rows.append([t["code"], t["title"], _uname(db, cur, t["assignee"]),
                             ", ".join(_uname(db, cur, c) for c in dr.get("coassignees") or []) or "—",
                             _fd(t["end"]), t["status"], t["progress"], over])
        else:
            rows.append(["—", x.get("text", ""), _uname(db, cur, x.get("resp")), "—", _fd(x.get("due")), "поручение не создано", 0, 0])
    d.table(["Поручение", "Содержание", "Исполнитель", "Соисполнители", "Срок", "Состояние", "Прогресс, %", "Просрочка, дн"], rows,
            widths=[0.8, 2.6, 1.3, 1.3, 0.8, 1.2, 0.7, 0.8], aligns=[None, None, None, None, None, None, "right", "right"])
    crit = [[(db.get_ent(cur, "task", tid) or {}).get("code", ""),
             ((db.get_ent(cur, "task", tid) or {}).get("directive") or {}).get("criteria", "") or "—"]
            for x in decs for tid in dec_task_ids(x)]
    if crit:
        d.h2("Критерии завершения поручений")
        d.table(["Поручение", "Критерии завершения"], crit, widths=[1, 5.5])
    d.h1("7. Подписи")
    d.signatures([("Председатель совещания", _uname(db, cur, m["chair"])),
                  ("Секретарь совещания", _uname(db, cur, m["secretary"]))])
    d.h1("8. Приложения")
    docs_ = [x for x in db.all_ent(cur, "doc") if x.get("taskId") in [x2.get("taskId") for x2 in decs if x2.get("taskId")]]
    if docs_:
        d.table(["Код", "Документ", "Тип", "Статус"],
                [[x["code"], x["title"], x["type"], x["status"]] for x in docs_], widths=[1, 3.4, 1.4, 1.4])
    else:
        d.p("Приложения не зарегистрированы.")
    return d

def _role_of(db, cur, user_id):
    if not user_id: return ""
    cur.execute("SELECT role, position FROM users WHERE id=%s", (user_id,))
    r = cur.fetchone()
    return (r["position"] or ROLES.get(r["role"], "")) if r else ""

@reg("meeting.protocol")
def h_meeting_protocol(db, cur, u, p):
    """Формирование протокола: DOCX + PDF регистрируются в M09 как версии документа (ТЗ §2.8, §2.12)."""
    m = get_req(db, cur, "meeting", p["id"])
    guard(db, cur, u, "meetings", "export", m)
    guard(db, cur, u, "docs", "create")
    fmts = [f for f in (p.get("formats") or ["docx", "pdf"])]
    reg = _protocol_register(db, cur, u, m, fmts,
                             "Протокол совещания · представления: %s" % ", ".join(f.upper() for f in fmts))
    db.audit(cur, "BUSINESS", "info", u["id"], "meetings", "Сформирован протокол совещания", "Совещание", m["id"],
             {"code": m["code"], "doc": reg["docCode"], "v": reg["docV"], "formats": fmts})
    return {"docId": reg["docId"], "version": reg["docV"], "files": reg["files"]}, msg(
        "Протокол сформирован (версия %d)" % reg["docV"], "DOCX и PDF зарегистрированы в M09 · событие в M15")

# ---------------- Маршрут согласования и версии протокола (ТЗ M16 §2.9) ----------------
# Статусы протокола, настраиваемый маршрут (секретарь → председатель → руководитель → иные
# согласующие), регистрация даты направления, согласующего, результата и замечаний.
# Утверждённый протокол сохраняется отдельной версией (DOCX+PDF в M09 + снимок содержания);
# изменение утверждённого протокола возможно только через новую редакцию с сохранением истории.

PROTOCOL_STATUSES = ["Подготовка", "Черновик", "Направлен на согласование", "На доработке",
                     "Согласован", "Утверждён", "Опубликован", "Архивный"]
PROTOCOL_ROLES = ["Секретарь", "Председатель", "Руководитель подразделения", "Согласующий"]
PROTOCOL_EDITABLE = ("Подготовка", "Черновик", "На доработке")

def protocol_default():
    return {"status": "Подготовка", "route": [], "sentAt": None, "sentBy": None,
            "approvedAt": None, "approvedBy": None, "publishedAt": None,
            "version": 0, "versions": [], "comments": []}

def prot_of(m):
    """Состояние протокола совещания (для записей прежних версий — значения по умолчанию)."""
    p = dict(protocol_default())
    p.update(m.get("protocol") or {})
    for k in ("route", "versions", "comments"):
        if not isinstance(p.get(k), list): p[k] = []
    return p

def _prot_step_pending(pr):
    for i, s in enumerate(pr["route"]):
        if s.get("state") == "Ожидает": return i, s
    return None, None

def _prot_comment(pr, u, kind, text):
    pr["comments"].append({"ts": now_ms(), "by": u["id"], "kind": kind, "text": (text or "")[:1000]})
    pr["comments"] = pr["comments"][-100:]

def _prot_default_route(db, cur, m):
    """Типовой маршрут: секретарь → председатель → руководитель подразделения (ТЗ §2.9)."""
    route = []
    if m.get("secretary"): route.append({"user": m["secretary"], "role": "Секретарь"})
    if m.get("chair") and m["chair"] != m.get("secretary"): route.append({"user": m["chair"], "role": "Председатель"})
    for usr in db.users_public(cur):
        if usr["role"] == "head" and not usr["blocked"] and usr["id"] not in [x["user"] for x in route]:
            route.append({"user": usr["id"], "role": "Руководитель подразделения"}); break
    return route

def _prot_notify_step(db, cur, m, pr, step):
    notify(db, cur, step["user"], "protocol.review", "Протокол на согласование: %s" % m["title"],
           "%s · роль в маршруте: %s" % (m["code"], step.get("role", "Согласующий")), "Совещание", m["id"],
           dedupe="protocol.review:%s:%s:%s:%s" % (m["id"], step["user"], pr["version"], pr.get("sentAt")))

def _protocol_register(db, cur, u, m, fmts, note):
    """Формирование файлов протокола и регистрация версии в M09 (общий код для §2.8 и §2.9)."""
    if docgen is None: raise RpcError("Генератор документов недоступен", "Экспорт", 500, "NO_DOCGEN")
    d = meeting_protocol_doc(db, cur, u, m)
    cur.execute("SELECT id FROM docs WHERE task_id IS NULL AND title=%s", ("Протокол совещания %s" % m["code"],))
    r = cur.fetchone()
    if r:
        doc = db.get_ent(cur, "doc", r["id"])
    else:
        n = next_seq(db, cur, "doc")
        doc = dict(id=uid("d"), code="ДОК-%03d" % n, title="Протокол совещания %s" % m["code"], type="Протокол",
                   projectId=None, taskId=None, riskId=None, status="Проект", author=u["id"], versions=[])
        db.save_ent(cur, "doc", doc)
    v = max([x["v"] for x in (doc.get("versions") or [])] or [0]) + 1
    for fmt in fmts:
        if fmt not in ("docx", "pdf", "xlsx", "html"): raise RpcError("Недопустимый формат протокола: %s" % fmt)
    os.makedirs(FILES_DIR, exist_ok=True)
    stored, primary = [], None
    for i, fmt in enumerate(fmts):
        data = docgen.render(d, fmt)
        fname = "protokol_%s_v%d.%s" % (m["code"], v, fmt)
        path = file_path(doc["id"], v) if i == 0 else file_path(doc["id"], v) + "." + fmt
        with open(path, "wb") as f: f.write(data)
        if i == 0: primary = (fname, len(data))
        stored.append({"fmt": fmt, "name": fname, "size": len(data)})
    cur.execute("INSERT INTO doc_versions(doc_id,v,ts,by_user,size,note,file_name,stored) VALUES(%s,%s,%s,%s,%s,%s,%s,TRUE) "
                "ON CONFLICT (doc_id,v) DO UPDATE SET ts=EXCLUDED.ts, size=EXCLUDED.size, note=EXCLUDED.note, "
                "file_name=EXCLUDED.file_name, stored=TRUE",
                (doc["id"], v, now_ms(), u["id"], primary[1], note, primary[0]))
    db.audit(cur, "AUDIT", "info", u["id"], "docs", "Зарегистрирована версия документа", "Документ", doc["id"],
             {"v": v, "source": "M16"})
    return {"docId": doc["id"], "docCode": doc["code"], "docV": v, "files": stored}

@reg("protocol.route")
def h_protocol_route(db, cur, u, p):
    """Настройка маршрута согласования (ТЗ §2.9): состав и порядок согласующих."""
    m = get_req(db, cur, "meeting", p["id"]); guard(db, cur, u, "meetings", "edit", m)
    pr = prot_of(m)
    if pr["status"] not in PROTOCOL_EDITABLE:
        raise RpcError("Маршрут нельзя менять в статусе «%s»" % pr["status"], "Протокол")
    route = []
    for item in (p.get("route") or [])[:8]:
        uid_ = item.get("user") if isinstance(item, dict) else item
        if not uid_: continue
        cur.execute("SELECT id FROM users WHERE id=%s AND NOT blocked", (uid_,))
        if not cur.fetchone(): raise RpcError("Согласующий не найден: %s" % uid_)
        if uid_ in [x["user"] for x in route]: continue
        role = (item.get("role") if isinstance(item, dict) else None) or "Согласующий"
        if role not in PROTOCOL_ROLES: role = "Согласующий"
        route.append({"user": uid_, "role": role, "state": "Ожидает", "ts": None, "comment": ""})
    pr["route"] = route
    m["protocol"] = pr
    db.save_ent(cur, "meeting", m)
    db.audit(cur, "AUDIT", "info", u["id"], "meetings", "Настроен маршрут согласования протокола", "Совещание", m["id"],
             {"code": m["code"], "route": [x["user"] for x in route], "event": "MEETING_PROTOCOL_ROUTE_SET"})
    return m, msg("Маршрут согласования сохранён", "Согласующих: %d" % len(route))

@reg("protocol.send")
def h_protocol_send(db, cur, u, p):
    """Направление протокола на согласование: фиксируется дата, маршрут и первый согласующий."""
    m = get_req(db, cur, "meeting", p["id"]); guard(db, cur, u, "meetings", "edit", m)
    if m["status"] not in ("Проведено", "Протокол утверждён"):
        raise RpcError("Направить можно протокол проведённого совещания", "Протокол")
    pr = prot_of(m)
    if pr["status"] not in PROTOCOL_EDITABLE:
        raise RpcError("Протокол уже в статусе «%s»" % pr["status"], "Протокол")
    if not pr["route"]:
        pr["route"] = [dict(x, state="Ожидает", ts=None, comment="") for x in _prot_default_route(db, cur, m)]
    if not pr["route"]: raise RpcError("Маршрут согласования пуст: назначьте согласующих", "Протокол")
    for s in pr["route"]: s.update(state="Ожидает", ts=None, comment="")
    pr["status"] = "Направлен на согласование"
    pr["sentAt"] = now_ms(); pr["sentBy"] = u["id"]
    _prot_comment(pr, u, "Направление", p.get("note", ""))
    m["protocol"] = pr
    db.save_ent(cur, "meeting", m)
    _prot_notify_step(db, cur, m, pr, pr["route"][0])
    db.audit(cur, "BUSINESS", "info", u["id"], "meetings", "Протокол направлен на согласование", "Совещание", m["id"],
             {"code": m["code"], "route": [x["user"] for x in pr["route"]], "event": "MEETING_PROTOCOL_SENT_FOR_APPROVAL"})
    return m, msg("Протокол направлен на согласование", "Первый согласующий: %s" % _uname(db, cur, pr["route"][0]["user"]))

@reg("protocol.review")
def h_protocol_review(db, cur, u, p):
    """Результат согласования: «Согласовано» либо «Замечания» (возврат на доработку)."""
    m = get_req(db, cur, "meeting", p["id"]); guard(db, cur, u, "meetings", "view", m)
    pr = prot_of(m)
    if pr["status"] != "Направлен на согласование":
        raise RpcError("Протокол не на согласовании (статус «%s»)" % pr["status"], "Протокол")
    idx, step = _prot_step_pending(pr)
    if not step: raise RpcError("Все согласующие уже дали результат", "Протокол")
    if step["user"] != u["id"] and u["role"] != "admin":
        raise RpcError("Сейчас очередь согласующего: %s" % _uname(db, cur, step["user"]),
                       "403 · Не ваша очередь согласования", 403, "FORBIDDEN")
    decision = p.get("decision")
    if decision not in ("Согласовано", "Замечания"): raise RpcError("Укажите результат: Согласовано или Замечания")
    comment = (p.get("comment") or "")[:1000]
    if decision == "Замечания" and not comment.strip():
        raise RpcError("Замечания должны содержать текст", "Протокол")
    step.update(state=decision, ts=now_ms(), comment=comment)
    _prot_comment(pr, u, decision, comment)
    if decision == "Замечания":
        pr["status"] = "На доработке"
        for uid_ in dict.fromkeys([m.get("secretary"), pr.get("sentBy")]):
            if uid_ and uid_ != u["id"]:
                notify(db, cur, uid_, "protocol.rework", "Протокол возвращён на доработку: %s" % m["title"],
                       "%s · %s: %s" % (m["code"], u["name"], comment[:200]), "Совещание", m["id"],
                       dedupe="protocol.rework:%s:%s:%s" % (m["id"], uid_, now_ms()))
        msg_ = msg("Протокол возвращён на доработку", "Замечания направлены секретарю")
        ev = "MEETING_PROTOCOL_REWORK"
    else:
        nxt_idx, nxt = _prot_step_pending(pr)
        if nxt:
            _prot_notify_step(db, cur, m, pr, nxt)
            msg_ = msg("Согласовано", "Следующий согласующий: %s" % _uname(db, cur, nxt["user"]))
        else:
            pr["status"] = "Согласован"
            for uid_ in dict.fromkeys([m.get("chair"), m.get("secretary")]):
                if uid_ and uid_ != u["id"]:
                    notify(db, cur, uid_, "protocol.approved", "Протокол согласован: %s" % m["title"],
                           "%s · готов к утверждению" % m["code"], "Совещание", m["id"],
                           dedupe="protocol.agreed:%s:%s:%s" % (m["id"], uid_, pr["version"]))
            msg_ = msg("Протокол согласован", "Все согласующие дали положительный результат")
        ev = "MEETING_PROTOCOL_AGREED"
    m["protocol"] = pr
    db.save_ent(cur, "meeting", m)
    db.audit(cur, "BUSINESS", "info", u["id"], "meetings", "Согласование протокола: " + decision, "Совещание", m["id"],
             {"code": m["code"], "step": idx + 1, "comment": comment, "event": ev})
    return m, msg_

def _protocol_approve(db, cur, u, m, note=""):
    """Утверждение протокола: отдельная версия с файлами и снимком содержания (ТЗ §2.9)."""
    pr = prot_of(m)
    if pr["status"] not in ("Согласован", "Подготовка", "Черновик"):
        raise RpcError("Утвердить можно согласованный протокол (статус «%s»)" % pr["status"], "Протокол")
    if pr["route"] and pr["status"] != "Согласован":
        raise RpcError("Протокол должен пройти маршрут согласования", "Протокол")
    reg = _protocol_register(db, cur, u, m, ["docx", "pdf"],
                             "Утверждённый протокол %s · редакция %d" % (m["code"], pr["version"] + 1))
    pr["version"] += 1
    pr["versions"].append({"v": pr["version"], "ts": now_ms(), "by": u["id"], "note": (note or "")[:500],
                           "docId": reg["docId"], "docCode": reg["docCode"], "docV": reg["docV"], "files": reg["files"],
                           "agenda": json.loads(json.dumps(m.get("agenda") or [])),
                           "decisions": json.loads(json.dumps(m.get("decisions") or []))})
    pr["status"] = "Утверждён"
    pr["approvedAt"] = now_ms(); pr["approvedBy"] = u["id"]
    m["protocol"] = pr
    m["status"] = "Протокол утверждён"
    db.save_ent(cur, "meeting", m)
    notify_many(db, cur, [x for x in dict.fromkeys([m["chair"], m["secretary"]] + (m.get("attendees") or [])) if x != u["id"]],
                "protocol.approved", "Протокол утверждён: %s" % m["title"],
                "%s · редакция %d" % (m["code"], pr["version"]), "Совещание", m["id"],
                dedupe_suffix="approved:%d" % pr["version"])
    db.audit(cur, "AUDIT", "info", u["id"], "meetings", "Утверждён протокол совещания", "Совещание", m["id"],
             {"code": m["code"], "version": pr["version"], "doc": reg["docCode"], "decisions": len(m["decisions"]),
              "event": "MEETING_PROTOCOL_APPROVED"})
    LOG.info("Протокол %s утверждён (редакция %d, документ %s v%d)", m["code"], pr["version"], reg["docCode"], reg["docV"])
    return reg

@reg("protocol.approve")
def h_protocol_approve(db, cur, u, p):
    m = get_req(db, cur, "meeting", p["id"]); guard(db, cur, u, "meetings", "approve", m)
    if m["status"] not in ("Проведено", "Протокол утверждён"):
        raise RpcError("Утверждать можно протокол проведённого совещания", "Протокол")
    reg = _protocol_approve(db, cur, u, m, p.get("note", ""))
    pr = prot_of(m)
    return {"meeting": m, "version": pr["version"], "docId": reg["docId"], "files": reg["files"]}, msg(
        "Протокол утверждён (редакция %d)" % pr["version"], "Версия сохранена в M09, участники оповещены")

@reg("protocol.newVersion")
def h_protocol_new_version(db, cur, u, p):
    """Новая редакция утверждённого протокола: история предыдущих редакций сохраняется."""
    m = get_req(db, cur, "meeting", p["id"]); guard(db, cur, u, "meetings", "edit", m)
    pr = prot_of(m)
    if pr["status"] not in ("Утверждён", "Опубликован", "Архивный"):
        raise RpcError("Новая редакция создаётся из утверждённого протокола", "Протокол")
    pr["status"] = "Черновик"
    pr["sentAt"] = None; pr["sentBy"] = None; pr["approvedAt"] = None; pr["approvedBy"] = None; pr["publishedAt"] = None
    for s in pr["route"]: s.update(state="Ожидает", ts=None, comment="")
    _prot_comment(pr, u, "Новая редакция", p.get("note", ""))
    m["protocol"] = pr
    m["status"] = "Проведено"
    db.save_ent(cur, "meeting", m)
    db.audit(cur, "AUDIT", "warning", u["id"], "meetings", "Создана новая редакция протокола", "Совещание", m["id"],
             {"code": m["code"], "prevVersion": pr["version"], "event": "MEETING_PROTOCOL_VERSION_CREATED"})
    return m, msg("Создана новая редакция", "Утверждённая редакция %d сохранена в истории" % pr["version"])

@reg("protocol.publish")
def h_protocol_publish(db, cur, u, p):
    m = get_req(db, cur, "meeting", p["id"]); guard(db, cur, u, "meetings", "approve", m)
    pr = prot_of(m)
    if pr["status"] != "Утверждён": raise RpcError("Опубликовать можно утверждённый протокол", "Протокол")
    pr["status"] = "Опубликован"; pr["publishedAt"] = now_ms()
    m["protocol"] = pr
    db.save_ent(cur, "meeting", m)
    notify_many(db, cur, [x for x in dict.fromkeys([m["chair"], m["secretary"]] + (m.get("attendees") or [])) if x != u["id"]],
                "protocol.published", "Протокол опубликован: %s" % m["title"],
                "%s · редакция %d" % (m["code"], pr["version"]), "Совещание", m["id"],
                dedupe_suffix="published:%d" % pr["version"])
    db.audit(cur, "BUSINESS", "info", u["id"], "meetings", "Протокол опубликован", "Совещание", m["id"],
             {"code": m["code"], "version": pr["version"], "event": "MEETING_PROTOCOL_PUBLISHED"})
    return m, msg("Протокол опубликован", "Участники оповещены")

@reg("protocol.archive")
def h_protocol_archive(db, cur, u, p):
    m = get_req(db, cur, "meeting", p["id"]); guard(db, cur, u, "meetings", "approve", m)
    pr = prot_of(m)
    if pr["status"] not in ("Утверждён", "Опубликован"): raise RpcError("В архив переводится утверждённый протокол", "Протокол")
    pr["status"] = "Архивный"
    m["protocol"] = pr
    db.save_ent(cur, "meeting", m)
    db.audit(cur, "AUDIT", "info", u["id"], "meetings", "Протокол переведён в архив", "Совещание", m["id"],
             {"code": m["code"], "version": pr["version"], "event": "MEETING_PROTOCOL_ARCHIVED"})
    return m, msg("Протокол в архиве", "История редакций сохранена")

# ---------------- Центр уведомлений и напоминаний (ТЗ M12 §8, M16 §2.11.1) ----------------
# Единый контур доставки: уведомления хранятся на сервере (кому, тип, дата, канал, статус,
# ошибка, дата прочтения), повторная доставка одного и того же события исключается ключом
# dedupe. Напоминания — настраиваемые интервалы для событий календаря и совещаний.

NOTIF_TYPES = {
    "task.assigned":      ("Назначена задача", "tasks"),
    "task.due":           ("Приближается срок задачи", "tasks"),
    "task.overdue":       ("Задача просрочена", "tasks"),
    "directive.assigned": ("Назначено поручение совещания", "meetings"),
    "directive.due":      ("Приближается срок поручения", "meetings"),
    "directive.overdue":  ("Поручение просрочено", "meetings"),
    "directive.stale":    ("Поручение без обновления состояния", "meetings"),
    "directive.noassignee": ("Поручение без исполнителя", "meetings"),
    "directive.nodue":    ("Поручение без установленного срока", "meetings"),
    "directive.review":   ("Поручение требует проверки результата", "meetings"),
    "directive.extended": ("Срок поручения продлён", "meetings"),
    "directive.accepted": ("Результат поручения принят", "meetings"),
    "directive.rework":   ("Поручение возвращено на доработку", "meetings"),
    "meeting.reminder":   ("Напоминание о совещании", "calendar"),
    "event.reminder":     ("Напоминание о событии календаря", "calendar"),
    "meeting.invite":     ("Приглашение на совещание", "meetings"),
    "meeting.moved":      ("Перенос совещания", "meetings"),
    "meeting.cancelled":  ("Отмена совещания", "meetings"),
    "meeting.materials":  ("Материалы к совещанию", "meetings"),
    "protocol.review":    ("Протокол направлен на согласование", "meetings"),
    "protocol.approved":  ("Протокол утверждён", "meetings"),
    "protocol.rework":    ("Протокол возвращён на доработку", "meetings"),
    "protocol.published": ("Протокол опубликован", "meetings"),
    "time.approval":      ("Трудозатраты ожидают утверждения", "time"),
    "time.result":        ("Трудозатраты рассмотрены", "time"),
    "doc.approval":       ("Документ на согласовании", "docs"),
    "doc.approved":       ("Документ утверждён", "docs"),
    "calc.verify":        ("Расчёт ожидает проверки", "calcs"),
    "risk.review":        ("Наступил срок пересмотра риска", "risks"),
}
# типовые интервалы напоминаний (ТЗ M12 §8.2), минуты до события
REMINDER_OFFSETS = [(0, "В момент начала"), (60, "За 1 час"), (1440, "За 1 день"),
                    (4320, "За 3 дня"), (10080, "За 7 дней"), (20160, "За 14 дней")]
DEFAULT_REMINDERS = [1440, 60]
NOTIF_CHANNEL = "web"          # автономная ЛВС: внутренний центр уведомлений (ТЗ §8.3)
NOTIF_KEEP_DAYS = 90

def notif_prefs(db, cur, user_id):
    s = db.settings(cur) or {}
    return (s.get("notifPrefs") or {}).get(user_id, {})

def notify(db, cur, user_id, type_, title, body="", obj="", obj_id="", dedupe=None, ts=None):
    """Доставка уведомления во внутренний центр (идемпотентно по ключу dedupe)."""
    if not user_id or user_id == "system": return None
    if type_ not in NOTIF_TYPES: return None
    prefs = notif_prefs(db, cur, user_id)
    if prefs.get(type_) is False: return None
    module = NOTIF_TYPES[type_][1]
    key = dedupe or "%s:%s:%s:%s" % (type_, user_id, obj_id or title, dISO(today()))
    nid = uid("n")
    cur.execute(
        "INSERT INTO notifications(id,ts,user_id,type,module,obj,obj_id,title,body,channel,status,error,read_at,dedupe) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'sent','',NULL,%s) ON CONFLICT (dedupe) DO NOTHING",
        (nid, ts or now_ms(), user_id, type_, module, obj, obj_id or "", title[:300], body[:1000], NOTIF_CHANNEL, key))
    return nid if cur.rowcount else None

def notify_many(db, cur, user_ids, type_, title, body="", obj="", obj_id="", dedupe_suffix=""):
    n = 0
    for uid_ in dict.fromkeys([x for x in user_ids if x]):
        if notify(db, cur, uid_, type_, title, body, obj, obj_id,
                  dedupe=("%s:%s:%s:%s" % (type_, uid_, obj_id, dedupe_suffix or dISO(today())))): n += 1
    return n

def event_dt(date_s, time_s):
    """Дата/время события → миллисекунды (локальное время сервера)."""
    try: d = parseD(date_s)
    except Exception: return None
    hh, mm = 9, 0
    if time_s and re.match(r"^\d{1,2}:\d{2}$", str(time_s)):
        hh, mm = [int(x) for x in str(time_s).split(":")]
    return int(time.mktime(datetime.datetime(d.year, d.month, d.day, min(hh, 23), min(mm, 59)).timetuple())) * 1000

def reminders_set(db, cur, owner_type, obj, user_ids, offsets, by_user):
    """Замена набора напоминаний события (ТЗ M12 §8.2: несколько напоминаний на событие)."""
    base = event_dt(obj.get("date"), obj.get("time"))
    if base is None: raise RpcError("У события не задана дата")
    offs = sorted({int(o) for o in offsets if int(o) in [x[0] for x in REMINDER_OFFSETS] or 0 <= int(o) <= 60 * 24 * 60})
    cur.execute("DELETE FROM reminders WHERE owner_type=%s AND owner_id=%s", (owner_type, obj["id"]))
    made = 0
    for uid_ in dict.fromkeys([x for x in user_ids if x]):
        for off in offs:
            key = "%s:%s:%s:%d" % (owner_type, obj["id"], uid_, off)
            cur.execute("INSERT INTO reminders(id,owner_type,owner_id,user_id,offset_min,fire_at,sent_at,key,created_by,created_at) "
                        "VALUES(%s,%s,%s,%s,%s,%s,NULL,%s,%s,%s) ON CONFLICT (key) DO NOTHING",
                        (uid("rm"), owner_type, obj["id"], uid_, off, base - off * 60_000, key, by_user, now_ms()))
            made += 1
    db.audit(cur, "AUDIT", "info", by_user, "calendar", "Настроены напоминания события",
             "Событие" if owner_type == "calEvent" else "Совещание", obj["id"],
             {"offsets": offs, "users": len(set(user_ids)), "count": made, "event": "calendar.reminder.created"})
    return made

def reminders_of(db, cur, owner_type, owner_id):
    cur.execute("SELECT id,owner_type,owner_id,user_id,offset_min,fire_at,sent_at FROM reminders "
                "WHERE owner_type=%s AND owner_id=%s ORDER BY offset_min DESC", (owner_type, owner_id))
    return [dict(r) for r in cur.fetchall()]

def notif_list(db, cur, user_id, limit=100, unread_only=False):
    q = "SELECT id,ts,type,module,obj,obj_id,title,body,channel,status,error,read_at FROM notifications WHERE user_id=%s"
    if unread_only: q += " AND read_at IS NULL"
    q += " ORDER BY ts DESC LIMIT %s"
    cur.execute(q, (user_id, int(limit)))
    rows = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT COUNT(*) AS n FROM notifications WHERE user_id=%s AND read_at IS NULL", (user_id,))
    return rows, cur.fetchone()["n"]

@reg("notif.list")
def h_notif_list(db, cur, u, p):
    items, unread = notif_list(db, cur, u["id"], p.get("limit", 100), bool(p.get("unread")))
    return {"items": items, "unread": unread, "prefs": notif_prefs(db, cur, u["id"]),
            "types": [{"key": k, "name": v[0], "module": v[1]} for k, v in NOTIF_TYPES.items()]}, None

@reg("notif.read")
def h_notif_read(db, cur, u, p):
    if p.get("all"):
        cur.execute("UPDATE notifications SET read_at=%s WHERE user_id=%s AND read_at IS NULL", (now_ms(), u["id"]))
    else:
        ids = [str(x) for x in (p.get("ids") or [])][:200]
        if not ids: raise RpcError("Не указаны уведомления")
        cur.execute("UPDATE notifications SET read_at=%s WHERE user_id=%s AND id = ANY(%s) AND read_at IS NULL",
                    (now_ms(), u["id"], ids))
    n = cur.rowcount
    items, unread = notif_list(db, cur, u["id"])
    return {"read": n, "unread": unread}, msg("Отмечено прочитанным: %d" % n) if n else None

@reg("notif.prefs")
def h_notif_prefs(db, cur, u, p):
    s = db.settings(cur) or {}
    prefs = s.setdefault("notifPrefs", {}).setdefault(u["id"], {})
    for k, v in (p.get("prefs") or {}).items():
        if k in NOTIF_TYPES: prefs[k] = bool(v)
    db.save_settings(cur, s)
    db.audit(cur, "AUDIT", "info", u["id"], "admin", "Изменены настройки уведомлений", "Пользователь", u["id"],
             {"off": [k for k, v in prefs.items() if v is False]})
    return prefs, msg("Настройки уведомлений сохранены", "Отключённые типы не доставляются")

@reg("reminder.set")
def h_reminder_set(db, cur, u, p):
    ot = p.get("ownerType")
    if ot not in ("meeting", "calEvent"): raise RpcError("Напоминания доступны для совещаний и событий календаря")
    obj = get_req(db, cur, "meeting" if ot == "meeting" else "calEvent", p.get("ownerId"))
    guard(db, cur, u, "meetings" if ot == "meeting" else "calendar", "edit", obj)
    if ot == "meeting":
        users = list(dict.fromkeys(([obj.get("chair"), obj.get("secretary")] + (obj.get("attendees") or []))))
    else:
        users = obj.get("participants") or [u["id"]]
    users = p.get("users") or users
    n = reminders_set(db, cur, ot, obj, users, p.get("offsets", DEFAULT_REMINDERS), u["id"])
    return {"count": n, "reminders": reminders_of(db, cur, ot, obj["id"])}, msg(
        "Напоминания настроены (%d)" % n, "Доставка — во внутренний центр уведомлений")

@reg("reminder.list")
def h_reminder_list(db, cur, u, p):
    ot = p.get("ownerType", "meeting")
    if ot not in ("meeting", "calEvent"): raise RpcError("Неизвестный тип события")
    get_req(db, cur, "meeting" if ot == "meeting" else "calEvent", p.get("ownerId"))
    return reminders_of(db, cur, ot, p["ownerId"]), None

@reg("reminder.remove")
def h_reminder_remove(db, cur, u, p):
    cur.execute("SELECT owner_type, owner_id FROM reminders WHERE id=%s", (p.get("id"),))
    r = cur.fetchone()
    if not r: raise RpcError("Напоминание не найдено")
    obj = get_req(db, cur, "meeting" if r["owner_type"] == "meeting" else "calEvent", r["owner_id"])
    guard(db, cur, u, "meetings" if r["owner_type"] == "meeting" else "calendar", "edit", obj)
    cur.execute("DELETE FROM reminders WHERE id=%s", (p["id"],))
    db.audit(cur, "AUDIT", "info", u["id"], "calendar", "Напоминание удалено", "Событие", r["owner_id"], {})
    return True, msg("Напоминание удалено")

@reg("notif.tick")
def h_notif_tick(db, cur, u, p):
    """Немедленный цикл планировщика (M14 «Диагностика», приёмочные испытания)."""
    guard(db, cur, u, "admin", "edit")
    n = notification_cycle_cur(db, cur)
    db.audit(cur, "TECH", "info", u["id"], "admin", "Внеочередной цикл планировщика уведомлений", "Система", "", {"delivered": n})
    return {"delivered": n}, msg("Планировщик отработал", "Доставлено уведомлений: %d" % n)

def _approvers(db, cur, module):
    """Кому адресовать уведомления о необходимости утверждения (по матрице прав)."""
    s = db.settings(cur) or {}
    perms = s.get("perms") or BASE_PERMS
    out = []
    for usr in db.users_public(cur):
        if usr["blocked"]: continue
        if "approve" in (perms.get(usr["role"], {}).get(module) or []): out.append(usr["id"])
    return out

def notification_cycle_cur(db, cur):
    """Тело цикла планировщика на переданном курсоре (используется и по расписанию, и из M14)."""
    delivered = 0
    now = now_ms()
    # 1. Напоминания событий и совещаний
    cur.execute("SELECT * FROM reminders WHERE sent_at IS NULL AND fire_at<=%s ORDER BY fire_at LIMIT 200", (now,))
    for r in cur.fetchall():
        if r["owner_type"] == "meeting":
            cur.execute("SELECT id,code,title,date,mtime,status FROM meetings WHERE id=%s", (r["owner_id"],))
            m = cur.fetchone()
            if not m or m["status"] in ("Отменено", "Протокол утверждён"):
                cur.execute("UPDATE reminders SET sent_at=%s WHERE id=%s", (now, r["id"])); continue
            title = "Совещание %s %s: %s" % (_fd(m["date"]), m["mtime"] or "", m["title"])
            body = "%s · напоминание %s" % (m["code"], dict(REMINDER_OFFSETS).get(r["offset_min"], "за %d мин" % r["offset_min"]))
            type_ = "meeting.reminder"; obj = "Совещание"
        else:
            cur.execute("SELECT id,title,date,mtime FROM cal_events WHERE id=%s", (r["owner_id"],))
            e = cur.fetchone()
            if not e:
                cur.execute("UPDATE reminders SET sent_at=%s WHERE id=%s", (now, r["id"])); continue
            title = "Событие %s %s: %s" % (_fd(e["date"]), e["mtime"] or "", e["title"])
            body = "Напоминание %s" % dict(REMINDER_OFFSETS).get(r["offset_min"], "за %d мин" % r["offset_min"])
            type_ = "event.reminder"; obj = "Событие"
        got = notify(db, cur, r["user_id"], type_, title, body, obj, r["owner_id"], dedupe=r["key"])
        cur.execute("UPDATE reminders SET sent_at=%s WHERE id=%s", (now, r["id"]))
        if got:
            delivered += 1
            db.audit(cur, "BUSINESS", "info", "system", "calendar", "Доставлено напоминание",
                     obj, r["owner_id"], {"user": r["user_id"], "offset": r["offset_min"],
                                          "event": "calendar.reminder.sent"})
    # 2. Сроки обычных задач M02 (поручения совещаний проверяются отдельным контуром §2.11.1)
    today_s = dISO(today()); tomorrow = D(1)
    cur.execute("SELECT id,code,title,status,assignee,\"end\",type,meeting_id,created_at FROM tasks "
                "WHERE status NOT IN ('Завершена','Отменена','Проверена') AND assignee IS NOT NULL "
                "AND COALESCE(type,'task')<>'directive' AND COALESCE(\"end\",'')<>''")
    for t in cur.fetchall():
        if t["end"] < today_s:
            days = diffD(today_s, t["end"])
            if notify(db, cur, t["assignee"], "task.overdue", "Просрочено: %s" % t["title"],
                      "%s · просрочка %d дн" % (t["code"], days), "Задача", t["id"]):
                delivered += 1
        elif t["end"] <= tomorrow:
            if notify(db, cur, t["assignee"], "task.due", "Срок истекает: %s" % t["title"],
                      "%s · срок %s" % (t["code"], _fd(t["end"])), "Задача", t["id"]):
                delivered += 1
    # 3. Контроль сроков поручений: шесть проверок и настраиваемый состав получателей (ТЗ §2.11.1)
    delivered += directive_control_cycle(db, cur)["delivered"]
    # 3а. Регулярные совещания: ближайшие даты серии — отдельными карточками (ТЗ §2.5)
    meeting_series_cycle(db, cur)
    # 4. Пересмотр рисков
    cur.execute("SELECT id,code,title,owner FROM risks WHERE status<>'Закрыт' AND review<=%s AND owner IS NOT NULL", (today_s,))
    for r in cur.fetchall():
        notify(db, cur, r["owner"], "risk.review", "Пересмотр риска: %s" % r["title"],
               "%s · срок пересмотра %s" % (r["code"], _fd(r["review"])), "Риск", r["id"])
    # 5. Очереди утверждения (ежедневная сводка, без повторов)
    cur.execute("SELECT COUNT(*) AS n FROM time_entries WHERE status='Отправлена'")
    n = cur.fetchone()["n"]
    if n:
        notify_many(db, cur, _approvers(db, cur, "time"), "time.approval",
                    "Трудозатраты ожидают утверждения: %d" % n, "Модуль M06 · Учёт трудозатрат", "Очередь", "time")
    cur.execute("SELECT COUNT(*) AS n FROM docs WHERE status='На согласовании'")
    n = cur.fetchone()["n"]
    if n:
        notify_many(db, cur, _approvers(db, cur, "docs"), "doc.approval",
                    "Документы на согласовании: %d" % n, "Модуль M09 · Документы", "Очередь", "docs")
    cur.execute("SELECT COUNT(*) AS n FROM calcs WHERE status='Выполнен'")
    n = cur.fetchone()["n"]
    if n:
        notify_many(db, cur, _approvers(db, cur, "calcs"), "calc.verify",
                    "Расчёты ожидают проверки: %d" % n, "Модуль M10 · Инженерные расчёты", "Очередь", "calcs")
    # 6. Хранение: очистка старых прочитанных уведомлений
    cur.execute("DELETE FROM notifications WHERE read_at IS NOT NULL AND ts < %s", (now - NOTIF_KEEP_DAYS * DAY * 1000,))
    return delivered

def notification_cycle(db):
    """Один цикл планировщика: доставка напоминаний и контроль сроков (ТЗ M12 §8.4, M16 §2.11.1)."""
    delivered = 0
    with db.wlock:
        conn = db.get()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                delivered = notification_cycle_cur(db, cur)
                if delivered: db.bump(cur)
                conn.commit()
        except Exception as e:
            conn.rollback()
            LOG.error("Планировщик уведомлений: сбой цикла: %s\n%s", e, traceback.format_exc())
            try:
                with conn.cursor() as cur2:
                    db.audit(cur2, "TECH", "error", "system", "calendar", "Ошибка планировщика уведомлений: %s" % e,
                             "Система", "", {"event": "calendar.reminder.failed"})
                conn.commit()
            except Exception: pass
        finally:
            db.put(conn)
    if delivered: LOG.info("Планировщик уведомлений: доставлено %d", delivered)
    return delivered

def notification_worker(db, period=30):
    """Фоновый планировщик уведомлений (автономно, без внешних служб)."""
    LOG.info("Планировщик уведомлений запущен (интервал %d с)", period)
    while True:
        try: notification_cycle(db)
        except Exception as e: LOG.error("Планировщик: необработанная ошибка: %s", e)
        time.sleep(period)

# ---------------- Диспетчер RPC ----------------
def dispatch(db, user, action, payload):
    h = HANDLERS.get(action)
    if not h:
        LOG.warning("RPC %s: неизвестная операция (пользователь %s)", action, user.get("login"))
        raise RpcError("Неизвестная операция: %s" % action, "Неизвестная операция", 404, "UNKNOWN_ACTION")
    t0 = time.time()
    with db.wlock:
        conn = db.get()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                try:
                    result, m = h(db, cur, user, payload or {})
                    db.bump(cur)
                    conn.commit()
                    state = db.state(cur, user["id"])
                    LOG.info("RPC %s · %s · OK · %.0f мс", action, user.get("login"), (time.time() - t0) * 1000)
                    return {"ok": True, "result": result, "msg": m, "state": state}
                except PermError as e:
                    conn.rollback()
                    LOG.warning("RPC %s · %s · ОТКАЗ ПРАВ (%s/%s)", action, user.get("login"), e.mod, e.op)
                    db.audit(cur, "SECURITY", "warning", user["id"], e.mod, "Отказано в операции (недостаточно прав)",
                             "Операция", action, {"op": e.op, "role": user["role"], "action": action})
                    conn.commit()
                    state = db.state(cur, user["id"])
                    return {"ok": False, "error": {"code": e.code, "title": e.title, "message": str(e)}, "state": state, "status": 403}
                except RpcError as e:
                    conn.rollback()
                    LOG.warning("RPC %s · %s · ошибка %s: %s", action, user.get("login"), e.code, e)
                    state = db.state(cur, user["id"])
                    return {"ok": False, "error": {"code": e.code, "title": e.title, "message": str(e)}, "state": state, "status": e.status}
                except Exception as e:
                    conn.rollback()
                    LOG.error("RPC %s · %s · ВНУТРЕННЯЯ ОШИБКА: %s\n%s", action, user.get("login"), e, traceback.format_exc())
                    db.audit(cur, "TECH", "error", user["id"], "dash", "Ошибка операции %s: %s" % (action, e), "Операция", action, {})
                    conn.commit()
                    state = db.state(cur, user["id"])
                    return {"ok": False, "error": {"code": "INTERNAL", "title": "Внутренняя ошибка", "message": str(e)}, "state": state, "status": 500}
        finally:
            db.put(conn)

# ---------------- Сессии и аутентификация ----------------
DATA_DIR = os.path.join(BASE_DIR, "data")
FILES_DIR = os.path.join(DATA_DIR, "files")
COOKIE = "supip_sid"
TLS_ON = False

# ---------------- Файловые журналы (ТЗ §15: контроль, разбор ошибок и противоречий) ----------------
# Журнал не зависит от БД: он пишется и тогда, когда PostgreSQL недоступен, — поэтому причины
# сбоя установки/запуска видны даже при отказе СУБД. Файлы: supip.log (работа), setup.log
# (мастер установки), installer.log (журнал NSIS-установщика, копируется им же).
LOG_DIR = os.path.join(DATA_DIR, "logs")
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_KEEP = 5
LOG = logging.getLogger("supip")
SETUP_LOG = logging.getLogger("supip.setup")
LOG_STATE = {"ready": False, "dir": LOG_DIR, "level": "INFO"}
LOG_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}\.log(\.[0-9]{1,2})?$")

def log_init(level=None, log_dir=None, max_mb=None, keep=None):
    """Инициализация файловых журналов с ротацией. Вызывается при любом режиме запуска."""
    global LOG_DIR
    LOG_DIR = os.path.abspath(log_dir or LOG_DIR)
    level = (level or LOG_STATE["level"] or "INFO").upper()
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"): level = "INFO"
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
    except Exception as e:
        sys.stderr.write("Не удалось создать каталог журналов %s: %s\n" % (LOG_DIR, e))
        return None
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    for logger, fname in ((LOG, "supip.log"), (SETUP_LOG, "setup.log")):
        for h in list(logger.handlers):
            logger.removeHandler(h); h.close()
        logger.setLevel(level); logger.propagate = False
        try:
            fh = logging.handlers.RotatingFileHandler(os.path.join(LOG_DIR, fname),
                                                      maxBytes=int((max_mb or LOG_MAX_BYTES / 1048576) * 1048576),
                                                      backupCount=int(keep or LOG_KEEP), encoding="utf-8")
            fh.setFormatter(fmt); logger.addHandler(fh)
        except Exception as e:
            sys.stderr.write("Не удалось открыть журнал %s: %s\n" % (fname, e))
        ch = logging.StreamHandler(sys.stderr)      # в консоль — только предупреждения и ошибки
        ch.setLevel(logging.WARNING); ch.setFormatter(fmt); logger.addHandler(ch)
    LOG_STATE.update(ready=True, dir=LOG_DIR, level=level)
    return os.path.join(LOG_DIR, "supip.log")

def log_mask(dsn):
    """Пароли в журнал не пишутся."""
    return re.sub(r"(password=)\S+", r"\1***", dsn or "")

def log_files():
    """Список доступных журналов (только каталог журналов, без обхода путей)."""
    out = []
    try:
        for n in sorted(os.listdir(LOG_DIR)):
            if not LOG_NAME_RE.match(n): continue
            p = os.path.join(LOG_DIR, n)
            if not os.path.isfile(p): continue
            st = os.stat(p)
            out.append({"name": n, "size": st.st_size, "mtime": int(st.st_mtime * 1000)})
    except Exception:
        pass
    return out

def log_tail(name, lines=300):
    """Последние строки журнала (защита от обхода каталога и от чтения произвольных файлов)."""
    if not LOG_NAME_RE.match(name or ""): raise RpcError("Недопустимое имя журнала", "Журналы", 400, "BAD_LOG_NAME")
    p = os.path.join(LOG_DIR, name)
    if not os.path.isfile(p): raise RpcError("Журнал не найден: %s" % name, "Журналы", 404, "NO_LOG")
    n = max(1, min(int(lines or 300), 5000))
    with open(p, "rb") as f:
        f.seek(0, os.SEEK_END); size = f.tell()
        block = min(size, 512 * 1024)
        f.seek(size - block)
        data = f.read(block)
    text = data.decode("utf-8", "replace")
    return text.splitlines()[-n:]

class ReportList(list):
    """Отчёт мастера установки: каждая строка сразу пишется в setup.log."""
    def append(self, s):
        super().append(s)
        SETUP_LOG.info("%s", s)

def session_user(db, token):
    """Возвращает публичную запись пользователя по токену сессии; продлевает idle-таймер."""
    if not token: return None
    conn = db.get()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT s.user_id, s.last_seen, u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=%s", (token,))
            r = cur.fetchone()
            if not r: conn.commit(); return None
            idle_min = (db.settings(cur) or {}).get("sessionMin", 60)
            if now_ms() - r["last_seen"] > idle_min * 60_000 or r["blocked"]:
                cur.execute("DELETE FROM sessions WHERE token=%s", (token,)); conn.commit(); return None
            cur.execute("UPDATE sessions SET last_seen=%s WHERE token=%s", (now_ms(), token)); conn.commit()
            return {k: r[k] for k in ("id","login","name","role","position","capacity","color","blocked","absences")}
    finally:
        db.put(conn)

def do_login(db, login, password, ip):
    login = (login or "").strip().lower(); password = password or ""
    with db.wlock:
        conn = db.get()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM users WHERE login=%s", (login,))
                u = cur.fetchone()
                if not u:
                    db.audit(cur, "SECURITY", "warning", "system", "admin", "Неуспешный вход: неизвестный логин", "Сессия", "", {"login": login, "ip": ip})
                    conn.commit()
                    LOG.warning("ВХОД отклонён: неизвестный логин '%s' с %s", login, ip)
                    raise RpcError("Неверный логин или пароль", "Вход отклонён", 401, "BAD_CREDENTIALS")
                if u["blocked"]:
                    db.audit(cur, "SECURITY", "warning", u["id"], "admin", "Вход отклонён: учётная запись заблокирована", "Сессия", u["id"], {"ip": ip})
                    conn.commit(); raise RpcError("Учётная запись заблокирована администратором", "Вход отклонён", 403, "BLOCKED")
                if u["lock_until"] > now_ms():
                    secs = math.ceil((u["lock_until"] - now_ms()) / 1000)
                    conn.commit(); raise RpcError("Слишком много неудачных попыток. Повторите через %d с." % secs, "Временная блокировка", 429, "LOCKED")
                if not pass_verify(password, u["pass_salt"], u["pass_hash"]):
                    failed = u["failed"] + 1; lock = 0
                    if failed >= 3: lock = now_ms() + 60_000; failed = 0
                    cur.execute("UPDATE users SET failed=%s, lock_until=%s WHERE id=%s", (failed, lock, u["id"]))
                    db.audit(cur, "SECURITY", "warning", u["id"], "admin",
                             "Неуспешный вход: неверный пароль" + (" · блокировка 60 с" if lock else ""), "Сессия", u["id"], {"ip": ip, "attempt": failed or 3})
                    conn.commit()
                    LOG.warning("ВХОД отклонён: неверный пароль '%s' с %s%s", login, ip, " · блокировка 60 с" if lock else "")
                    raise RpcError("Неверный логин или пароль" + (" · вход заблокирован на 60 с" if lock else " · осталось попыток: %d" % (3 - failed)),
                                   "Вход отклонён", 401, "BAD_CREDENTIALS")
                s = db.settings(cur) or {}
                idle = s.get("sessionMin", 60) * 60_000
                cur.execute("DELETE FROM sessions WHERE last_seen < %s", (now_ms() - idle,))
                cur.execute("SELECT DISTINCT user_id FROM sessions")
                active = {r["user_id"] for r in cur.fetchall()}
                if u["id"] not in active and len(active) >= MAX_USERS_ONLINE:
                    conn.commit(); raise RpcError("Достигнут лимит %d одновременных пользователей (ТЗ §2). Попробуйте позже." % MAX_USERS_ONLINE,
                                                  "Лимит сессий", 429, "SESSION_LIMIT")
                token = secrets.token_hex(24)
                cur.execute("INSERT INTO sessions(token,user_id,created,last_seen) VALUES(%s,%s,%s,%s)", (token, u["id"], now_ms(), now_ms()))
                cur.execute("UPDATE users SET failed=0, lock_until=0 WHERE id=%s", (u["id"],))
                db.audit(cur, "SECURITY", "info", u["id"], "admin", "Вход в систему", "Сессия", u["id"], {"ip": ip, "role": u["role"]})
                LOG.info("ВХОД: %s (%s) с %s", u["login"], u["role"], ip)
                db.bump(cur); conn.commit()
                state = db.state(cur, u["id"])
                return token, state
        finally:
            db.put(conn)

def do_logout(db, token, user):
    if not token: return
    with db.wlock:
        conn = db.get()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("DELETE FROM sessions WHERE token=%s", (token,))
                if user: db.audit(cur, "SECURITY", "info", user["id"], "admin", "Выход из системы", "Сессия", user["id"], {})
                db.bump(cur); conn.commit()
        finally:
            db.put(conn)

# ---------------- Файлы документов (M09) ----------------
def file_path(doc_id, v):
    safe = re.sub(r"[^A-Za-z0-9_\-]", "", doc_id)
    return os.path.join(FILES_DIR, "%s_v%d.bin" % (safe, int(v)))

def store_file(db, user, doc_id, v, name, body):
    with db.wlock:
        conn = db.get()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                d = db.get_ent(cur, "doc", doc_id)
                if not d: raise RpcError("Документ не найден", "Файл", 404, "NOT_FOUND")
                guard(db, cur, user, "docs", "edit", d)
                if not any(x["v"] == v for x in d["versions"]): raise RpcError("Версия документа не найдена", "Файл", 404, "NOT_FOUND")
                if len(body) > MAX_FILE: raise RpcError("Файл больше 25 МБ", "Файл", 413, "FILE_TOO_BIG")
                with open(file_path(doc_id, v), "wb") as f: f.write(body)
                cur.execute("UPDATE doc_versions SET stored=TRUE, size=%s, file_name=%s WHERE doc_id=%s AND v=%s",
                            (len(body), (name or "file.bin")[:200], doc_id, v))
                db.audit(cur, "BUSINESS", "info", user["id"], "docs", "Загружен файл версии документа", "Документ", doc_id,
                         {"code": d["code"], "v": v, "name": name, "size": len(body)})
                db.bump(cur); conn.commit()
                return db.state(cur, user["id"])
        except Exception:
            conn.rollback(); raise
        finally:
            db.put(conn)

def read_file_bytes(db, cur, user, doc_id, v, fmt=None):
    d = db.get_ent(cur, "doc", doc_id)
    if not d: raise RpcError("Документ не найден", "Файл", 404, "NOT_FOUND")
    guard(db, cur, user, "docs", "view", d)
    ver = next((x for x in d["versions"] if x["v"] == v), None)
    if not ver: raise RpcError("Версия не найдена", "Файл", 404, "NOT_FOUND")
    p = file_path(doc_id, v)
    if fmt and fmt in ("docx", "pdf", "xlsx", "html"):
        alt = p + "." + fmt
        if ver["stored"] and os.path.isfile(alt):
            with open(alt, "rb") as f:
                return f.read(), re.sub(r"\.[A-Za-z0-9]+$", "", ver["name"]) + "." + fmt, False
    if ver["stored"] and os.path.isfile(p):
        with open(p, "rb") as f: return f.read(), ver["name"], False
    demo = ("%s · демонстрационный файл\n\nДокумент: %s — %s\nВерсия: %d от %s\nСтатус: %s\n\n"
            "Это эталонный демо-файл из начального набора данных.\nРеальные файлы загружаются через карточку документа (M09) "
            "и хранятся на сервере в каталоге data/files.\n" % (APP_NAME, d["code"], d["title"], v,
            datetime.datetime.fromtimestamp(ver["ts"]/1000).strftime("%d.%m.%Y %H:%M"), d["status"])).encode("utf-8")
    return demo, re.sub(r"\.[A-Za-z0-9]+$", "", ver["name"]) + "_demo.txt", True

# ---------------- Резервное копирование (M14) ----------------
BACKUP_TABLES = ["users","projects","meetings","risks","tasks","docs","doc_versions","calcs","time_entries","cal_events","reports","events","settings",
                 "notifications","reminders","meeting_tasks"]
# копии прежних версий (13 таблиц) восстанавливаются: новые таблицы считаются пустыми
BACKUP_REQUIRED = ["users","projects","meetings","risks","tasks","docs","doc_versions","calcs","time_entries","cal_events","reports","events","settings"]

def make_backup(db, user):
    with db.wlock:
        conn = db.get()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                guard(db, cur, user, "admin", "export")
                data = {"app": APP_NAME, "version": VERSION, "ts": now_ms(), "rev": db.rev(cur), "tables": {}}
                for t in BACKUP_TABLES:
                    cur.execute("SELECT * FROM " + t)
                    data["tables"][t] = [dict(r) for r in cur.fetchall()]
                LOG.info("Резервная копия создана пользователем %s (%d таблиц)", user.get("login"), len(BACKUP_TABLES))
                db.audit(cur, "TECH", "info", user["id"], "admin", "Создана резервная копия БД (JSON)", "БД", "",
                         {"rows": {t: len(data["tables"][t]) for t in BACKUP_TABLES}})
                db.bump(cur); conn.commit()
                return data
        finally:
            db.put(conn)

def restore_backup(db, user, data, token=None):
    if not isinstance(data, dict) or "tables" not in data: raise RpcError("Некорректный файл резервной копии", "Восстановление")
    tables = data["tables"]
    if any(t not in tables or not isinstance(tables[t], list) for t in BACKUP_REQUIRED):
        raise RpcError("В копии отсутствуют обязательные таблицы", "Восстановление")
    for t in BACKUP_TABLES:
        tables.setdefault(t, [])
    with db.wlock:
        conn = db.get()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                guard(db, cur, user, "admin", "delete")
                for t in ["sessions"] + BACKUP_TABLES[::-1]:
                    cur.execute("DELETE FROM " + t)
                def ins(t, row):
                    row = {k: (psycopg2.extras.Json(v) if isinstance(v, (dict, list)) else v) for k, v in row.items()}
                    cols = ", ".join('"%s"' % k for k in row)
                    ph = ", ".join(["%s"] * len(row))
                    cur.execute("INSERT INTO %s(%s) VALUES(%s)" % (t, cols, ph), list(row.values()))
                for t in ["users","projects","meetings","risks"]:
                    for row in tables[t]: ins(t, row)
                pending = list(tables["tasks"]); guard_n = 0
                while pending and guard_n < 1000:
                    rest = []
                    ids_done = {r["id"] for r in tables["tasks"]} - {r["id"] for r in pending}
                    for row in pending:
                        par = row.get("parent")
                        if not par or par in ids_done: ins("tasks", row)
                        else: rest.append(row)
                    if len(rest) == len(pending): raise RpcError("Циклическая иерархия задач в копии", "Восстановление")
                    pending = rest; guard_n += 1
                for t in ["meeting_tasks","docs","doc_versions","calcs","time_entries","cal_events","reports","events","settings","reminders","notifications"]:
                    for row in tables[t]: ins(t, row)
                cur.execute("UPDATE meta SET v=(v::bigint+1)::text WHERE k='rev'")
                LOG.warning("БД ВОССТАНОВЛЕНА из копии пользователем %s (копия от %s)", user.get("login"), data.get("ts"))
                db.audit(cur, "SECURITY", "warning", user["id"], "admin", "БД восстановлена из резервной копии", "БД", "",
                         {"backupTs": data.get("ts"), "rows": {t: len(tables[t]) for t in BACKUP_TABLES}})
                conn.commit()
                cur.execute("SELECT 1 FROM users WHERE id=%s AND NOT blocked", (user["id"],))
                alive = cur.fetchone() is not None
                if alive and token:
                    cur.execute("INSERT INTO sessions(token,user_id,created,last_seen) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                                (token, user["id"], now_ms(), now_ms()))
                    conn.commit()
                return {"relogin": not alive}
        except RpcError as e:
            conn.rollback()
            LOG.warning("Восстановление БД отклонено: %s", e)
            raise
        except Exception as e:
            conn.rollback()
            LOG.error("Восстановление БД не выполнено: %s\n%s", e, traceback.format_exc())
            raise
        finally:
            db.put(conn)

# ---------------- HTTP-слой ----------------
MIME = {".html":"text/html; charset=utf-8", ".js":"text/javascript; charset=utf-8", ".css":"text/css; charset=utf-8",
        ".svg":"image/svg+xml", ".png":"image/png", ".jpg":"image/jpeg", ".ico":"image/x-icon",
        ".woff2":"font/woff2", ".json":"application/json; charset=utf-8", ".txt":"text/plain; charset=utf-8",
        ".docx":"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pdf":"application/pdf", ".csv":"text/csv; charset=utf-8"}

class App(http.server.BaseHTTPRequestHandler):
    server_version = "SUP-IP/" + VERSION
    db = None

    def log_message(self, fmt, *args):
        line = "%s - %s" % (self.address_string(), fmt % args)
        # первый аргумент может быть не строкой (HTTPStatus из send_error) — приводим к тексту
        first = str(args[0]) if args else ""
        if "/api/state" in first:
            LOG.debug("HTTP %s", line)          # поллинг состояния — только в режиме DEBUG
        else:
            LOG.info("HTTP %s", line)

    # --- утилиты ответа ---
    def _headers(self, status, ctype, length, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers()

    def send_json(self, obj, status=200, cookie=None, clear_cookie=False):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        extra = {}
        if cookie:
            extra["Set-Cookie"] = "%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=86400%s" % (COOKIE, cookie, "; Secure" if TLS_ON else "")
        if clear_cookie:
            extra["Set-Cookie"] = "%s=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0" % COOKIE
        self._headers(status, "application/json; charset=utf-8", len(body), extra)
        self.wfile.write(body)

    def token(self):
        try:
            c = SimpleCookie(self.headers.get("Cookie", ""))
            return c[COOKIE].value if COOKIE in c else None
        except Exception:
            return None

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY: raise RpcError("Тело запроса слишком велико", "Запрос", 413, "BODY_TOO_BIG")
        return self.rfile.read(n) if n else b""

    def body_json(self):
        raw = self.body()
        try: return json.loads(raw.decode("utf-8")) if raw else {}
        except Exception: raise RpcError("Некорректный JSON", "Запрос", 400, "BAD_JSON")

    def need_user(self):
        u = session_user(self.db, self.token())
        if not u: raise RpcError("Сессия не найдена или истекла. Войдите заново.", "Требуется вход", 401, "NO_SESSION")
        return u

    # --- маршруты ---
    def do_GET(self):
        try:
            path = urllib.parse.urlparse(self.path).path
            if path.startswith("/api/"): return self.api_get(path)
            return self.serve_static(path)
        except RpcError as e:
            self.send_json({"ok": False, "error": {"code": e.code, "title": e.title, "message": str(e)}}, e.status)
        except (BrokenPipeError, ConnectionResetError): pass
        except Exception as e:
            LOG.error("GET %s · ВНУТРЕННЯЯ ОШИБКА: %s\n%s", self.path, e, traceback.format_exc())
            self.send_json({"ok": False, "error": {"code": "INTERNAL", "title": "Ошибка сервера", "message": str(e)}}, 500)

    def do_POST(self):
        try:
            path = urllib.parse.urlparse(self.path).path
            db = self.db
            if path == "/api/login":
                p = self.body_json()
                token, state = do_login(db, p.get("login"), p.get("password"), self.client_address[0])
                return self.send_json({"ok": True, "state": state}, cookie=token)
            if path == "/api/logout":
                u = session_user(db, self.token())
                do_logout(db, self.token(), u)
                return self.send_json({"ok": True}, clear_cookie=True)
            u = self.need_user()
            if path == "/api/rpc":
                p = self.body_json()
                res = dispatch(db, u, p.get("action", ""), p.get("payload") or {})
                status = 200 if res.get("ok") else res.pop("status", 400)
                return self.send_json(res, status)
            if path == "/api/export":
                return self.export_response(db, u, self.body_json())
            m = re.match(r"^/api/files/([A-Za-z0-9_\-]+)/(\d+)$", path)
            if m:
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                name = (q.get("name") or [self.headers.get("X-File-Name", "file.bin")])[0]
                try: name = urllib.parse.unquote(name)
                except Exception: pass
                state = store_file(db, u, m.group(1), int(m.group(2)), name, self.body())
                return self.send_json({"ok": True, "state": state, "msg": msg("Файл загружен на сервер", "Версия сохранена в хранилище data/files")})
            if path == "/api/restore":
                res = restore_backup(db, u, self.body_json(), self.token())
                return self.send_json({"ok": True, "relogin": res["relogin"],
                                       "msg": msg("База восстановлена из копии", "Все клиенты получат обновлённое состояние")},
                                      clear_cookie=res["relogin"])
            raise RpcError("Неизвестный маршрут", "API", 404, "NOT_FOUND")
        except RpcError as e:
            self.send_json({"ok": False, "error": {"code": e.code, "title": e.title, "message": str(e)}}, e.status,
                           clear_cookie=(e.code == "NO_SESSION"))
        except (BrokenPipeError, ConnectionResetError): pass
        except Exception as e:
            self.send_json({"ok": False, "error": {"code": "INTERNAL", "title": "Ошибка сервера", "message": str(e)}}, 500)

    def export_response(self, db, u, p):
        """Выгрузка документа (ТЗ §25): DOCX/XLSX/PDF/CSV/HTML с серверной проверкой прав и аудитом."""
        kind = p.get("kind") or ""
        fmt = (p.get("fmt") or "docx").lower()
        if fmt not in EXPORT_FORMATS:
            raise RpcError("Недопустимый формат: %s" % fmt, "Экспорт", 400, "BAD_FORMAT")
        params = dict(p.get("params") or {})
        for k, v in p.items():
            if k not in ("kind", "id", "fmt", "params"): params.setdefault(k, v)
        with db.wlock:
            conn = db.get()
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    try:
                        d, base = build_export(db, cur, u, kind, p.get("id") or "", params)
                        data = docgen.render(d, fmt)
                        mod, label = EXPORT_KINDS[kind]
                        db.audit(cur, "BUSINESS", "info", u["id"], mod,
                                 "Выгружен документ: %s (%s)" % (label, fmt.upper()), "Документ", p.get("id") or "",
                                 {"kind": kind, "fmt": fmt, "size": len(data), "title": d.title})
                        db.bump(cur); conn.commit()
                    except PermError as e:
                        conn.rollback()
                        db.audit(cur, "SECURITY", "warning", u["id"], e.mod, "Отказано в выгрузке документа (недостаточно прав)",
                                 "Документ", kind, {"op": e.op, "role": u["role"], "fmt": fmt})
                        conn.commit(); raise
                    except Exception:
                        conn.rollback(); raise
            finally:
                db.put(conn)
        fname = "%s.%s" % (base, fmt)
        self._headers(200, MIME.get("." + fmt, "application/octet-stream"), len(data),
                      {"Content-Disposition": "attachment; filename*=UTF-8''" + urllib.parse.quote(fname)})
        self.wfile.write(data)

    def api_get(self, path):
        db = self.db
        if path == "/api/logs":
            u = self.need_user()
            conn = db.get()
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    guard(db, cur, u, "admin", "view")
                conn.commit()
            finally: db.put(conn)
            return self.send_json({"ok": True, "dir": LOG_DIR, "level": LOG_STATE["level"], "files": log_files()})
        if path.startswith("/api/logs/"):
            u = self.need_user()
            conn = db.get()
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    guard(db, cur, u, "admin", "export")
                conn.commit()
            finally: db.put(conn)
            name = urllib.parse.unquote(path[len("/api/logs/"):])
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            tail = int((q.get("tail") or ["0"])[0])
            if tail:
                body = "\n".join(log_tail(name, tail)).encode("utf-8")
            else:
                if not LOG_NAME_RE.match(name or ""): raise RpcError("Недопустимое имя журнала", "Журналы", 400, "BAD_LOG_NAME")
                fp = os.path.join(LOG_DIR, name)
                if not os.path.isfile(fp): raise RpcError("Журнал не найден", "Журналы", 404, "NO_LOG")
                with open(fp, "rb") as f: body = f.read()
            LOG.info("Журнал %s выгружен пользователем %s (%d байт)", name, u.get("login"), len(body))
            self._headers(200, "text/plain; charset=utf-8", len(body),
                          {"Content-Disposition": 'attachment; filename="%s"' % name})
            return self.wfile.write(body)
        if path == "/api/formats":
            self.need_user()
            av = docgen.available() if docgen else {}
            return self.send_json({"ok": True, "formats": av, "kinds": {k: v[1] for k, v in EXPORT_KINDS.items()}})
        if path == "/api/export":
            u = self.need_user()
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            return self.export_response(db, u, {k: v[0] for k, v in q.items()})
        if path == "/api/health":
            conn = db.get()
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT COUNT(DISTINCT user_id) AS n FROM sessions WHERE last_seen > %s", (now_ms() - 60*60_000,))
                    online = cur.fetchone()["n"]; rev = db.rev(cur)
                conn.commit()
            finally: db.put(conn)
            return self.send_json({"ok": True, "app": APP_NAME, "version": VERSION, "db": "PostgreSQL", "rev": rev,
                                   "online": online, "tls": TLS_ON, "time": now_ms()})
        if path == "/api/bootstrap":
            u = session_user(db, self.token())
            base = {"ok": True, "server": {"name": APP_NAME, "version": VERSION, "db": "PostgreSQL", "tls": TLS_ON}}
            if not u: return self.send_json(dict(base, authed=False))
            conn = db.get()
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    state = db.state(cur, u["id"])
                conn.commit()
            finally: db.put(conn)
            return self.send_json(dict(base, authed=True, state=state))
        if path == "/api/state":
            u = self.need_user()
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            known = int((q.get("rev") or ["0"])[0])
            conn = db.get()
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    rev = db.rev(cur)
                    if rev == known:
                        conn.commit(); return self.send_json({"ok": True, "same": True, "rev": rev})
                    state = db.state(cur, u["id"])
                conn.commit()
            finally: db.put(conn)
            return self.send_json({"ok": True, "same": False, "state": state})
        m = re.match(r"^/api/files/([A-Za-z0-9_\-]+)/(\d+)$", path)
        if m:
            u = self.need_user()
            conn = db.get()
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                    fmt = (q.get("fmt") or [None])[0]
                    data, name, is_demo = read_file_bytes(db, cur, u, m.group(1), int(m.group(2)), fmt)
                    if not is_demo:
                        db.audit(cur, "AUDIT", "info", u["id"], "docs", "Скачан файл документа", "Документ", m.group(1), {"v": int(m.group(2)), "name": name})
                        conn.commit()
                    else: conn.commit()
            finally: db.put(conn)
            ctype = MIME.get(os.path.splitext(name)[1].lower(), "application/octet-stream")
            quoted = urllib.parse.quote(name)
            self._headers(200, ctype, len(data), {"Content-Disposition": "attachment; filename*=UTF-8''" + quoted})
            return self.wfile.write(data)
        if path == "/api/backup":
            u = self.need_user()
            data = make_backup(db, u)
            body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
            fname = "supip-backup-%s.json" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            self._headers(200, "application/json; charset=utf-8", len(body),
                          {"Content-Disposition": 'attachment; filename="%s"' % fname})
            return self.wfile.write(body)
        raise RpcError("Неизвестный маршрут", "API", 404, "NOT_FOUND")

    def serve_static(self, path):
        if path in ("/", ""): path = "/index.html"
        rel = os.path.normpath(urllib.parse.unquote(path)).lstrip("/\\")
        full = os.path.abspath(os.path.join(STATIC_DIR, rel))
        if not full.startswith(os.path.abspath(STATIC_DIR)) or not os.path.isfile(full):
            body = "404 · Не найдено".encode("utf-8")
            return self._headers(404, "text/plain; charset=utf-8", len(body)) or self.wfile.write(body)
        ctype = MIME.get(os.path.splitext(full)[1].lower(), "application/octet-stream")
        with open(full, "rb") as f: data = f.read()
        self._headers(200, ctype, len(data))
        self.wfile.write(data)

class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

def main():
    global TLS_ON, CONFIG_PATH
    ap = argparse.ArgumentParser(description="СУП-ИП — сервер управления проектами (PostgreSQL)")
    ap.add_argument("--host", help="адрес прослушивания (по умолчанию 0.0.0.0 — вся ЛВС)")
    ap.add_argument("--port", type=int, help="порт (по умолчанию из supip.ini или 8000)")
    ap.add_argument("--dsn", help="строка подключения PostgreSQL")
    ap.add_argument("--cert", help="PEM-сертификат для HTTPS")
    ap.add_argument("--key", help="PEM-ключ для HTTPS")
    ap.add_argument("--config", help="путь к supip.ini (по умолчанию рядом с EXE/скриптом)")
    ap.add_argument("--no-seed", action="store_true", help="не заполнять демо-данные при пустой БД")
    ap.add_argument("--install", action="store_true", help="мастер установки и настройки (ТЗ §7.3)")
    ap.add_argument("--install-auto", metavar="INI", help="автоматическая установка по параметрам из INI (для установщика)")
    ap.add_argument("--service", metavar="ACTION", help="служба Windows: install|remove|start|stop|restart|run")
    ap.add_argument("--migrate", action="store_true", help="применить миграции БД и выйти")
    ap.add_argument("--backup-now", action="store_true", help="создать резервную копию и выйти")
    ap.add_argument("--health", action="store_true", help="диагностика: БД, файловое хранилище, порт")
    ap.add_argument("--version", action="store_true", help="показать версию и выйти")
    ap.add_argument("--selftest", action="store_true", help="проверка целостности поставки (без БД): клиент, зависимости, ресурсы")
    ap.add_argument("--log-level", metavar="LEVEL", help="уровень файлового журнала: DEBUG|INFO|WARNING|ERROR")
    ap.add_argument("--log-dir", metavar="DIR", help="каталог журналов (по умолчанию data/logs)")
    args = ap.parse_args()
    log_init(args.log_level, args.log_dir)
    LOG.info("Режим запуска: %s · версия %s · Python %s · %s",
             " ".join(sys.argv[1:]) or "сервер", VERSION, platform.python_version(), platform.platform())
    if args.version:
        print("%s %s (%s, Python %s)" % (APP_NAME, VERSION, "EXE" if getattr(sys, "frozen", False) else "script", platform.python_version()))
        sys.exit(0)
    if args.selftest:
        sys.exit(selftest())
    if args.config: CONFIG_PATH = os.path.abspath(args.config)
    if args.install or args.install_auto:
        sys.exit(run_install(args.install_auto))
    if args.service:
        sys.exit(service_command(args.service))
    cfg = load_config()
    if not args.log_level and (cfg.get("log_level") or cfg.get("log_dir")):
        log_init(cfg.get("log_level"), args.log_dir or cfg.get("log_dir"),
                 float(cfg.get("log_max_mb") or 10), int(cfg.get("log_keep") or 5))
    host = args.host or cfg["host"]
    port = args.port or int(os.environ.get("SUPIP_PORT", cfg["port"]))
    dsn = args.dsn or os.environ.get("SUPIP_DSN", cfg["dsn"])
    cert = args.cert or cfg["cert"] or None
    key = args.key or cfg["key"] or None
    os.makedirs(FILES_DIR, exist_ok=True)
    LOG.info("Подключение к PostgreSQL: %s", log_mask(dsn))
    try:
        db = DB(dsn)
    except Exception as e:
        if args.backup_now:
            LOG.warning("Резервная копия не создана: PostgreSQL недоступен (%s)", e)
            print("Резервная копия не создана: PostgreSQL недоступен.\n"
                  "При первой установке это нормально — данных ещё нет; при обновлении проверьте службу PostgreSQL.")
            print("Журнал: %s" % os.path.join(LOG_DIR, "supip.log"))
            sys.exit(3)
        LOG.critical("PostgreSQL недоступен: %s\n%s", e, traceback.format_exc())
        print("Не удалось подключиться к PostgreSQL: %s\nЗапустите мастер установки: SUP-IP.exe --install (или run.bat)." % e)
        print("Журнал: %s" % os.path.join(LOG_DIR, "supip.log"))
        sys.exit(2)
    applied = migrate(db)
    LOG.info("Схема БД: версия %s%s", SCHEMA_VERSION, "; применены миграции %s" % applied if applied else "")
    if args.health:
        ok = True
        conn = db.get()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT COUNT(*) AS n FROM users"); n = cur.fetchone()["n"]
                cur.execute("SELECT v FROM meta WHERE k='schema'"); sv = cur.fetchone()["v"]
            conn.commit()
            print("БД: OK (PostgreSQL, пользователей: %d, схема: %s)" % (n, sv))
        except Exception as e:
            print("БД: ОШИБКА %s" % e); ok = False
        finally:
            db.put(conn)
        try:
            probe = os.path.join(FILES_DIR, ".probe")
            open(probe, "w").write("ok"); os.remove(probe)
            print("Файловое хранилище: OK (%s)" % FILES_DIR)
        except Exception as e:
            print("Файловое хранилище: ОШИБКА %s" % e); ok = False
        scheme = "https" if (cert and key) else "http"
        probe_host = "127.0.0.1" if host in ("0.0.0.0", "", "::") else host
        ready, info = server_health_probe(scheme, probe_host, port)
        if ready:
            print("Сервер: ОТВЕЧАЕТ %s://%s:%s/api/health (версия %s, БД %s)"
                  % (scheme, probe_host, port, info.get("version", "?"), info.get("db", "?")))
        else:
            print("Сервер: НЕ ОТВЕЧАЕТ %s://%s:%s (%s)" % (scheme, probe_host, port, info.get("error", "нет ответа")))
        print(port_status_line(port, socket_bind_probe(host, port), ready))
        sys.exit(0 if ok else 1)
    if args.backup_now:
        conn = db.get()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                data = {"app": APP_NAME, "version": VERSION, "ts": now_ms(), "rev": db.rev(cur), "tables": {}}
                for t in BACKUP_TABLES:
                    cur.execute("SELECT * FROM " + t); data["tables"][t] = [dict(r) for r in cur.fetchall()]
                db.audit(cur, "TECH", "info", "system", "admin", "Резервная копия создана (CLI --backup-now)", "БД", "", {})
                conn.commit()
        finally:
            db.put(conn)
        os.makedirs(os.path.join(DATA_DIR, "backups"), exist_ok=True)
        p = os.path.join(DATA_DIR, "backups", "supip-backup-%s.json" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        with open(p, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, default=str)
        print("Резервная копия: %s" % p)
        sys.exit(0)
    if args.migrate:
        print("Миграции применены; схема актуальна."); sys.exit(0)
    if not args.no_seed and cfg.get("seed", "1") == "1": seed(db)
    App.db = db
    srv = Server((host, port), App)
    scheme = "http"
    if cert and key:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        TLS_ON = True; scheme = "https"
    print("=" * 62)
    print("  %s v%s — сервер запущен" % (APP_NAME, VERSION))
    print("  СУБД: PostgreSQL · хранилище файлов: %s" % FILES_DIR)
    print("  Адрес: %s://%s:%d  (веб-клиенты ЛВС — по IP этого ПК)" % (scheme, host, port))
    print("  Демо-вход: u1…u8, пароль demo (u1 — администратор)")
    print("  Центр уведомлений: планировщик запущен (интервал 30 с)")
    print("  Журнал работы: %s" % os.path.join(LOG_DIR, "supip.log"))
    print("=" * 62)
    LOG.info("СЕРВЕР ЗАПУЩЕН: %s://%s:%d · файловое хранилище %s · журнал %s (уровень %s)",
             scheme, host, port, FILES_DIR, LOG_DIR, LOG_STATE["level"])
    threading.Thread(target=notification_worker, args=(db,), daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановка сервера…")
        LOG.info("Остановка сервера по сигналу пользователя")
    except Exception as e:
        LOG.critical("Аварийная остановка сервера: %s\n%s", e, traceback.format_exc())
        raise
    finally:
        srv.server_close()
        LOG.info("СЕРВЕР ОСТАНОВЛЕН")

# ================= Установка и настройка через EXE (ТЗ §7) =================
CONFIG_PATH = os.path.join(BASE_DIR, "supip.ini")
SCHEMA_VERSION = 5   # 2 — уведомления (v1.4.0), 3 — маршрут протокола (v1.5.0), 4 — поручения (v1.6.0), 5 — планирование совещаний (v1.10.0)
MIGRATIONS = {   # {версия: [SQL, ...]} — применяется по порядку при старте/обновлении
    2: [  # v1.4.0: центр уведомлений и напоминаний
        """CREATE TABLE IF NOT EXISTS notifications(
             id TEXT PRIMARY KEY, ts BIGINT NOT NULL, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
             type TEXT NOT NULL, module TEXT NOT NULL, obj TEXT NOT NULL DEFAULT '', obj_id TEXT NOT NULL DEFAULT '',
             title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', channel TEXT NOT NULL DEFAULT 'web',
             status TEXT NOT NULL DEFAULT 'sent', error TEXT NOT NULL DEFAULT '', read_at BIGINT,
             dedupe TEXT NOT NULL UNIQUE)""",
        "CREATE INDEX IF NOT EXISTS ix_notif_user ON notifications(user_id, ts DESC)",
        """CREATE TABLE IF NOT EXISTS reminders(
             id TEXT PRIMARY KEY, owner_type TEXT NOT NULL, owner_id TEXT NOT NULL, user_id TEXT NOT NULL,
             offset_min INT NOT NULL, fire_at BIGINT NOT NULL, sent_at BIGINT, key TEXT NOT NULL UNIQUE,
             created_by TEXT, created_at BIGINT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS ix_reminders_fire ON reminders(fire_at) WHERE sent_at IS NULL",
    ],
    3: [  # v1.5.0: маршрут согласования и версии протокола (ТЗ M16 §2.9)
        "ALTER TABLE meetings ADD COLUMN IF NOT EXISTS protocol JSONB NOT NULL DEFAULT '{}'",
    ],
    4: [  # v1.6.0: поручения совещаний (ТЗ M16 §2.10, техпроект §4.1)
        "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS directive JSONB NOT NULL DEFAULT '{}'",
        """CREATE TABLE IF NOT EXISTS meeting_tasks(
             id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
             decision_id TEXT NOT NULL, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
             ts BIGINT NOT NULL, by_user TEXT, UNIQUE(decision_id, task_id))""",
        "CREATE INDEX IF NOT EXISTS ix_meeting_tasks_m ON meeting_tasks(meeting_id)",
        "CREATE INDEX IF NOT EXISTS ix_meeting_tasks_t ON meeting_tasks(task_id)",
    ],
    5: [  # v1.10.0: планирование совещаний (ТЗ M16 §2.5)
        "ALTER TABLE meetings ADD COLUMN IF NOT EXISTS materials JSONB NOT NULL DEFAULT '[]'",
        "ALTER TABLE meetings ADD COLUMN IF NOT EXISTS projects JSONB NOT NULL DEFAULT '[]'",
        "ALTER TABLE meetings ADD COLUMN IF NOT EXISTS recurrence JSONB NOT NULL DEFAULT '{}'",
        "ALTER TABLE meetings ADD COLUMN IF NOT EXISTS plan_log JSONB NOT NULL DEFAULT '[]'",
        "ALTER TABLE meetings ADD COLUMN IF NOT EXISTS series_id TEXT NOT NULL DEFAULT ''",
        "CREATE INDEX IF NOT EXISTS ix_meetings_series ON meetings(series_id) WHERE series_id <> ''",
        "CREATE INDEX IF NOT EXISTS ix_meetings_date ON meetings(date)",
    ],
}

def migrate(db):
    """Миграции схемы БД (ТЗ §7.6: обновление выполняет миграции)."""
    conn = db.get()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT v FROM meta WHERE k='schema'")
            r = cur.fetchone(); cur_ver = int(r["v"]) if r else 0
            applied = []
            for ver in sorted(MIGRATIONS):
                if ver > cur_ver:
                    for sql in MIGRATIONS[ver]: cur.execute(sql)
                    cur.execute("UPDATE meta SET v=%s WHERE k='schema'", (str(ver),))
                    applied.append(ver)
            if applied:
                db.audit(cur, "TECH", "info", "system", "admin", "Миграции БД применены: %s" % applied, "БД", "", {"schema": max(applied)})
            conn.commit()
            return applied
    finally:
        db.put(conn)

def load_config(path=None):
    """Конфигурация supip.ini (рядом с EXE/скриптом). CLI и env имеют приоритет."""
    cfg = {"host": "0.0.0.0", "port": "8000",
           "dsn": "host=127.0.0.1 port=5432 dbname=supip user=supip password=supip",
           "cert": "", "key": "", "seed": "1",
           "log_level": "INFO", "log_dir": "", "log_max_mb": "10", "log_keep": "5"}
    p = path or CONFIG_PATH
    if os.path.isfile(p):
        cp = configparser.ConfigParser()
        cp.read(p, encoding="utf-8")
        if cp.has_section("server"):
            for k in ("host", "port", "dsn", "cert", "key", "seed"):
                if cp.has_option("server", k): cfg[k] = cp.get("server", k)
        if cp.has_section("logs"):
            for k in ("log_level", "log_dir", "log_max_mb", "log_keep"):
                opt = k[4:]
                if cp.has_option("logs", opt): cfg[k] = cp.get("logs", opt)
    return cfg

def save_config(cfg, path=None):
    cp = configparser.ConfigParser()
    cp["server"] = {k: v for k, v in cfg.items() if not k.startswith("log_")}
    cp["logs"] = {"level": cfg.get("log_level", "INFO"), "dir": cfg.get("log_dir", ""),
                  "max_mb": str(cfg.get("log_max_mb", "10")), "keep": str(cfg.get("log_keep", "5"))}
    with open(path or CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write("# СУП-ИП — конфигурация сервера (создана мастером установки)\n")
        f.write("# [logs] — файловые журналы: level=DEBUG|INFO|WARNING|ERROR, dir, max_mb, keep\n")
        cp.write(f)

def _gen_cert(cert_path, key_path, cn):
    """Самоподписанный сертификат для HTTPS ЛВС: cryptography → openssl → отказ."""
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
        now = datetime.datetime.utcnow()
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(days=1))
                .not_valid_after(now + datetime.timedelta(days=825))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName(cn), x509.DNSName("localhost")]), critical=False)
                .sign(key, hashes.SHA256()))
        with open(key_path, "wb") as f:
            f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
        with open(cert_path, "wb") as f: f.write(cert.public_bytes(serialization.Encoding.PEM))
        return True
    except ImportError:
        pass
    try:
        r = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                            "-keyout", key_path, "-out", cert_path, "-days", "825", "-subj", "/CN=" + cn],
                           capture_output=True, timeout=60)
        return r.returncode == 0
    except Exception:
        return False

def _pg_ensure(dsn_super, app_db, app_user, app_pass, report):
    """Создание роли и БД приложения через суперпользователя PostgreSQL (идемпотентно)."""
    conn = psycopg2.connect(dsn_super)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (app_user,))
            if not cur.fetchone():
                cur.execute("CREATE USER %s PASSWORD %%s" % psycopg2.extensions.quote_ident(app_user, cur), (app_pass,))
                report.append("Роль PostgreSQL «%s» создана" % app_user)
            else:
                cur.execute("ALTER USER %s PASSWORD %%s" % psycopg2.extensions.quote_ident(app_user, cur), (app_pass,))
                report.append("Роль PostgreSQL «%s» существует (пароль обновлён)" % app_user)
            cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (app_db,))
            if not cur.fetchone():
                cur.execute("CREATE DATABASE %s OWNER %s" % (psycopg2.extensions.quote_ident(app_db, cur), psycopg2.extensions.quote_ident(app_user, cur)))
                report.append("База данных «%s» создана" % app_db)
            else:
                report.append("База данных «%s» существует" % app_db)
    finally:
        conn.close()

def install_report_write(lines):
    os.makedirs(DATA_DIR, exist_ok=True)
    p = os.path.join(DATA_DIR, "install-report.txt")
    with open(p, "w", encoding="utf-8") as f:
        f.write("СУП-ИП v%s — отчёт об установке (%s)\n\n" % (VERSION, datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")))
        f.write("\n".join(lines) + "\n")
    return p

def run_install(auto_path=None):
    """Мастер установки/настройки (ТЗ §7.3). Интерактивный или автоматический (--install-auto ini).
       Каждая строка отчёта сразу пишется в data/logs/setup.log — журнал сохраняется и при сбое."""
    if not LOG_STATE["ready"]: log_init()
    report = ReportList()
    SETUP_LOG.info("=== Мастер установки СУП-ИП v%s (режим: %s) ===", VERSION, "автоматический" if auto_path else "интерактивный")
    report.append("Версия: %s · Python %s · %s" % (VERSION, platform.python_version(), platform.platform()))
    print("=" * 64)
    print("  СУП-ИП v%s — мастер установки и настройки" % VERSION)
    print("=" * 64)
    auto = None
    if auto_path:
        cp = configparser.ConfigParser(); cp.read(auto_path, encoding="utf-8")
        auto = dict(cp["install"]) if cp.has_section("install") else {}
    def ask(key, prompt, default, secret=False):
        if auto is not None: return auto.get(key, default)
        suffix = " [%s]" % default if default else ""
        try:
            v = input("%s%s: " % (prompt, suffix)).strip()
        except EOFError:
            v = ""
        return v or default
    # 1. Проверка совместимости (ТЗ §7.2)
    if sys.platform == "win32":
        wv = sys.getwindowsversion()
        ok = wv.major >= 10
        report.append("ОС: Windows %d.%d x%s — %s" % (wv.major, wv.build, "64" if platform.machine().endswith("64") else "32", "совместима" if ok else "ТРЕБУЕТСЯ Windows 10/11 x64"))
        if not ok: print("ОШИБКА: требуется Windows 10/11 x64"); return 2
    else:
        report.append("ОС: %s (режим разработки; целевая платформа — Windows 10/11 x64)" % platform.system())
    free_gb = shutil.disk_usage(BASE_DIR).free / 2**30
    report.append("Свободно на диске: %.1f ГБ — %s" % (free_gb, "достаточно" if free_gb >= 1 else "МАЛО (<1 ГБ)"))
    if free_gb < 1: print("ОШИБКА: недостаточно дискового пространства"); return 2
    # 2. Параметры
    pg_host = ask("pg_host", "Адрес PostgreSQL", "127.0.0.1")
    pg_port = ask("pg_port", "Порт PostgreSQL", "5432")
    pg_super = ask("pg_super", "Суперпользователь PostgreSQL", "postgres")
    pg_super_pass = ask("pg_super_pass", "Пароль суперпользователя PostgreSQL", "", secret=True)
    app_db = ask("app_db", "Имя БД приложения", "supip")
    app_user = ask("app_user", "Роль приложения", "supip")
    app_pass = ask("app_pass", "Пароль роли приложения", "supip")
    port = ask("port", "Порт веб-сервера", "8443" if sys.platform == "win32" else "8000")
    bind = ask("host", "Адрес прослушивания (0.0.0.0 — вся ЛВС)", "0.0.0.0")
    https = ask("https", "HTTPS (1 — да, 0 — нет)", "1").lower() in ("1", "да", "yes", "true")
    admin_pass = ask("admin_pass", "Пароль администратора приложения (логин admin)", "demo")
    # 3. Роль и БД
    dsn_super = "host=%s port=%s dbname=postgres user=%s password=%s" % (pg_host, pg_port, pg_super, pg_super_pass)
    try:
        _pg_ensure(dsn_super, app_db, app_user, app_pass, report)
    except Exception as e:
        SETUP_LOG.error("PostgreSQL: %s\n%s", e, traceback.format_exc())
        report.append("ОШИБКА PostgreSQL: %s" % e)
        report.append("Журнал настройки: %s" % os.path.join(LOG_DIR, "setup.log"))
        print("\n".join(report)); print("\nПроверьте, что служба PostgreSQL запущена и пароль суперпользователя верен.")
        install_report_write(report); return 3
    # 4. Схема, миграции, демо-данные
    dsn = "host=%s port=%s dbname=%s user=%s password=%s" % (pg_host, pg_port, app_db, app_user, app_pass)
    db = DB(dsn)
    applied = migrate(db)
    report.append("Схема БД создана/проверена (версия %d)%s" % (SCHEMA_VERSION, "; миграции: %s" % applied if applied else ""))
    seeded = seed(db)
    report.append("Демо-данные: %s" % ("создан эталонный набор" if seeded else "уже присутствуют"))
    # 5. Пароль администратора
    if admin_pass and admin_pass != "demo":
        conn = db.get()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                salt, h = pass_hash(admin_pass)
                cur.execute("UPDATE users SET pass_salt=%s, pass_hash=%s WHERE login='admin'", (salt, h))
                db.audit(cur, "SECURITY", "info", "system", "admin", "Установлен пароль администратора при установке", "Пользователь", "u1", {})
                conn.commit()
            report.append("Учётная запись администратора: admin (пароль задан мастером)")
        finally:
            db.put(conn)
    else:
        report.append("Учётная запись администратора: admin / demo (смените после входа!)")
    # 6. Файловое хранилище
    os.makedirs(FILES_DIR, exist_ok=True)
    os.makedirs(os.path.join(DATA_DIR, "backups"), exist_ok=True)
    report.append("Файловое хранилище: %s" % FILES_DIR)
    # 7. HTTPS
    cert = key = ""
    if https:
        cert_dir = os.path.join(DATA_DIR, "cert"); os.makedirs(cert_dir, exist_ok=True)
        cert, key = os.path.join(cert_dir, "cert.pem"), os.path.join(cert_dir, "key.pem")
        if not (os.path.isfile(cert) and os.path.isfile(key)):
            cn = ask("cn", "Имя сервера для сертификата (CN)", platform.node() or "supip-server")
            if _gen_cert(cert, key, cn):
                report.append("HTTPS: самоподписанный сертификат создан (%s)" % cert)
            else:
                report.append("HTTPS: не удалось создать сертификат (нет cryptography/openssl) — сервер будет работать по HTTP")
                cert = key = ""
        else:
            report.append("HTTPS: использован существующий сертификат")
    else:
        report.append("HTTPS: отключён мастером (включите: --cert/--key или supip.ini)")
    # 8. Конфигурация
    cfg = {"host": bind, "port": str(port), "dsn": dsn, "cert": cert, "key": key, "seed": "1",
           "log_level": LOG_STATE["level"], "log_dir": LOG_DIR}
    save_config(cfg)
    report.append("Конфигурация сохранена: %s" % CONFIG_PATH)
    report.append("Файловые журналы: %s (уровень %s)" % (LOG_DIR, LOG_STATE["level"]))
    # 9. Брандмауэр и служба (Windows)
    service_started = False
    if sys.platform == "win32":
        try:
            r = subprocess.run(["netsh", "advfirewall", "firewall", "add", "rule", "name=SUP-IP",
                                "dir=in", "action=allow", "protocol=TCP", "localport=%s" % port], capture_output=True, timeout=30)
            report.append("Брандмауэр: правило для TCP %s — %s" % (port, "добавлено" if r.returncode == 0 else "не добавлено (нужны права администратора)"))
        except Exception as e:
            report.append("Брандмауэр: %s" % e)
        if ask("service", "Установить службу Windows с автозапуском (1/0)", "1") in ("1", "да", "yes"):
            rc = service_command("install", cfg)
            service_started = rc == 0
            report.append("Служба Windows: %s" % ("установлена, команда запуска выдана" if rc == 0 else "не установлена (см. вывод выше)"))
    else:
        report.append("Служба Windows: пропущено (не Windows); используйте systemd/run.sh")
    # 10. Проверка работоспособности
    conn = db.get()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1"); cur.fetchone()
        conn.commit(); report.append("Проверка БД: соединение OK")
    finally:
        db.put(conn)
    scheme = "https" if cert else "http"
    probe_host = "127.0.0.1" if bind in ("0.0.0.0", "", "::") else bind
    if service_started:
        ready, info = wait_server_ready(scheme, probe_host, int(port), wait_s=40)
        if ready:
            report.append("Проверка запуска сервера: отвечает %s://%s:%s/api/health · версия %s · БД %s"
                          % (scheme, probe_host, port, info.get("version", "?"), info.get("db", "?")))
            SETUP_LOG.info("Служба поднята: сервер отвечает на %s://%s:%s", scheme, probe_host, port)
        else:
            report.append("Проверка запуска сервера: НЕТ ОТВЕТА за 40 с (%s). Проверьте службу командой "
                          "sc query SUP-IP и журнал %s" % (info.get("error", "нет ответа"), os.path.join(LOG_DIR, "supip.log")))
            SETUP_LOG.warning("Служба установлена, но сервер не ответил за 40 с: %s", info.get("error"))
    else:
        ready, info = server_health_probe(scheme, probe_host, int(port))
        report.append("Проверка запуска сервера: %s" % (
            "сервер уже отвечает (%s://%s:%s)" % (scheme, probe_host, port) if ready
            else "пропущено — служба не устанавливалась; запустите сервер и проверьте: SUP-IP.exe --health"))
    try:
        probe = socket_bind_probe(bind, int(port))
        report.append(port_status_line(port, probe, ready))
    except Exception as e:
        report.append("Проверка порта: %s" % e)
    report.append("")
    if service_started and not ready:
        report.append("ВНИМАНИЕ: служба установлена, но сервер не ответил. Разбор: %s и %s"
                      % (os.path.join(LOG_DIR, "setup.log"), os.path.join(LOG_DIR, "supip.log")))
    report.append("ГОТОВО. Запуск: %s" % ("SUP-IP.exe (служба)" if sys.platform == "win32" else "python3 server.py"))
    report.append("Клиенты в ЛВС: %s://<IP-этого-ПК>:%s" % (scheme, port))
    report.append("Вход: admin / %s" % ("(пароль мастера)" if admin_pass != "demo" else "demo"))
    report.append("Журнал настройки: %s" % os.path.join(LOG_DIR, "setup.log"))
    p = install_report_write(report)
    print("\n".join(report))
    print("\nОтчёт об установке: %s" % p)
    SETUP_LOG.info("=== Установка завершена успешно ===")
    return 0

def server_health_probe(scheme, host, port, timeout=3):
    """Проверка фактического ответа сервера (/api/health). Самоподписанный сертификат мастера
       не проверяется по доверию — важен сам факт ответа службы."""
    url = "%s://%s:%s/api/health" % (scheme, host or "127.0.0.1", port)
    try:
        if scheme == "https":
            ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
            r = urllib.request.urlopen(url, timeout=timeout, context=ctx)
        else:
            r = urllib.request.urlopen(url, timeout=timeout)
        j = json.loads(r.read().decode("utf-8"))
        if j.get("ok"): return True, j
        return False, {"error": "сервер ответил, но состояние не OK", "url": url}
    except Exception as e:
        return False, {"error": str(e), "url": url}

def wait_server_ready(scheme, host, port, wait_s=40, interval=1.0):
    """Служба Windows поднимается асинхронно: ждём фактического ответа сервера (ТЗ §7, §15)."""
    deadline = time.time() + max(1, wait_s)
    okk, info = server_health_probe(scheme, host, port)
    while not okk and time.time() < deadline:
        time.sleep(interval)
        okk, info = server_health_probe(scheme, host, port)
    return okk, info

def port_status_line(port, free, ready):
    """Однозначная трактовка состояния порта: занят своей службой — это норма, а не ошибка."""
    if ready:
        return ("Проверка порта %s: занят службой СУП-ИП (ожидаемо)" % port if not free
                else "Проверка порта %s: сервер отвечает (состояние порта подтверждено ответом)" % port)
    return ("Проверка порта %s: свободен — сервер НЕ запущен (служба не подняла порт)" % port if free
            else "Проверка порта %s: ЗАНЯТ другим процессом — освободите порт или измените supip.ini" % port)

def socket_bind_probe(bind, port):
    """Проверка занятости порта. На Windows SO_REUSEADDR разрешает связывание уже занятого
       порта, поэтому проба всегда сообщала «свободен» — используем исключительный режим."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((bind if bind != "0.0.0.0" else "", port)); return True
    except OSError:
        return False
    finally:
        s.close()

def selftest():
    """Проверка целостности поставки без обращения к БД (ТЗ §15.3: испытания установки).
       Используется сборкой EXE и мастером установки: SUP-IP.exe --selftest."""
    ok = True
    if not LOG_STATE["ready"]: log_init()
    SETUP_LOG.info("=== Самотестирование поставки СУП-ИП v%s ===", VERSION)
    print("СУП-ИП %s — самотестирование поставки" % VERSION)
    idx = os.path.join(STATIC_DIR, "index.html")
    try:
        size = os.path.getsize(idx)
        body = open(idx, encoding="utf-8").read(4096)
        good = size > 100_000 and "<html" in body.lower()
        print("  Веб-клиент static/index.html: %s (%.0f КБ)" % ("OK" if good else "ПОВРЕЖДЁН", size / 1024))
        ok = ok and good
    except Exception as e:
        print("  Веб-клиент: ОШИБКА %s" % e); ok = False
    print("  psycopg2: OK (%s)" % psycopg2.__version__)
    for mod, need, why in (("cryptography", False, "HTTPS без openssl"),
                           ("win32serviceutil", sys.platform == "win32", "служба Windows"),
                           ("docgen", False, "экспорт DOCX/XLSX/PDF")):
        try:
            __import__(mod); print("  %s: OK — %s" % (mod, why))
        except ImportError:
            print("  %s: %s — %s" % (mod, "ОШИБКА" if need else "нет (необязательно)", why))
            ok = ok and not need
    if docgen is not None:
        av = docgen.available()
        print("  Форматы документов: %s" % ", ".join(k.upper() for k, v in av.items() if v))
        if not av.get("pdf"):
            print("  Шрифты assets/fonts: ОШИБКА — PDF недоступен"); ok = False
    try:
        os.makedirs(FILES_DIR, exist_ok=True)
        probe = os.path.join(FILES_DIR, ".selftest")
        open(probe, "w").write("ok"); os.remove(probe)
        print("  Каталог данных: OK (%s)" % DATA_DIR)
    except Exception as e:
        print("  Каталог данных: ОШИБКА %s" % e); ok = False
    print("ИТОГ: %s" % ("поставка исправна" if ok else "ЕСТЬ ОШИБКИ"))
    print("Журнал: %s" % os.path.join(LOG_DIR, "setup.log"))
    SETUP_LOG.info("Самотестирование: %s", "поставка исправна" if ok else "ЕСТЬ ОШИБКИ")
    return 0 if ok else 1

# ---------------- Служба Windows (ТЗ §7.4: автозапуск, контролируемый перезапуск) ----------------
def _svc_absent(e):
    """Служба не установлена (winerror 1060) — для stop/remove это штатная ситуация."""
    return getattr(e, "winerror", None) == 1060 or "1060" in str(e)

def service_command(action, cfg=None):
    if not LOG_STATE["ready"]: log_init()
    LOG.info("Служба Windows: команда '%s'", action)
    if sys.platform != "win32":
        print("Служба Windows доступна только на Windows. На Linux используйте systemd или run.sh."); return 1
    try:
        import win32serviceutil, win32service, servicemanager  # noqa
    except ImportError:
        print("Для службы нужен pywin32: python -m pip install pywin32 (входит в EXE-сборку)."); return 1
    name = "SUP-IP"
    exe = sys.executable if getattr(sys, "frozen", False) else os.path.abspath(sys.argv[0])
    exe_args = '--service run --config "%s"' % CONFIG_PATH
    if action == "install":
        try:
            win32serviceutil.InstallService(None, name, "СУП-ИП — сервер управления проектами",
                                            startType=win32service.SERVICE_AUTO_START,
                                            exeName=exe, exeArgs=exe_args,
                                            description="СУП-ИП: веб-сервер управления проектами инженерного подразделения (PostgreSQL)")
            subprocess.run(["sc", "failure", name, "reset=", "86400", "actions=", "restart/5000/restart/5000/restart/5000"], capture_output=True)
            win32serviceutil.StartService(name)
            return 0
        except Exception as e:
            print("Служба: %s" % e); return 1
    if action == "remove":
        try:
            try: win32serviceutil.StopService(name)
            except Exception: pass
            win32serviceutil.RemoveService(name); return 0
        except Exception as e:
            if _svc_absent(e):
                LOG.info("Служба Windows не установлена — удаление не требуется")
                print("Служба не установлена — удаление не требуется."); return 0
            print("Служба: %s" % e); return 1
    if action in ("start", "stop", "restart"):
        try:
            if action == "restart":
                try: win32serviceutil.StopService(name)
                except Exception: pass
                win32serviceutil.StartService(name)
            elif action == "start": win32serviceutil.StartService(name)
            else: win32serviceutil.StopService(name)
            return 0
        except Exception as e:
            if _svc_absent(e) and action in ("stop", "restart"):
                LOG.info("Служба Windows не установлена — остановка не требуется")
                print("Служба не установлена — остановка не требуется."); return 0
            print("Служба: %s" % e); return 1
    if action == "run":
        import servicemanager, win32serviceutil
        cfg = cfg or load_config()
        class SupipService(win32serviceutil.ServiceFramework):
            _svc_name_ = name
            _svc_display_name_ = "СУП-ИП — сервер управления проектами"
            def SvcStop(self):
                self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
                LOG.info("Служба Windows: получен запрос остановки")
                if self._srv: threading.Thread(target=self._srv.shutdown, daemon=True).start()
            def SvcDoRun(self):
                self._srv = None
                log_init(cfg.get("log_level"), cfg.get("log_dir"),
                         float(cfg.get("log_max_mb") or 10), int(cfg.get("log_keep") or 5))
                LOG.info("Служба Windows: запуск сервера СУП-ИП v%s", VERSION)
                try:
                    db = DB(cfg["dsn"])
                    migrate(db)
                    if cfg.get("seed", "1") == "1": seed(db)
                    App.db = db
                    srv = Server((cfg["host"], int(cfg["port"])), App)
                    if cfg.get("cert") and cfg.get("key"):
                        global TLS_ON
                        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                        ctx.load_cert_chain(cfg["cert"], cfg["key"])
                        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
                        TLS_ON = True
                    self._srv = srv
                    threading.Thread(target=notification_worker, args=(db,), daemon=True).start()
                    LOG.info("Служба Windows: сервер слушает %s:%s", cfg["host"], cfg["port"])
                    srv.serve_forever()
                except Exception as e:
                    LOG.critical("Служба Windows: аварийное завершение: %s\n%s", e, traceback.format_exc())
                    servicemanager.LogErrorMsg("СУП-ИП: %s (журнал: %s)" % (e, os.path.join(LOG_DIR, "supip.log")))
        servicemanager.PrepareToHostSingle(SupipService)
        servicemanager.StartServiceCtrlDispatcher()
        return 0
    print("Неизвестное действие службы: %s" % action); return 1

if __name__ == "__main__":
    main()


