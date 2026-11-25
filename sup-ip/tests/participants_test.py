#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
СУП-ИП. Испытания участников совещания (ТТ-2, M16 §2.7, экранные требования MTG-05).
Запуск при работающем сервере:  python3 tests/participants_test.py --base http://127.0.0.1:8000

Проверяется полный состав §2.7: выбор сотрудников из единого справочника (ФИО, должность,
подразделение), роль на совещании, обязательность, статусы приглашения (Не направлено,
Направлено, Подтверждено, Отклонено, Требует уточнения), ответы участников, фактическое участие
(Присутствовал, Отсутствовал, Подключился дистанционно, Участвовал частично, Не определено),
регистрация фактического состава после проведения, массовые операции с подтверждением,
исключение из состава, права ролей, уведомления, аудит, протокол DOCX/HTML, резервная копия
и совместимость с записями прежних версий. Только стандартная библиотека Python.
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
    p = {"title": "Совещание §2.7", "type": "Оперативное", "date": D(0), "time": "10:00",
         "chair": "u2", "secretary": "u3", "attendees": ["u2", "u3", "u5"]}
    p.update(kw)
    return cli.res("meeting.create", p)


def parts(cli, mid):
    r, c, j = cli.res("meeting.participants.list", {"id": mid})
    return (r or {}).get("items") or []


def part(cli, mid, user_id):
    return next((x for x in parts(cli, mid) if x["userId"] == user_id), None)


def counts(cli, mid):
    r, c, j = cli.res("meeting.participants.list", {"id": mid})
    return (r or {}).get("counts") or {}


