#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Релиз-операция СУП-ИП: новая версия → README в versions/ → CHANGELOG → zip → commit+push+tag.
   Использование: python3 tools/release.py 1.1.0 "описание операции" [--no-tag]"""
import io, os, re, subprocess, sys, zipfile, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def run(*cmd, **kw):
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        print(r.stdout, r.stderr); sys.exit("Команда не выполнена: " + " ".join(cmd))
    return r.stdout.strip()

def main():
    if len(sys.argv) < 3: sys.exit(__doc__)
    ver, desc = sys.argv[1], sys.argv[2]
    no_tag = "--no-tag" in sys.argv
    assert re.match(r"^\d+\.\d+\.\d+$", ver), "версия X.Y.Z"
    today = datetime.date.today().isoformat()

    # 1. VERSION в сервере
    p = os.path.join(ROOT, "sup-ip", "server.py")
    s = io.open(p, encoding="utf-8").read()
    s2 = re.sub(r'VERSION = "\d+\.\d+\.\d+"', 'VERSION = "%s"' % ver, s, count=1)
    assert s2 != s or ('VERSION = "%s"' % ver) in s
    io.open(p, "w", encoding="utf-8").write(s2)

    # 2. README (корневой — канонический) + копия в sup-ip/
    rp = os.path.join(ROOT, "README.md")
    r = io.open(rp, encoding="utf-8").read()
    r = re.sub(r"\*\*Версия \d+\.\d+\.\d+", "**Версия " + ver, r, count=1)
    io.open(rp, "w", encoding="utf-8").write(r)
    io.open(os.path.join(ROOT, "sup-ip", "README.md"), "w", encoding="utf-8").write(r)

    # 3. Копия README за новым номером версии
    io.open(os.path.join(ROOT, "versions", "README-v%s.md" % ver), "w", encoding="utf-8").write(r)

    # 4. CHANGELOG
    cp = os.path.join(ROOT, "CHANGELOG.md")
    c = io.open(cp, encoding="utf-8").read()
    entry = "\n## v%s — %s\n- %s\n" % (ver, today, desc)
    i = c.index("\n## v")
    io.open(cp, "w", encoding="utf-8").write(c[:i] + entry + c[i:])

    # 5. Архив поставки
    zp = os.path.join(ROOT, "SUP-IP_server.zip")
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(os.path.join(ROOT, "sup-ip")):
            dirs[:] = [d for d in dirs if d not in ("data", "__pycache__", "dist", "build", "node_modules")]
            for f in files:
                if f == "postgresql-setup.exe":   # офлайн-пакет СУБД (~350 МБ) — только в Release
                    continue
                fp = os.path.join(root, f)
                z.write(fp, os.path.relpath(fp, ROOT))

    # 6. git: commit + push (+tag)
    run("git", "add", "-A")
    run("git", "commit", "-m", "v%s: %s" % (ver, desc))
    run("git", "push", "origin", "main")
    if not no_tag:
        run("git", "tag", "-a", "v" + ver, "-m", desc)
        run("git", "push", "origin", "v" + ver)
    print("Релиз v%s запушен: README → versions/README-v%s.md, CHANGELOG, zip, tag" % (ver, ver))

if __name__ == "__main__":
    main()
