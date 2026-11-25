#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
СУП-ИП. Испытания схемы БД и инициализации НОВОЙ базы (ТТ-2 §4.1, §7.6).
Запуск:  python3 tests/schema_test.py [--dsn "host=... dbname=supip user=... password=..."]
         DSN берётся из --dsn, затем из SUPIP_DSN, затем из supip.ini.

Причина набора — отказ запуска на чистой базе: базовая схема SCHEMA не содержала
колонок и таблицы, добавленных миграцией (метка версии схемы записывалась сразу,
миграции на новой базе не применялись) → «column requisites of relation meetings
does not exist» при заполнении эталонного набора.

Проверяется:
  A. полнота базовой схемы: каждая таблица/колонка/индекс из MIGRATIONS есть в SCHEMA;
  B. соответствие слоя объектов ENT и STATE_KEYS фактической схеме;
  C. полнота резервного копирования: BACKUP_TABLES и список вставки в restore_backup;
  D. создание НОВОЙ базы: схема, метка версии, эталонный набор, шаблоны §2.8.1;
  E. сходимость схемы: база с опережающей меткой версии и отсутствующими колонками
     (следствие отказа v1.14.0) чинится при следующем запуске;
  F. обновление прежней версии: метка 6 → миграция 7 применяет колонки и таблицу.