def main():
    global BASE
    ap = argparse.ArgumentParser(); ap.add_argument("--base", default=BASE)
    BASE = ap.parse_args().base.rstrip("/")

    spec = importlib.util.spec_from_file_location("supip_parts_test", SERVER)
    srv = importlib.util.module_from_spec(spec); spec.loader.exec_module(srv)

    adm = Client()
    c, j = adm.login("admin")
    check("вход администратора", c == 200 and j.get("ok"), c)
    adm.rpc("admin.reset")
    st = adm.state()
    users = {x["id"]: x for x in st["users"]}

    print("== A. Состав участников при создании совещания (§2.7) ==")
    m, c, j = meet(adm, title="Технический совет §2.7")
    mid = (m or {}).get("id")
    check("совещание создано", c == 200 and bool(mid), (c, j))
    its = parts(adm, mid)
    check("председатель и секретарь включены в состав", [x["userId"] for x in its][:2] == ["u2", "u3"],
          [x["userId"] for x in its])
    check("состав совпадает с приглашёнными", [x["userId"] for x in its] == ["u2", "u3", "u5"], [x["userId"] for x in its])
    check("роли председателя и секретаря проставлены",
          its[0]["role"] == "Председатель" and its[1]["role"] == "Секретарь", [x["role"] for x in its])
    check("прочие участники получают роль «Участник»", its[2]["role"] == "Участник", its[2]["role"])
    check("председатель и секретарь — обязательные участники", its[0]["required"] and its[1]["required"])
    check("приглашения при создании направлены", all(x["invite"] == "Направлено" for x in its), [x["invite"] for x in its])
    check("дата и автор направления приглашения зафиксированы",
          all(x["invitedAt"] and x["invitedBy"] == "u1" for x in its))
    check("фактическое участие изначально не определено",
          all(x["participation"] == "Не определено" for x in its), [x["participation"] for x in its])
    check("ФИО, должность и подразделение берутся из справочника",
          its[0]["name"] == users["u2"]["name"] and its[0]["position"] == users["u2"]["position"]
          and its[0]["department"] == users["u2"]["department"], its[0])
    check("идентификаторы участников самостоятельные", len({x["id"] for x in its}) == 3)
    check("история участника заведена", its[0]["history"][0]["action"] == "add", its[0]["history"])
    cnt = counts(adm, mid)
    check("сводка по приглашениям", cnt["invite"]["Направлено"] == 3, cnt["invite"])
    check("сводка по участию", cnt["participation"]["Не определено"] == 3 and cnt["present"] == 0, cnt["participation"])
    check("счётчик обязательных участников", cnt["required"] == 2, cnt["required"])
    r, c, j = adm.res("meeting.participants.list", {"id": mid})
    check("справочники статусов передаются клиенту",
          r["roles"][:2] == ["Председатель", "Секретарь"] and len(r["inviteStates"]) == 5 and len(r["participationStates"]) == 5,
          (r["roles"], r["inviteStates"], r["participationStates"]))
    check("признак возможности отмечать участие", r["canAttendance"] is True, r["canAttendance"])

    print("== B. Единый справочник сотрудников (§2.7, M05 §3.5) ==")
    check("подразделение есть у всех сотрудников", all(x.get("department") for x in st["users"]),
          [x.get("department") for x in st["users"]])
    r, c, j = adm.res("admin.user.profile", {"id": "u7", "department": "Группа испытаний и измерений",
                                             "position": "Ведущий инженер-испытатель"})
    check("сведения о сотруднике изменены", c == 200, (c, j))
    u7 = next(x for x in adm.state()["users"] if x["id"] == "u7")
    check("подразделение и должность сохранены",
          u7["department"] == "Группа испытаний и измерений" and u7["position"] == "Ведущий инженер-испытатель", u7)
    c, msg = adm.err("admin.user.profile", {"id": "u7", "department": "Группа испытаний и измерений",
                                            "position": "Ведущий инженер-испытатель"})
    check("повторное сохранение без изменений отклонено", c == 400 and "не изменились" in msg, (c, msg))
    c, msg = adm.err("admin.user.profile", {"id": "u7", "name": "   "})
    check("пустое ФИО отклонено", c == 400 and "ФИО" in msg, (c, msg))
    c, msg = adm.err("admin.user.profile", {"id": "u_none", "department": "X"})
    check("несуществующий сотрудник отклонён", c == 404, (c, msg))
    ev = [e for e in adm.state()["events"] if (e.get("details") or {}).get("event") == "USER_PROFILE_UPDATED"]
    check("изменение справочника попало в журнал", bool(ev), len(ev))
    r, c, j = adm.res("admin.user.create", {"name": "Тестов Сотрудник", "login": "testov", "role": "exec",
                                            "position": "Инженер", "department": "Группа испытаний"})
    check("сотрудник создан с подразделением", c == 200, (c, j))
    nu = next((x for x in adm.state()["users"] if x["login"] == "testov"), None)
    check("подразделение нового сотрудника сохранено", nu and nu["department"] == "Группа испытаний", nu)
    new_id = nu["id"] if nu else ""

    print("== C. Добавление участников из справочника (MTG-05 8.4) ==")
    r, c, j = adm.res("meeting.participants.add", {"id": mid, "userId": "u6", "role": "Докладчик",
                                                   "required": True, "comment": "Доклад по расчёту бюджета"})
    check("участник добавлен", c == 200 and len(parts(adm, mid)) == 4, (c, j))
    p6 = part(adm, mid, "u6")
    check("роль, обязательность и комментарий сохранены",
          p6["role"] == "Докладчик" and p6["required"] and p6["comment"] == "Доклад по расчёту бюджета", p6)
    check("добавленному участнику приглашение ещё не направлено", p6["invite"] == "Не направлено", p6["invite"])
    check("участник появился в составе совещания", "u6" in adm.state()["meetings"][[x["id"] for x in adm.state()["meetings"]].index(mid)]["attendees"])
    rem = [x for x in adm.state()["reminders"] if x["owner_id"] == mid and x["user_id"] == "u6"]
    check("напоминания новому участнику настроены", len(rem) == 2, len(rem))
    r, c, j = adm.res("meeting.participants.add", {"id": mid, "userIds": ["u4", "u7"], "role": "Эксперт"})
    check("несколько участников добавлены одной операцией", c == 200 and len(parts(adm, mid)) == 6, (c, j))
    check("роль применена ко всем добавленным",
          part(adm, mid, "u4")["role"] == "Эксперт" and part(adm, mid, "u7")["role"] == "Эксперт")
    c, msg = adm.err("meeting.participants.add", {"id": mid, "userId": "u6"})
    check("повторное добавление отклонено", c == 400 and "уже включён" in msg, (c, msg))
    c, msg = adm.err("meeting.participants.add", {"id": mid, "userId": "u_none"})
    check("несуществующий сотрудник отклонён", c == 404, (c, msg))
    c, msg = adm.err("meeting.participants.add", {"id": mid, "userId": "u8", "role": "Наблюдательный"})
    check("неизвестная роль отклонена", c == 400 and "роль на совещании" in msg, (c, msg))
    c, msg = adm.err("meeting.participants.add", {"id": mid, "userId": "u8", "role": "Председатель"})
    check("роль председателя задаётся полем совещания", c == 400 and "задаётся полем" in msg, (c, msg))
    c, msg = adm.err("meeting.participants.add", {"id": mid})
    check("без выбора сотрудника операция отклонена", c == 400 and "справочник" in msg, (c, msg))
    adm.rpc("admin.user.block", {"id": new_id})
    c, msg = adm.err("meeting.participants.add", {"id": mid, "userId": new_id})
    check("заблокированный сотрудник не добавляется", c == 400 and "заблокирован" in msg, (c, msg))
    adm.rpc("admin.user.block", {"id": new_id})
    ev = [e for e in adm.state()["events"] if (e.get("details") or {}).get("event") == "MEETING_PARTICIPANT_ADDED"]
    check("добавление участников попало в журнал", bool(ev), len(ev))

    print("== D. Роль, обязательность и комментарий участника ==")
    r, c, j = adm.res("meeting.participants.update", {"id": mid, "userId": "u4", "role": "Наблюдатель",
                                                      "comment": "Присутствует без права голоса"})
    check("роль участника изменена", c == 200 and part(adm, mid, "u4")["role"] == "Наблюдатель", (c, j))
    check("комментарий сохранён", part(adm, mid, "u4")["comment"] == "Присутствует без права голоса")
    r, c, j = adm.res("meeting.participants.update", {"id": mid, "userId": "u7", "required": True})
    check("обязательность участия изменена", c == 200 and part(adm, mid, "u7")["required"], (c, j))
    c, msg = adm.err("meeting.participants.update", {"id": mid, "userId": "u2", "role": "Участник"})
    check("роль председателя не переопределяется", c == 400 and "карточке совещания" in msg, (c, msg))
    c, msg = adm.err("meeting.participants.update", {"id": mid, "userId": "u3", "required": False})
    check("секретарь остаётся обязательным участником", c == 400 and "обязательные" in msg, (c, msg))
    c, msg = adm.err("meeting.participants.update", {"id": mid, "userId": "u4", "role": "Наблюдатель",
                                                     "comment": "Присутствует без права голоса"})
    check("повторное сохранение без изменений отклонено", c == 400 and "не изменились" in msg, (c, msg))
    c, msg = adm.err("meeting.participants.update", {"id": mid, "userId": "u_none"})
    check("несуществующий участник отклонён", c == 404, (c, msg))
    c, msg = adm.err("meeting.participants.update", {"id": mid})
    check("без указания участника операция отклонена", c == 400, (c, msg))
    p4 = part(adm, mid, "u4")
    check("изменения записаны в историю участника",
          any(x["action"] == "edit" for x in p4["history"]), p4["history"])

    print("== E. Направление приглашений (MTG-05 8.2) ==")
    r, c, j = adm.res("meeting.participants.invite", {"id": mid, "userIds": ["u6"], "note": "Подготовьте расчёт к совещанию"})
    check("приглашение направлено выбранному участнику", c == 200 and r["sent"] == 1, (c, r if c == 200 else j))
    p6 = part(adm, mid, "u6")
    check("статус приглашения — «Направлено»", p6["invite"] == "Направлено", p6["invite"])
    check("примечание к приглашению сохранено", p6["responseNote"] == "Подготовьте расчёт к совещанию", p6)
    koz = Client(); koz.login("kozlova")
    ntf = [n for n in koz.state()["notifications"] if n["type"] == "meeting.invite"]
    check("участнику доставлено уведомление о приглашении", bool(ntf), len(ntf))
    r, c, j = adm.res("meeting.participants.invite", {"id": mid, "all": True})
    check("приглашения направлены всему составу", c == 200 and r["sent"] == 6, (c, r if c == 200 else j))
    check("все приглашения в статусе «Направлено»",
          all(x["invite"] == "Направлено" for x in parts(adm, mid)))
    c, msg = adm.err("meeting.participants.invite", {"id": mid, "userIds": ["u_none"]})
    check("несуществующий участник отклонён", c == 404, (c, msg))
    c, msg = adm.err("meeting.participants.invite", {"id": mid})
    check("без выборки участников операция отклонена", c == 400, (c, msg))
    fut, c, j = meet(adm, title="Будущее совещание", date=D(5))
    ev = [e for e in adm.state()["events"] if (e.get("details") or {}).get("event") == "MEETING_PARTICIPANTS_INVITED"]
    check("направление приглашений попало в журнал", bool(ev) and ev[0]["details"]["count"] >= 1, ev[0]["details"] if ev else None)

    print("== F. Подтверждение участия и ответы на приглашение ==")
    r, c, j = koz.res("meeting.participants.respond", {"id": mid, "status": "Подтверждено", "note": "Буду лично"})
    check("участник подтвердил участие самостоятельно", c == 200, (c, j))
    p6 = part(adm, mid, "u6")
    check("статус приглашения — «Подтверждено»", p6["invite"] == "Подтверждено", p6["invite"])
    check("дата ответа и примечание зафиксированы", p6["respondedAt"] and p6["responseNote"] == "Буду лично", p6)
    check("подтверждение не подменяет фактическое участие", p6["participation"] == "Не определено", p6["participation"])
    chair = Client(); chair.login("morozov")
    ntf = [n for n in chair.state()["notifications"] if n["type"] == "meeting.invite.response"]
    check("председатель оповещён об ответе", bool(ntf), len(ntf))
    nov = Client(); nov.login("novikov")
    r, c, j = nov.res("meeting.participants.respond", {"id": mid, "status": "Отклонено", "note": "Командировка"})
    check("участник отклонил приглашение", c == 200 and part(adm, mid, "u5")["invite"] == "Отклонено", (c, j))
    r, c, j = nov.res("meeting.participants.respond", {"id": mid, "status": "Требует уточнения", "note": "Уточните время начала"})
    check("ответ «Требует уточнения» зарегистрирован", c == 200 and part(adm, mid, "u5")["invite"] == "Требует уточнения", (c, j))
    c, msg = nov.err("meeting.participants.respond", {"id": mid, "status": "Требует уточнения"})
    check("для уточнения нужно пояснение", c == 400 and "уточнения" in msg, (c, msg))
    c, msg = nov.err("meeting.participants.respond", {"id": mid, "status": "Направлено"})
    check("статус «Направлено» не является ответом", c == 400, (c, msg))
    c, msg = nov.err("meeting.participants.respond", {"id": mid, "status": "Подтверждено", "userId": "u6"})
    check("за другого участника без прав ответить нельзя", c == 403, (c, msg))
    r, c, j = adm.res("meeting.participants.respond", {"id": mid, "userId": "u7", "status": "Подтверждено",
                                                       "note": "Подтвердил по телефону"})
    check("секретарь отметил ответ за участника", c == 200 and part(adm, mid, "u7")["invite"] == "Подтверждено", (c, j))
    c, msg = adm.err("meeting.participants.respond", {"id": mid, "userId": "u8", "status": "Подтверждено"})
    check("ответ за сотрудника вне состава отклонён", c == 404, (c, msg))
    late, c, j = meet(adm, title="Приглашение не направлено", date=D(3))
    adm.res("meeting.participants.add", {"id": late["id"], "userId": "u6"})
    c, msg = adm.err("meeting.participants.respond", {"id": late["id"], "userId": "u6", "status": "Подтверждено"})
    check("ответ без направленного приглашения отклонён", c == 400 and "не направлено" in msg, (c, msg))
    ev = [e for e in adm.state()["events"] if (e.get("details") or {}).get("event") == "MEETING_INVITE_RESPONSE"]
    check("ответы на приглашения попали в журнал", len(ev) >= 3, len(ev))

    print("== G. Фактическое участие (MTG-05 8.3) ==")
    c, msg = adm.err("meeting.participants.attendance", {"id": fut["id"], "userId": "u2", "participation": "Присутствовал"})
    check("до даты совещания участие не отмечается", c == 400 and "после проведения" in msg, (c, msg))
    r, c, j = adm.res("meeting.conduct", {"id": mid})
    check("совещание проведено", c == 200, (c, j))
    check("проведение не считает участников присутствовавшими",
          all(x["participation"] == "Не определено" for x in parts(adm, mid)),
          [x["participation"] for x in parts(adm, mid)])
    koz.res("meeting.participants.list", {"id": mid})
    koz.req("GET", "/api/state")
    check("открытие карточки не отмечает присутствие",
          part(adm, mid, "u6")["participation"] == "Не определено", part(adm, mid, "u6")["participation"])
    for uid_, stt in (("u2", "Присутствовал"), ("u3", "Присутствовал"), ("u6", "Подключился дистанционно"),
                      ("u7", "Участвовал частично"), ("u5", "Отсутствовал")):
        r, c, j = adm.res("meeting.participants.attendance", {"id": mid, "userId": uid_, "participation": stt})
        check("отмечено участие %s: %s" % (uid_, stt), c == 200 and part(adm, mid, uid_)["participation"] == stt, (c, j))
    p2 = part(adm, mid, "u2")
    check("дата и автор отметки участия зафиксированы", p2["participationAt"] and p2["participationBy"] == "u1", p2)
    cnt = counts(adm, mid)
    check("сводка фактического участия", cnt["present"] == 4 and cnt["participation"]["Отсутствовал"] == 1, cnt)
    c, msg = adm.err("meeting.participants.attendance", {"id": mid, "userId": "u2", "participation": "Присутствовал"})
    check("повторная отметка того же статуса отклонена", c == 400 and "уже отмечен" in msg, (c, msg))
    c, msg = adm.err("meeting.participants.attendance", {"id": mid, "userId": "u2", "participation": "Пришёл"})
    check("неизвестный статус участия отклонён", c == 400 and "статус участия" in msg, (c, msg))
    r, c, j = adm.res("meeting.participants.attendance", {"id": mid, "userId": "u5", "participation": "Отсутствовал",
                                                          "comment": "Командировка на объект"})
    check("комментарий к участию сохранён", c == 200 and part(adm, mid, "u5")["comment"] == "Командировка на объект", (c, j))
    r, c, j = adm.res("meeting.participants.attendance", {"id": mid, "userId": "u7", "participation": "Не определено"})
    check("участие можно вернуть в «Не определено»", c == 200 and part(adm, mid, "u7")["participation"] == "Не определено", (c, j))
    r, c, j = adm.res("meeting.participants.attendance", {"id": mid, "userId": "u7", "participation": "Участвовал частично"})
    check("участие отмечено повторно после уточнения", c == 200, (c, j))
    ev = [e for e in adm.state()["events"] if (e.get("details") or {}).get("event") == "MEETING_PARTICIPATION"]
    check("отметки участия попали в журнал", len(ev) >= 5, len(ev))

    print("== H. Массовые операции с подтверждением (MTG-05 8.4) ==")
    bulk_m, c, j = meet(adm, title="Массовые операции §2.7", date=D(0))
    bid = bulk_m["id"]
    adm.res("meeting.participants.add", {"id": bid, "userIds": ["u4", "u6", "u7"]})
    c, msg = adm.err("meeting.participants.bulk", {"id": bid, "all": True, "invite": "Направлено"})
    check("массовая операция требует подтверждения", c == 409 and "подтверждения" in msg, (c, msg))
    r, c, j = adm.res("meeting.participants.bulk", {"id": bid, "all": True, "invite": "Направлено", "confirm": True})
    check("массовое направление приглашений выполнено", c == 200 and r["changed"] == 3, (c, r if c == 200 else j))
    check("уже приглашённые повторно не оповещаются", r["changed"] == 3, r.get("changed"))
    check("приглашения получили все участники", all(x["invite"] == "Направлено" for x in parts(adm, bid)))
    check("уведомления о приглашениях доставлены", r["delivered"] == 3, r.get("delivered"))
    adm.res("meeting.conduct", {"id": bid})
    r, c, j = adm.res("meeting.participants.bulk", {"id": bid, "filter": "Обязательные",
                                                    "participation": "Присутствовал", "confirm": True})
    check("массовая отметка по условию отбора", c == 200 and r["changed"] == 2, (c, r if c == 200 else j))
    check("обязательные участники отмечены присутствовавшими",
          part(adm, bid, "u2")["participation"] == "Присутствовал" and part(adm, bid, "u3")["participation"] == "Присутствовал")
    r, c, j = adm.res("meeting.participants.bulk", {"id": bid, "userIds": ["u4", "u6", "u7"],
                                                    "participation": "Отсутствовал", "confirm": True,
                                                    "comment": "Не смогли подключиться"})
    check("массовая отметка выбранных участников", c == 200 and r["changed"] == 3, (c, r if c == 200 else j))
    check("комментарий применён ко всем выбранным",
          all(part(adm, bid, x)["comment"] == "Не смогли подключиться" for x in ("u4", "u6", "u7")))
    c, msg = adm.err("meeting.participants.bulk", {"id": bid, "userIds": ["u4"], "participation": "Отсутствовал", "confirm": True})
    check("повторная массовая отметка того же статуса отклонена", c == 400 and "уже в статусе" in msg, (c, msg))
    c, msg = adm.err("meeting.participants.bulk", {"id": bid, "all": True, "confirm": True})
    check("без указания нового статуса операция отклонена", c == 400, (c, msg))
    c, msg = adm.err("meeting.participants.bulk", {"id": bid, "all": True, "invite": "Направлено",
                                                   "participation": "Присутствовал", "confirm": True})
    check("одновременно два статуса не задаются", c == 400, (c, msg))
    c, msg = adm.err("meeting.participants.bulk", {"id": bid, "all": True, "participation": "Был", "confirm": True})
    check("неизвестный статус участия отклонён", c == 400, (c, msg))
    c, msg = adm.err("meeting.participants.bulk", {"id": bid, "all": True, "invite": "Подтверждено", "confirm": True})
    check("массово подтверждать за участников нельзя", c == 400 and "сам участник" in msg, (c, msg))
    c, msg = adm.err("meeting.participants.bulk", {"id": bid, "all": True, "invite": "Не направлено", "confirm": True})
    check("массовый отзыв приглашений запрещён", c == 400, (c, msg))
    c, msg = adm.err("meeting.participants.bulk", {"id": bid, "filter": "Нет такого", "participation": "Присутствовал", "confirm": True})
    check("пустой отбор отклонён", c == 400 and "не найдено" in msg, (c, msg))
    ev = [e for e in adm.state()["events"] if (e.get("details") or {}).get("event") == "MEETING_PARTICIPANTS_BULK"]
    check("массовые операции попали в журнал", len(ev) >= 3, len(ev))

    print("== I. Регистрация фактического состава после проведения (§2.7) ==")
    plan_m, c, j = meet(adm, title="Регистрация состава", date=D(0))
    aid = plan_m["id"]
    c, msg = adm.err("meeting.participants.actual", {"id": aid})
    check("до проведения состав не регистрируется", c == 400 and "после проведения" in msg, (c, msg))
    adm.res("meeting.conduct", {"id": aid})
    c, msg = adm.err("meeting.participants.actual", {"id": aid})
    check("без отметок обязательных участников регистрация отклонена",
          c == 400 and "обязательных участников" in msg, (c, msg))
    check("в отказе перечислены сотрудники", "Морозов" in msg and "Волкова" in msg, msg)
    adm.res("meeting.participants.attendance", {"id": aid, "userId": "u2", "participation": "Присутствовал"})
    adm.res("meeting.participants.attendance", {"id": aid, "userId": "u3", "participation": "Подключился дистанционно"})
    adm.res("meeting.participants.attendance", {"id": aid, "userId": "u5", "participation": "Отсутствовал",
                                                "comment": "Отпуск"})
    r, c, j = adm.res("meeting.participants.actual", {"id": aid, "note": "Состав зафиксирован секретарём"})
    check("фактический состав зарегистрирован", c == 200 and r["actual"]["total"] == 3, (c, j))
    snap = r["actual"]
    check("в снимке участвовали двое", len(snap["present"]) == 2, snap["present"])
    check("в снимке отмечен отсутствовавший", len(snap["absent"]) == 1 and snap["absent"][0]["userId"] == "u5")
    check("в снимке сохранены ФИО, должность и подразделение",
          snap["present"][0]["name"] and snap["present"][0]["position"] and snap["present"][0]["department"], snap["present"][0])
    check("в снимке указаны роли на совещании",
          [x["role"] for x in snap["all"]] == ["Председатель", "Секретарь", "Участник"], [x["role"] for x in snap["all"]])
    check("автор и примечание регистрации сохранены", snap["by"] == "u1" and snap["note"] == "Состав зафиксирован секретарём")
    r, c, j = adm.res("meeting.participants.list", {"id": aid})
    check("снимок состава доступен в карточке", (r["actual"] or {}).get("total") == 3, r["actual"])
    adm.res("meeting.participants.attendance", {"id": aid, "userId": "u5", "participation": "Присутствовал"})
    r, c, j = adm.res("meeting.participants.list", {"id": aid})
    check("изменение участия сбрасывает устаревший снимок", not (r["actual"] or {}), r["actual"])
    r, c, j = adm.res("meeting.participants.actual", {"id": aid})
    check("состав зарегистрирован повторно", c == 200 and len(r["actual"]["present"]) == 3, (c, j))
    ev = [e for e in adm.state()["events"] if (e.get("details") or {}).get("event") == "MEETING_ACTUAL_COMPOSITION"]
    check("регистрация состава попала в журнал", len(ev) >= 2, len(ev))

    print("== J. Исключение из состава участников ==")
    c, msg = adm.err("meeting.participants.remove", {"id": mid, "userId": "u2"})
    check("председателя исключить нельзя", c == 400 and "Председателя" in msg, (c, msg))
    c, msg = adm.err("meeting.participants.remove", {"id": mid, "userId": "u3"})
    check("секретаря исключить нельзя", c == 400, (c, msg))
    c, msg = adm.err("meeting.participants.remove", {"id": mid, "userId": "u6"})
    check("участника с отмеченным участием исключить нельзя", c == 400 and "Отсутствовал" in msg, (c, msg))
    rem_before = len([x for x in adm.state()["reminders"] if x["owner_id"] == mid])
    r, c, j = adm.res("meeting.participants.remove", {"id": mid, "userId": "u4"})
    check("участник без отметки исключён", c == 200 and part(adm, mid, "u4") is None, (c, j))
    rem_after = len([x for x in adm.state()["reminders"] if x["owner_id"] == mid])
    check("напоминания исключённому участнику сняты", rem_after == rem_before - 2, (rem_before, rem_after))
    check("состав совещания обновлён", "u4" not in next(x for x in adm.state()["meetings"] if x["id"] == mid)["attendees"])
    c, msg = adm.err("meeting.participants.remove", {"id": mid, "userId": "u4"})
    check("повторное исключение отклонено", c == 404, (c, msg))
    ev = [e for e in adm.state()["events"] if (e.get("details") or {}).get("event") == "MEETING_PARTICIPANT_REMOVED"]
    check("исключение попало в журнал", bool(ev), len(ev))

    print("== K. Права ролей и отменённое совещание ==")
    obs = Client(); obs.login("zaytseva")
    r, c, j = obs.res("meeting.participants.list", {"id": mid})
    check("наблюдатель видит состав участников", c == 200 and len(r["items"]) >= 5, c)
    for act, payload in (("meeting.participants.add", {"id": mid, "userId": "u8"}),
                         ("meeting.participants.update", {"id": mid, "userId": "u5", "role": "Эксперт"}),
                         ("meeting.participants.remove", {"id": mid, "userId": "u5"}),
                         ("meeting.participants.invite", {"id": mid, "all": True}),
                         ("meeting.participants.attendance", {"id": mid, "userId": "u5", "participation": "Присутствовал"}),
                         ("meeting.participants.bulk", {"id": mid, "all": True, "participation": "Присутствовал", "confirm": True}),
                         ("meeting.participants.actual", {"id": mid})):
        c, msg = obs.err(act, payload)
        check("наблюдателю отказано: %s" % act.split(".")[-1], c == 403, (c, msg))
    c, msg = nov.err("meeting.participants.add", {"id": mid, "userId": "u8"})
    check("исполнителю отказано в изменении состава", c == 403, (c, msg))
    c, msg = nov.err("meeting.participants.attendance", {"id": mid, "userId": "u5", "participation": "Присутствовал"})
    check("исполнитель не отмечает участие за себя", c == 403, (c, msg))
    canc, c, j = meet(adm, title="Отменённое совещание §2.7", date=D(2))
    adm.res("meeting.cancel", {"id": canc["id"], "reason": "Отпуск председателя"})
    for act, payload in (("meeting.participants.add", {"id": canc["id"], "userId": "u6"}),
                         ("meeting.participants.invite", {"id": canc["id"], "all": True}),
                         ("meeting.participants.attendance", {"id": canc["id"], "userId": "u2", "participation": "Присутствовал"})):
        c, msg = adm.err(act, payload)
        check("отменённое совещание: отказ %s" % act.split(".")[-1], c == 400, (c, msg))
    done, c, j = meet(adm, title="Проведённое для приглашений", date=D(-1))
    adm.res("meeting.conduct", {"id": done["id"]})
    c, msg = adm.err("meeting.participants.invite", {"id": done["id"], "all": True})
    check("на проведённое совещание приглашения не направляются", c == 400 and "запланированное" in msg, (c, msg))

    print("== L. Протокол, выгрузка и сохранность данных ==")
    r, c, j = adm.res("meeting.participants.actual", {"id": mid})
    check("состав технического совета зарегистрирован", c == 200, (c, j))
    c, raw = adm.req("GET", "/api/export?kind=meeting&id=%s&fmt=docx" % mid)
    check("протокол DOCX выгружен", c == 200 and isinstance(raw, bytes) and raw[:2] == b"PK", c)
    txt = ""
    if isinstance(raw, bytes) and raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            txt = z.read("word/document.xml").decode("utf-8")
    check("в протоколе раздел «Присутствовали»", "Присутствовали" in txt)
    check("в протоколе указаны подразделение и роль", "Подразделение" in txt and "Роль на совещании" in txt)
    check("в протоколе указано фактическое участие", "Фактическое участие" in txt and "Подключился дистанционно" in txt)
    check("в протоколе перечислены не участвовавшие", "Приглашены, но не участвовали" in txt)
    check("в протоколе указана регистрация состава", "Фактический состав зарегистрирован" in txt)
    c, raw = adm.req("GET", "/api/export?kind=meeting&id=%s&fmt=html" % mid)
    htxt = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
    check("протокол HTML содержит состав участников", c == 200 and "Подразделение" in htxt, c)
    c, raw = adm.req("GET", "/api/backup")
    bk = json.loads(raw if isinstance(raw, (bytes, str)) else json.dumps(raw))
    rows = (bk.get("tables") or {}).get("meetings") or []
    row = next((x for x in rows if x["id"] == mid), None)
    check("резервная копия содержит состав участников", bool(row) and isinstance(row.get("participants"), list) and len(row["participants"]) >= 5)
    check("в копии сохранён фактический состав", bool(row) and (row.get("actual_composition") or {}).get("total"))
    check("в копии есть подразделение сотрудников",
          all(x.get("department") for x in bk["tables"]["users"]), None)
    c, j = adm.req("POST", "/api/restore", bk)
    check("восстановление из копии выполнено", c == 200, (c, j if c != 200 else ""))
    adm.login("admin")
    its = parts(adm, mid)
    check("после восстановления состав сохранён", len(its) >= 5 and part(adm, mid, "u6")["participation"] == "Подключился дистанционно",
          [x["participation"] for x in its])
    # совместимость: запись прежней версии без поля participants
    bk2 = json.loads(json.dumps(bk))
    row2 = next(x for x in bk2["tables"]["meetings"] if x["id"] == mid)
    row2.pop("participants", None); row2.pop("actual_composition", None)
    c, j = adm.req("POST", "/api/restore", bk2)
    check("копия прежней версии восстановлена", c == 200, (c, j if c != 200 else ""))
    adm.login("admin")
    its = parts(adm, mid)
    check("состав восстановлен из attendees", [x["userId"] for x in its][:2] == ["u2", "u3"] and len(its) >= 5,
          [x["userId"] for x in its])
    check("записи прежней версии дополнены полями §2.7",
          all(x["participation"] == "Не определено" and x["invite"] == "Не направлено" for x in its),
          [(x["invite"], x["participation"]) for x in its])
    check("роли председателя и секретаря восстановлены",
          its[0]["role"] == "Председатель" and its[1]["role"] == "Секретарь", [x["role"] for x in its])

    adm.rpc("admin.reset")
    print("\nИТОГ: %d ok, %d FAIL" % (ok, fail))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