Часть A–C выполняется без PostgreSQL; D–F — при доступной СУБД и праве CREATE DATABASE.
Только стандартная библиотека Python и psycopg2 из поставки.
"""
import argparse, importlib.util, os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SERVER = os.path.join(ROOT, "server.py")
ok = fail = skip = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print("  ok  " + name)
    else:
        fail += 1; print("  FAIL " + name + ("  " + str(extra) if extra else ""))


def skipped(name, why):
    global skip
    skip += 1; print("  --  " + name + " (пропуск: " + why + ")")


def load_server():
    spec = importlib.util.spec_from_file_location("supip_server_schema_test", SERVER)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---------------------------------------------------------------- разбор DDL
def schema_tables(sql):
    """{таблица: {колонка: определение}} из CREATE TABLE IF NOT EXISTS."""
    out = {}
    for mo in re.finditer(r"CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\((.*?)\);", sql, re.S):
        name, body = mo.group(1), mo.group(2)
        cols = {}
        depth = 0; cur = ""
        for ch in body:
            if ch == "(": depth += 1
            if ch == ")": depth -= 1
            if ch == "," and depth == 0:
                cols_add(cols, cur); cur = ""
            else:
                cur += ch
        cols_add(cols, cur)
        out[name] = cols
    return out


def cols_add(cols, frag):
    frag = frag.strip()
    if not frag: return
    mo = re.match(r'^"?(\w+)"?\s+(.*)$', frag, re.S)
    if not mo: return
    name, rest = mo.group(1), mo.group(2).strip()
    if name.upper() in ("PRIMARY", "UNIQUE", "FOREIGN", "CHECK", "CONSTRAINT"): return
    cols[name] = " ".join(rest.split())


def schema_indexes(sql):
    return set(re.findall(r"CREATE INDEX IF NOT EXISTS\s+(\w+)", sql))


def type_of(defn):
    mo = re.match(r"^([A-Za-z]+)", defn or "")
    return mo.group(1).upper() if mo else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("SUPIP_DSN", ""))
    ap.add_argument("--no-live", action="store_true", help="только статические проверки A–C")
    a = ap.parse_args()
    srv = load_server()

    tables = schema_tables(srv.SCHEMA)
    indexes = schema_indexes(srv.SCHEMA)
    print("== A. Полнота базовой схемы относительно миграций ==")
    check("базовая схема разбирается", len(tables) >= 18, len(tables))
    check("версия схемы = старшая миграция", srv.SCHEMA_VERSION == max(srv.MIGRATIONS),
          (srv.SCHEMA_VERSION, sorted(srv.MIGRATIONS)))
    need_t, need_c, need_i = [], [], []
    for ver in sorted(srv.MIGRATIONS):
        for sql in srv.MIGRATIONS[ver]:
            s = " ".join(sql.split())
            mo = re.search(r"CREATE TABLE IF NOT EXISTS (\w+)", s)
            if mo: need_t.append((ver, mo.group(1)))
            mo = re.search(r"ALTER TABLE (\w+) ADD COLUMN IF NOT EXISTS (\w+) ([^,]+)", s)
            if mo: need_c.append((ver, mo.group(1), mo.group(2), mo.group(3)))
            mo = re.search(r"CREATE INDEX IF NOT EXISTS (\w+)", s)
            if mo: need_i.append((ver, mo.group(1)))
    check("миграции разобраны", len(need_t) >= 4 and len(need_c) >= 12 and len(need_i) >= 6,
          (len(need_t), len(need_c), len(need_i)))
    for ver, t in need_t:
        check("схема содержит таблицу %s (миграция %d)" % (t, ver), t in tables)
    for ver, t, c, defn in need_c:
        have = tables.get(t, {}).get(c)
        check("схема содержит колонку %s.%s (миграция %d)" % (t, c, ver), have is not None)
        if have is not None:
            check("тип %s.%s совпадает с миграцией" % (t, c), type_of(have) == type_of(defn),
                  (have, defn))
    for ver, i in need_i:
        check("схема содержит индекс %s (миграция %d)" % (i, ver), i in indexes)
    init = open(SERVER, encoding="utf-8").read()
    init = init[init.index("class DB:"):]; init = init[:init.index("    def get(")]
    check("сходимость схемы при инициализации (DDL миграций применяется всегда)",
          "for ver in sorted(MIGRATIONS)" in init and 'startswith(("CREATE", "ALTER"))' in init)

    print("== B. Слой объектов ENT / STATE_KEYS ==")
    for kind, (table, fields) in srv.ENT.items():
        check("ENT %s → таблица %s в схеме" % (kind, table), table in tables)
        for js, col, ty in fields:
            c = col.strip('"')
            check("ENT %s.%s есть в схеме" % (kind, js), c in tables.get(table, {}), c)
    for kind, coll in srv.STATE_KEYS.items():
        check("STATE_KEYS %s → объект ENT" % kind, kind in srv.ENT)
        check("STATE_KEYS %s → коллекция %s не пуста" % (kind, coll), bool(coll))

    print("== C. Резервное копирование и восстановление ==")
    tech = {"meta", "sessions"}
    for t in srv.BACKUP_TABLES:
        check("копия: таблица %s есть в схеме" % t, t in tables)
    for t in sorted(set(tables) - tech):
        check("копия покрывает таблицу %s" % t, t in srv.BACKUP_TABLES)
    src = open(SERVER, encoding="utf-8").read()
    body = src[src.index("def restore_backup("):]
    body = body[:body.index("\ndef ")]
    ins_tables = set(re.findall(r'for t in \[([^\]]+)\]:\s*\n\s*for row in', body))
    listed = set()
    for grp in ins_tables:
        listed |= set(re.findall(r'"(\w+)"', grp))
    listed |= set(re.findall(r'for row in tables\["(\w+)"\]', body))
    listed |= set(re.findall(r'pending = list\(tables\["(\w+)"\]\)', body))
    for t in srv.BACKUP_TABLES:
        check("восстановление вставляет таблицу %s" % t, t in listed, sorted(listed))
    check("восстановление: обязательные таблицы — подмножество копии",
          all(t in srv.BACKUP_TABLES for t in srv.BACKUP_REQUIRED))

    if a.no_live:
        print("\nИТОГ: %d ok, %d FAIL, %d пропуск" % (ok, fail, skip))
        sys.exit(1 if fail else 0)

    # ---------------------------------------------------------- живые проверки D–F
    import psycopg2
    import psycopg2.extras
    dsn = a.dsn or srv.load_config().get("dsn", "")
    if not dsn:
        skipped("D–F: живые проверки", "не задан DSN")
        print("\nИТОГ: %d ok, %d FAIL, %d пропуск" % (ok, fail, skip))
        sys.exit(1 if fail else 0)
    kw = dict(p.split("=", 1) for p in dsn.split() if "=" in p)
    base_db = kw.get("dbname", "supip")
    tmp_db = "supip_schema_test_%d" % (int(time.time()) % 100000)
    adm = psycopg2.connect(dsn)
    adm.autocommit = True
    try:
        with adm.cursor() as cur:
            try:
                cur.execute('CREATE DATABASE "%s"' % tmp_db)
            except psycopg2.errors.InsufficientPrivilege:
                skipped("D–F: живые проверки", "нет права CREATE DATABASE")
                print("\nИТОГ: %d ok, %d FAIL, %d пропуск" % (ok, fail, skip))
                sys.exit(1 if fail else 0)
    except Exception:
        adm.close()
        raise
    kw["dbname"] = tmp_db
    tmp_dsn = " ".join("%s=%s" % (k, v) for k, v in kw.items())

    def cols_of(db, table):
        conn = db.get()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT column_name FROM information_schema.columns "
                            "WHERE table_name=%s", (table,))
                return {r[0] for r in cur.fetchall()}
        finally:
            db.put(conn)

    def scalar(db, sql, args=None):
        conn = db.get()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, args) if args else cur.execute(sql)
                r = cur.fetchone()
                return r[0] if r else None
        finally:
            db.put(conn)

    def exec_sql(db, *stmts):
        conn = db.get()
        try:
            with conn.cursor() as cur:
                for s in stmts: cur.execute(s)
            conn.commit()
        finally:
            db.put(conn)

    db = None
    try:
        print("== D. Инициализация НОВОЙ базы ==")
        db = srv.DB(tmp_dsn)
        check("новая база: метка версии схемы", str(scalar(db, "SELECT v FROM meta WHERE k='schema'")) == str(srv.SCHEMA_VERSION))
        check("новая база: миграции не требуются", srv.migrate(db) == [])
        mc = cols_of(db, "meetings")
        for c in ("requisites", "protocol_draft", "participants", "actual_composition", "protocol", "materials"):
            check("новая база: meetings.%s" % c, c in mc)
        check("новая база: таблица templates",
              scalar(db, "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='templates'") == 1)
        for t in sorted(tables):
            n = scalar(db, "SELECT COUNT(*) FROM information_schema.tables WHERE table_name=%s", (t,))
            check("новая база: таблица %s создана" % t, n == 1)
        seeded = srv.seed(db)
        check("новая база: эталонный набор заполнен", seeded is not False)
        check("новая база: пользователи", scalar(db, "SELECT COUNT(*) FROM users") >= 8)
        check("новая база: совещания", scalar(db, "SELECT COUNT(*) FROM meetings") >= 1)
        check("новая база: сохранение совещания с реквизитами",
              _save_meeting(srv, db) is True)
        srv.ensure_base_templates(db)
        check("новая база: базовые шаблоны §2.8.1", scalar(db, "SELECT COUNT(*) FROM templates") == len(srv.base_templates()))
        check("новая база: шаблон по умолчанию",
              scalar(db, "SELECT COUNT(*) FROM templates WHERE is_default") == 1)
        check("новая база: повторное заполнение идемпотентно", srv.seed(db) is False)
        check("новая база: повторное создание шаблонов идемпотентно", srv.ensure_base_templates(db) == 0)

        print("== E. Сходимость схемы (база с опережающей меткой версии) ==")
        exec_sql(db, "ALTER TABLE meetings DROP COLUMN requisites",
                 "ALTER TABLE meetings DROP COLUMN protocol_draft",
                 "DROP TABLE templates")
        db.pool.closeall()
        db = srv.DB(tmp_dsn)          # имитация следующего запуска сервера
        mc = cols_of(db, "meetings")
        check("сходимость: meetings.requisites восстановлена", "requisites" in mc)
        check("сходимость: meetings.protocol_draft восстановлена", "protocol_draft" in mc)
        check("сходимость: таблица templates восстановлена",
              scalar(db, "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='templates'") == 1)
        check("сходимость: сохранение совещания работает", _save_meeting(srv, db) is True)
        check("сходимость: эталонный набор не дублируется", srv.seed(db) is False)

        print("== F. Обновление прежней версии (метка 6 → 8) ==")
        exec_sql(db, "ALTER TABLE meetings DROP COLUMN requisites",
                 "ALTER TABLE meetings DROP COLUMN protocol_draft",
                 "ALTER TABLE meetings DROP COLUMN run",
                 "DROP TABLE templates",
                 "UPDATE meta SET v='6' WHERE k='schema'")
        db.pool.closeall()
        db = srv.DB(tmp_dsn)
        applied = srv.migrate(db)
        check("обновление: миграции 7 и 8 применены по порядку", applied == [7, 8], applied)
        check("обновление: метка версии схемы обновлена",
              str(scalar(db, "SELECT v FROM meta WHERE k='schema'")) == str(srv.SCHEMA_VERSION))
        mc = cols_of(db, "meetings")
        check("обновление: колонки реквизитов и черновика", "requisites" in mc and "protocol_draft" in mc)
        check("обновление: колонка хода проведения (миграция 8)", "run" in mc, sorted(mc))
        check("обновление: журнал TECH о миграциях",
              scalar(db, "SELECT COUNT(*) FROM events WHERE cat='TECH' AND action LIKE '%Миграции%'") >= 1)
        check("обновление: повторный запуск без миграций", srv.migrate(db) == [])
        check("обновление: данные сохранены", scalar(db, "SELECT COUNT(*) FROM users") >= 8)

        print("== G. Обновление с версии 1.14.x (метка 7 → 8) ==")
        exec_sql(db, "ALTER TABLE meetings DROP COLUMN run", "UPDATE meta SET v='7' WHERE k='schema'")
        db.pool.closeall()
        db = srv.DB(tmp_dsn)
        applied = srv.migrate(db)
        check("обновление: применена только миграция 8", applied == [8], applied)
        check("обновление: ход проведения восстановлен", "run" in cols_of(db, "meetings"))
        check("обновление: таблица шаблонов протокола не затронута",
              scalar(db, "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='templates'") == 1)
        check("обновление: сохранение хода проведения работает",
              scalar(db, "SELECT COUNT(*) FROM meetings WHERE run IS NOT NULL") >= 1)
        check("обновление: метка версии схемы — 8",
              str(scalar(db, "SELECT v FROM meta WHERE k='schema'")) == "8")
    finally:
        try:
            if db is not None: db.pool.closeall()
        except Exception: pass
        with adm.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s", (tmp_db,))
            cur.execute('DROP DATABASE IF EXISTS "%s"' % tmp_db)
        adm.close()
        print("  (временная база %s удалена)" % tmp_db)

    print("\nИТОГ: %d ok, %d FAIL, %d пропуск" % (ok, fail, skip))
    sys.exit(1 if fail else 0)


def _save_meeting(srv, db):
    """Сохранение совещания через слой объектов — то, что падало на чистой базе."""
    conn = db.get()
    try:
        with conn.cursor(cursor_factory=srv.psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM meetings ORDER BY id LIMIT 1")
            row = cur.fetchone()
            if not row: return False
            m = db.row2obj("meeting", row)
            m["requisites"] = {"number": "П-ТЕСТ-1", "org": "Инженерное подразделение"}
            m["draft"] = {"sections": [], "conclusion": ""}
            db.save_ent(cur, "meeting", m)
            cur.execute("SELECT requisites FROM meetings WHERE id=%s", (m["id"],))
            got = cur.fetchone()["requisites"]
        conn.commit()
        return (got or {}).get("number") == "П-ТЕСТ-1"
    except Exception as e:
        conn.rollback()
        print("      " + repr(e))
        return False
    finally:
        db.put(conn)


if __name__ == "__main__":
    main()