; СУП-ИП — установщик Windows x64 (ТЗ §7)
; Сборка: makensis installer\setup.nsi  (ожидает sup-ip\dist\SUP-IP.exe)
;
; PostgreSQL: три сценария (страница «PostgreSQL»):
;   1) автоматическая загрузка и тихая установка СУБД (нужен интернет, ~350 МБ);
;   2) офлайн-установщик PostgreSQL с диска/флешки (или включённый в пакет);
;   3) уже установленный локальный либо внешний сервер PostgreSQL в ЛВС.
; Журнал установки пишется всегда: %TEMP%\SUP-IP-Setup.log → $INSTDIR\data\logs\installer.log
Unicode true
ManifestDPIAware true
!ifndef VERSION
  !define VERSION "1.14.0"
!endif
!define APPNAME "SUP-IP"
!define DISPLAY "СУП-ИП — управление проектами"
!define PG_URL "https://get.enterprisedb.com/postgresql/postgresql-16.4-1-windows-x64.exe"
!define PG_VER "16"
!if /FILEEXISTS "postgresql-setup.exe"
  !define PG_BUNDLED
!endif
Name "${DISPLAY} ${VERSION}"
!ifdef PG_BUNDLED
  OutFile "..\dist\SUP-IP-Setup-full-${VERSION}.exe"
  !define DISPLAY_FULL " (с PostgreSQL)"
!else
  OutFile "..\dist\SUP-IP-Setup-${VERSION}.exe"
  !define DISPLAY_FULL ""
!endif
InstallDir "$PROGRAMFILES64\SUP-IP"
InstallDirRegKey HKLM "Software\SUP-IP" "InstallDir"
RequestExecutionLevel admin
SetCompressor /SOLID lzma

!include "MUI2.nsh"
!include "nsDialogs.nsh"
!include "LogicLib.nsh"
!include "WinVer.nsh"
!include "x64.nsh"
!include "FileFunc.nsh"
!insertmacro GetTime
!define MUI_ABORTWARNING
!insertmacro MUI_PAGE_DIRECTORY
Page custom PgPage PgPageLeave
Page custom ConfigPage ConfigPageLeave
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_TEXT "Установка завершена.$\r$\n$\r$\nЖурнал установки: $INSTDIR\data\logs\installer.log$\r$\nОтчёт мастера настройки: $INSTDIR\data\install-report.txt$\r$\nЖурнал настройки: $INSTDIR\data\logs\setup.log$\r$\nЖурнал работы сервера: $INSTDIR\data\logs\supip.log"
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Russian"

Var PgPass
Var Port
Var AdminPass
Var UseHttps
Var PgInstaller      ; путь к офлайн-установщику PostgreSQL (если выбран)
Var PgService        ; имя найденной/созданной службы
Var PgMode           ; auto | file | exist
Var PgHost
Var PgPort
Var PgFound          ; что обнаружено на ПК (для надписи)
Var LogFile
Var LogPath          ; путь журнала (в $LogFile — дескриптор, не путь)
Var PgDlg
Var RbAuto
Var RbFile
Var RbExist
Var TxtFile
Var TxtHost
Var TxtPort
Var Bundled

; ---------- Журнал установки (ТЗ §15: контроль и разбор ошибок) ----------
!macro LOG MSG
  Push $R0
  Push $R1
  Push $R2
  Push $R3
  Push $R4
  Push $R5
  Push $R6
  ${GetTime} "" "L" $R0 $R1 $R2 $R3 $R4 $R5 $R6
  FileSeek $LogFile 0 END
  FileWriteUTF16LE $LogFile "$R2-$R1-$R0 $R4:$R5:$R6  ${MSG}$\r$\n"
  Pop $R6
  Pop $R5
  Pop $R4
  Pop $R3
  Pop $R2
  Pop $R1
  Pop $R0
!macroend

; Выполнение команды с записью в журнал: результат (код возврата) остаётся в $0.
; Вывод команды пишется в файл и перекодируется в UTF-16LE: nsExec обрезает длинные
; строки и смешивает кодировки (CP1251 + UTF-8) — журнал становился нечитаемым.
!macro RUN MSG CMD
  DetailPrint "${MSG}"
  !insertmacro LOG "ШАГ: ${MSG}"
  Push $R9
  Push $R8
  StrCpy $R9 '${CMD}'
  !insertmacro LOG "  CMD: $R9"
  FileOpen $R8 "$TEMP\supip-cmd.bat" w
  ; bat пишется в системной кодовой странице: chcp 65001 сломал бы кириллические пути
  FileWrite $R8 "@echo off$\r$\nset PYTHONIOENCODING=utf-8$\r$\n"
  FileWrite $R8 "$R9 > $\"$TEMP\supip-out.txt$\" 2>&1$\r$\nexit /b %ERRORLEVEL%$\r$\n"
  FileClose $R8
  Delete "$TEMP\supip-out.txt"
  nsExec::ExecToStack '"$SYSDIR\cmd.exe" /d /c "$TEMP\supip-cmd.bat"'
  Pop $0
  Pop $R9
  !insertmacro LOG "  КОД: $0"
  !insertmacro LOG "  ВЫВОД:"
  Call AppendCmdOutput
  Pop $R8
  Pop $R9
!macroend

; Дописывание вывода команды в журнал в единой кодировке (UTF-8 либо OEM → UTF-16LE)
!macro APPEND_OUT UN
Function ${UN}AppendCmdOutput
  Push $R7
  Push $R5
  Push $R4
  Push $0
  ${If} ${FileExists} "$TEMP\supip-out.txt"
    ; журнал закрывается на время дописывания внешним процессом и открывается заново
    FileClose $LogFile
    StrCpy $R7 "$$b=[IO.File]::ReadAllBytes('$TEMP\supip-out.txt');try{$$t=(New-Object System.Text.UTF8Encoding($$false,$$true)).GetString($$b)}catch{$$t=[Text.Encoding]::GetEncoding(866).GetString($$b)};[IO.File]::AppendAllText('$LogPath',$$t,[Text.Encoding]::Unicode)"
    nsExec::ExecToStack 'powershell -NoProfile -ExecutionPolicy Bypass -Command "$R7"'
    Pop $0
    Pop $R5
    FileOpen $LogFile "$LogPath" a
    ${If} $0 != 0
      ; резервный путь: построчное чтение средствами NSIS (без перекодировки)
      FileOpen $R4 "$TEMP\supip-out.txt" r
      read_out:
        FileRead $R4 $R5
        ${If} $R5 == ""
          Goto read_done
        ${EndIf}
        FileSeek $LogFile 0 END
        FileWriteUTF16LE $LogFile "    $R5"
        Goto read_out
      read_done:
      FileClose $R4
    ${EndIf}
  ${Else}
    FileSeek $LogFile 0 END
    FileWriteUTF16LE $LogFile "    (вывод отсутствует)$\r$\n"
  ${EndIf}
  Pop $0
  Pop $R4
  Pop $R5
  Pop $R7
FunctionEnd
!macroend
!insertmacro APPEND_OUT ""
!insertmacro APPEND_OUT "un."

; То же для деинсталлятора
!macro URUN MSG CMD
  DetailPrint "${MSG}"
  !insertmacro LOG "ШАГ: ${MSG}"
  Push $R9
  Push $R8
  StrCpy $R9 '${CMD}'
  !insertmacro LOG "  CMD: $R9"
  FileOpen $R8 "$TEMP\supip-cmd.bat" w
  ; bat пишется в системной кодовой странице: chcp 65001 сломал бы кириллические пути
  FileWrite $R8 "@echo off$\r$\nset PYTHONIOENCODING=utf-8$\r$\n"
  FileWrite $R8 "$R9 > $\"$TEMP\supip-out.txt$\" 2>&1$\r$\nexit /b %ERRORLEVEL%$\r$\n"
  FileClose $R8
  Delete "$TEMP\supip-out.txt"
  nsExec::ExecToStack '"$SYSDIR\cmd.exe" /d /c "$TEMP\supip-cmd.bat"'
  Pop $0
  Pop $R9
  !insertmacro LOG "  КОД: $0"
  !insertmacro LOG "  ВЫВОД:"
  Call un.AppendCmdOutput
  Pop $R8
  Pop $R9
!macroend

; Метка порядка байтов для нового журнала UTF-16LE
!macro LOG_BOM
  Push $9
  FileSeek $LogFile 0 END $9
  ${If} $9 == 0
    FileWriteWord $LogFile 0xFEFF
  ${EndIf}
  Pop $9
!macroend

; Каждый запуск пишет новый журнал в UTF-16LE с BOM; прежний сохраняется как *.prev.log.
; Дописывание UTF-16LE в журнал прежней версии (ANSI) делало файл нечитаемым — устранено.
!macro LOG_NEW PATH PREV
  Push $9
  ${If} ${FileExists} "${PATH}"
    Delete "${PREV}"
    Rename "${PATH}" "${PREV}"
  ${EndIf}
  FileOpen $LogFile "${PATH}" w
  FileWriteWord $LogFile 0xFEFF
  Pop $9
!macroend

Function .onInit
  SetRegView 64
  StrCpy $LogPath "$TEMP\SUP-IP-Setup.log"
  !insertmacro LOG_NEW "$TEMP\SUP-IP-Setup.log" "$TEMP\SUP-IP-Setup.prev.log"
  !insertmacro LOG "==================================================="
  !insertmacro LOG "СУП-ИП ${VERSION}: запуск установщика (сборка ${__DATE__})"
  ReadRegStr $0 HKLM "SOFTWARE\Microsoft\Windows NT\CurrentVersion" "ProductName"
  ReadRegStr $1 HKLM "SOFTWARE\Microsoft\Windows NT\CurrentVersion" "CurrentBuild"
  !insertmacro LOG "ОС: $0 сборка $1; каталог запуска: $EXEDIR"
  StrCpy $PgMode "auto"
  StrCpy $PgHost "127.0.0.1"
  StrCpy $PgPort "5432"
  StrCpy $PgInstaller ""
  StrCpy $Bundled ""
  StrCpy $PgFound "не обнаружен"
  ; офлайн-пакет: включён в установщик либо лежит рядом с ним
  !ifdef PG_BUNDLED
    StrCpy $PgInstaller "$INSTDIR\installer\postgresql-setup.exe"
    StrCpy $Bundled "1"
    !insertmacro LOG "Офлайн-пакет PostgreSQL включён в установщик"
  !endif
  ${If} ${FileExists} "$EXEDIR\postgresql-setup.exe"
    StrCpy $PgInstaller "$EXEDIR\postgresql-setup.exe"
    StrCpy $Bundled "1"
  ${EndIf}
  Call DetectPostgres
  !insertmacro LOG "Обнаружен PostgreSQL: $PgFound (служба: $PgService)"
FunctionEnd

; Поиск установленного PostgreSQL: службы, реестр EDB, каталоги Program Files
Function DetectPostgres
  StrCpy $PgService ""
  ${For} $1 18 12
    nsExec::ExecToStack 'sc query postgresql-x64-$1'
    Pop $0
    Pop $2
    ${If} $0 == 0
      StrCpy $PgService "postgresql-x64-$1"
      StrCpy $PgFound "служба postgresql-x64-$1"
      !insertmacro LOG "Найдена служба postgresql-x64-$1"
      ${ExitFor}
    ${EndIf}
  ${Next}
  ${If} $PgService == ""
    ; записи EDB в реестре (имя службы может отличаться от типового)
    StrCpy $1 0
    enum_pg:
      EnumRegKey $2 HKLM "SOFTWARE\PostgreSQL\Installations" $1
      ${If} $2 == ""
        Goto enum_done
      ${EndIf}
      ReadRegStr $3 HKLM "SOFTWARE\PostgreSQL\Installations\$2" "Service ID"
      ReadRegStr $4 HKLM "SOFTWARE\PostgreSQL\Installations\$2" "Base Directory"
      ReadRegStr $5 HKLM "SOFTWARE\PostgreSQL\Installations\$2" "Port"
      !insertmacro LOG "Реестр EDB: $2 → служба '$3' · каталог '$4' · порт '$5'"
      ${If} $3 != ""
        StrCpy $PgService $3
        StrCpy $PgFound "установка EDB ($4)"
        ${If} $5 != ""
          StrCpy $PgPort $5
        ${EndIf}
        Goto enum_done
      ${EndIf}
      IntOp $1 $1 + 1
      Goto enum_pg
    enum_done:
  ${EndIf}
  ${If} $PgService == ""
    ; портативная установка (binaries zip) — служба не зарегистрирована
    ${For} $1 17 12
      ${If} ${FileExists} "$PROGRAMFILES64\PostgreSQL\$1\bin\pg_ctl.exe"
        StrCpy $PgFound "каталог $PROGRAMFILES64\PostgreSQL\$1 (служба не найдена)"
        !insertmacro LOG "Найден каталог PostgreSQL $1 без службы"
        ${ExitFor}
      ${EndIf}
    ${Next}
  ${EndIf}
  ${If} $PgService != ""
    StrCpy $PgMode "exist"
  ${ElseIf} $Bundled == "1"
    StrCpy $PgMode "file"
  ${EndIf}
FunctionEnd

; ---------- Страница выбора источника PostgreSQL ----------
Function PgPage
  !insertmacro LOG "Страница PostgreSQL: обнаружено '$PgFound'"
  nsDialogs::Create 1018
  Pop $PgDlg
  !ifdef PG_BUNDLED
    ${NSD_CreateLabel} 0 0 100% 26u "Офлайн-пакет PostgreSQL ${PG_VER} включён в этот установщик — интернет не требуется. Обнаружено на ПК: $PgFound."
  !else
    ${NSD_CreateLabel} 0 0 100% 26u "СУП-ИП работает на PostgreSQL (ТЗ §4.1). Обнаружено на этом ПК: $PgFound.$\r$\nВыберите способ подготовки СУБД:"
  !endif
  Pop $0
  ${NSD_CreateRadioButton} 0 32u 100% 12u "Скачать и установить PostgreSQL ${PG_VER} автоматически (нужен интернет, ~350 МБ)"
  Pop $RbAuto
  GetFunctionAddress $1 PgPickAuto
  nsDialogs::OnClick $RbAuto $1
  ${NSD_CreateRadioButton} 0 50u 100% 12u "Установить из офлайн-файла PostgreSQL (с диска или флешки)"
  Pop $RbFile
  GetFunctionAddress $1 PgPickFile
  nsDialogs::OnClick $RbFile $1
  ${NSD_CreateText} 12u 66u 66% 12u "$PgInstaller"
  Pop $TxtFile
  ${NSD_CreateButton} 80% 66u 18% 12u "Обзор…"
  Pop $0
  GetFunctionAddress $1 PgBrowse
  nsDialogs::OnClick $0 $1
  ${NSD_CreateRadioButton} 0 86u 100% 12u "PostgreSQL уже установлен (локально или на другом сервере ЛВС)"
  Pop $RbExist
  GetFunctionAddress $1 PgPickExist
  nsDialogs::OnClick $RbExist $1
  ${NSD_CreateLabel} 12u 102u 20% 12u "Адрес сервера:"
  Pop $0
  ${NSD_CreateText} 32u 102u 26% 12u "$PgHost"
  Pop $TxtHost
  ${NSD_CreateLabel} 60% 102u 12% 12u "Порт:"
  Pop $0
  ${NSD_CreateText} 72% 102u 16% 12u "$PgPort"
  Pop $TxtPort
  ${NSD_CreateLabel} 0 122u 100% 26u "Журнал установки ведётся всегда: $TEMP\SUP-IP-Setup.log$\r$\nПосле установки он копируется в каталог программы (data\logs\installer.log)."
  Pop $0
  ${If} $PgMode == "exist"
    Call PgPickExist
  ${ElseIf} $PgMode == "file"
    Call PgPickFile
  ${Else}
    Call PgPickAuto
  ${EndIf}
  nsDialogs::Show
FunctionEnd

Function PgPickAuto
  ${NSD_Check} $RbAuto
  ${NSD_Uncheck} $RbFile
  ${NSD_Uncheck} $RbExist
FunctionEnd

Function PgPickFile
  ${NSD_Uncheck} $RbAuto
  ${NSD_Check} $RbFile
  ${NSD_Uncheck} $RbExist
FunctionEnd

Function PgPickExist
  ${NSD_Uncheck} $RbAuto
  ${NSD_Uncheck} $RbFile
  ${NSD_Check} $RbExist
FunctionEnd

Function PgBrowse
  nsDialogs::SelectFileDialog open "$EXEDIR" "Установщик PostgreSQL|postgresql-*.exe|Все файлы|*.*"
  Pop $0
  ${If} $0 != ""
    ${NSD_SetText} $TxtFile $0
    Call PgPickFile
    !insertmacro LOG "Выбран офлайн-установщик: $0"
  ${EndIf}
FunctionEnd

Function PgPageLeave
  ${NSD_GetState} $RbExist $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $PgMode "exist"
  ${Else}
    ${NSD_GetState} $RbFile $0
    ${If} $0 == ${BST_CHECKED}
      StrCpy $PgMode "file"
    ${Else}
      StrCpy $PgMode "auto"
    ${EndIf}
  ${EndIf}
  ${NSD_GetText} $TxtFile $PgInstaller
  ${NSD_GetText} $TxtHost $PgHost
  ${NSD_GetText} $TxtPort $PgPort
  ${If} $PgMode == "file"
    ${If} $PgInstaller == ""
      MessageBox MB_ICONEXCLAMATION "Укажите файл установщика PostgreSQL (postgresql-16.x-windows-x64.exe) или выберите другой способ."
      Abort
    ${EndIf}
    ${IfNot} ${FileExists} "$PgInstaller"
      MessageBox MB_ICONEXCLAMATION "Файл не найден: $PgInstaller"
      Abort
    ${EndIf}
  ${EndIf}
  ${If} $PgMode == "exist"
    ${If} $PgHost == ""
      MessageBox MB_ICONEXCLAMATION "Укажите адрес сервера PostgreSQL."
      Abort
    ${EndIf}
  ${EndIf}
  ${If} $PgMode == "auto"
    ${If} $Bundled == "1"
      ${AndIf} ${FileExists} "$PgInstaller"
      StrCpy $PgMode "file"
      !insertmacro LOG "Режим изменён на офлайн: установщик PostgreSQL включён в пакет — загрузка из сети не требуется"
    ${EndIf}
  ${EndIf}
  !insertmacro LOG "Выбран режим PostgreSQL: $PgMode (файл: $PgInstaller · сервер: $PgHost:$PgPort)"
FunctionEnd

Function ConfigPage
  nsDialogs::Create 1018
  Pop $0
  ${NSD_CreateLabel} 0 0 100% 24u "Параметры установки СУП-ИП (ТЗ §7.3). Мастер создаст БД, схему, службу и правило брандмауэра."
  Pop $0
  ${If} $PgMode == "exist"
    ${NSD_CreateLabel} 0 30u 70% 12u "Пароль суперпользователя PostgreSQL (существующий):"
  ${Else}
    ${NSD_CreateLabel} 0 30u 70% 12u "Пароль суперпользователя PostgreSQL (будет установлен):"
  ${EndIf}
  Pop $0
  ${NSD_CreatePassword} 0 44u 60% 12u ""
  Pop $PgPass
  ${NSD_CreateLabel} 0 64u 60% 12u "Порт веб-сервера (клиенты ЛВС):"
  Pop $0
  ${NSD_CreateText} 0 78u 30% 12u "8443"
  Pop $Port
  ${NSD_CreateLabel} 0 98u 60% 12u "Пароль администратора приложения (логин admin):"
  Pop $0
  ${NSD_CreatePassword} 0 112u 60% 12u ""
  Pop $AdminPass
  ${NSD_CreateCheckbox} 0 134u 80% 12u "Включить HTTPS (самоподписанный сертификат)"
  Pop $UseHttps
  ${NSD_Check} $UseHttps
  nsDialogs::Show
FunctionEnd

Function ConfigPageLeave
  ${NSD_GetText} $PgPass $PgPass
  ${NSD_GetText} $Port $Port
  ${NSD_GetText} $AdminPass $AdminPass
  ${NSD_GetState} $UseHttps $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $UseHttps "1"
  ${Else}
    StrCpy $UseHttps "0"
  ${EndIf}
  ${If} $PgPass == ""
    MessageBox MB_ICONEXCLAMATION "Укажите пароль суперпользователя PostgreSQL."
    Abort
  ${EndIf}
  ${If} $AdminPass == ""
    StrCpy $AdminPass "demo"
  ${EndIf}
  ${If} $Port < 1
    MessageBox MB_ICONEXCLAMATION "Некорректный порт."
    Abort
  ${EndIf}
  ${If} $Port > 65535
    MessageBox MB_ICONEXCLAMATION "Порт должен быть в диапазоне 1–65535."
    Abort
  ${EndIf}
  !insertmacro LOG "Параметры: порт $Port · HTTPS=$UseHttps · режим СУБД=$PgMode (пароли в журнал не пишутся)"
FunctionEnd

Section "Проверки" SEC_CHECK
  !insertmacro LOG "--- Раздел: проверки совместимости ---"
  ${IfNot} ${AtLeastWin10}
    !insertmacro LOG "ОТКАЗ: требуется Windows 10/11 x64"
    MessageBox MB_ICONSTOP "Требуется Windows 10/11 x64.$\r$\nЖурнал: $TEMP\SUP-IP-Setup.log"
    Abort
  ${EndIf}
  ${If} ${RunningX64}
  ${Else}
    !insertmacro LOG "ОТКАЗ: 32-разрядная Windows"
    MessageBox MB_ICONSTOP "Требуется 64-разрядная Windows.$\r$\nЖурнал: $TEMP\SUP-IP-Setup.log"
    Abort
  ${EndIf}
  System::Call 'kernel32::GetDiskFreeSpaceExW(t "$WINDIR", *l .r1, , )'
  System::Int64Op $1 / 1048576
  Pop $2
  !insertmacro LOG "Свободно на системном диске: $2 МБ"
  ${If} $2 < 1024
    MessageBox MB_ICONSTOP "Недостаточно места на диске (нужно ≥ 1 ГБ).$\r$\nЖурнал: $TEMP\SUP-IP-Setup.log"
    Abort
  ${EndIf}
SectionEnd

Section "Файлы программы" SEC_FILES
  !insertmacro LOG "--- Раздел: файлы программы → $INSTDIR ---"
  CreateDirectory "$INSTDIR"
  CreateDirectory "$INSTDIR\data"
  CreateDirectory "$INSTDIR\data\logs"
  IfFileExists "$INSTDIR\SUP-IP.exe" 0 fresh
    !insertmacro RUN "Обновление: резервная копия БД" '"$INSTDIR\SUP-IP.exe" --backup-now'
    ${If} $0 == 3
      !insertmacro LOG "Резервная копия пропущена: БД недоступна (первая установка либо PostgreSQL не запущен)"
    ${ElseIf} $0 != 0
      !insertmacro LOG "ВНИМАНИЕ: резервная копия не создана (код $0) — установка продолжается"
    ${EndIf}
    !insertmacro RUN "Обновление: остановка службы" '"$INSTDIR\SUP-IP.exe" --service stop'
    Sleep 2000
  fresh:
  SetOutPath "$INSTDIR"
  File "..\dist\SUP-IP.exe"
  File "..\..\README.md"
  File "..\..\CHANGELOG.md"
  SetOutPath "$INSTDIR\installer"
  File "setup.nsi"
  !if /FILEEXISTS "postgresql-setup.exe"
    File "postgresql-setup.exe"
    !ifdef PG_BUNDLED
      StrCpy $PgInstaller "$INSTDIR\installer\postgresql-setup.exe"
    !endif
    !insertmacro LOG "В пакет включён офлайн-установщик PostgreSQL"
  !endif
  !insertmacro LOG "Файлы программы скопированы"
SectionEnd

Section "PostgreSQL" SEC_PG
  !insertmacro LOG "--- Раздел: PostgreSQL (режим $PgMode) ---"
  ${If} $PgMode == "exist"
    ${If} $PgService != ""
      !insertmacro RUN "Запуск службы $PgService" 'sc start $PgService'
    ${EndIf}
    DetailPrint "Используется существующий PostgreSQL: $PgHost:$PgPort"
    !insertmacro LOG "Используется существующий сервер $PgHost:$PgPort"
    Goto pg_wait_tcp
  ${EndIf}

  ${If} $PgMode == "auto"
    ${If} $Bundled == "1"
      ${AndIf} ${FileExists} "$PgInstaller"
      StrCpy $PgMode "file"
      !insertmacro LOG "Используется офлайн-пакет из установщика: $PgInstaller"
    ${EndIf}
  ${EndIf}
  ${If} $PgMode == "auto"
    DetailPrint "Загрузка PostgreSQL ${PG_VER} (~350 МБ)…"
    !insertmacro LOG "Загрузка PostgreSQL: ${PG_URL}"
    !insertmacro RUN "Загрузка установщика PostgreSQL (curl)" 'curl.exe -L --retry 3 --connect-timeout 30 -o "$TEMP\postgresql-setup.exe" "${PG_URL}"'
    ${If} $0 != 0
      !insertmacro RUN "Повторная загрузка (PowerShell)" 'powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri ${PG_URL} -OutFile "$TEMP\postgresql-setup.exe""'
    ${EndIf}
    ${IfNot} ${FileExists} "$TEMP\postgresql-setup.exe"
      !insertmacro LOG "ОТКАЗ: установщик PostgreSQL не загружен"
      MessageBox MB_ICONSTOP "Не удалось скачать PostgreSQL (нет интернета или доступ запрещён).$\r$\n$\r$\nСкачайте на любом ПК с интернетом файл$\r$\npostgresql-16.x-windows-x64.exe (enterprisedb.com/downloads/postgres-postgresql-downloads),$\r$\nперенесите на этот ПК и в установщике выберите «Установить из офлайн-файла».$\r$\n$\r$\nЖурнал: $TEMP\SUP-IP-Setup.log"
      Abort
    ${EndIf}
    FileOpen $4 "$TEMP\postgresql-setup.exe" r
    FileSeek $4 0 END $5
    FileClose $4
    IntOp $1 $5 / 1048576
    !insertmacro LOG "Загружено: $1 МБ ($5 байт)"
    ${If} $1 < 100
      ${If} $Bundled == "1"
        ${AndIf} ${FileExists} "$PgInstaller"
        !insertmacro LOG "Загрузка неполна — используется офлайн-пакет из установщика"
        StrCpy $PgMode "file"
      ${Else}
        !insertmacro LOG "ОТКАЗ: загруженный файл неполон ($1 МБ)"
        MessageBox MB_ICONSTOP "Загруженный файл PostgreSQL повреждён или неполон ($1 МБ).$\r$\nПовторите установку или используйте офлайн-файл (SUP-IP-Setup-full.exe).$\r$\nЖурнал: $TEMP\SUP-IP-Setup.log"
        Abort
      ${EndIf}
    ${EndIf}
    StrCpy $PgInstaller "$TEMP\postgresql-setup.exe"
  ${EndIf}

  ; обновление СУП-ИП поверх работающего PostgreSQL той же версии: СУБД не трогаем
  ${If} $PgService == "postgresql-x64-${PG_VER}"
    StrCpy $R7 "try{$$c=New-Object Net.Sockets.TcpClient;$$c.Connect('$PgHost',$PgPort);$$c.Close();exit 0}catch{exit 1}"
    nsExec::ExecToStack 'powershell -NoProfile -Command "$R7"'
    Pop $0
    Pop $3
    ${If} $0 == 0
      !insertmacro LOG "PostgreSQL ${PG_VER} уже установлен и принимает соединения на $PgHost:$PgPort — повторная установка СУБД пропущена"
      DetailPrint "PostgreSQL ${PG_VER} уже установлен — установка СУБД пропущена"
      Goto pg_wait_tcp
    ${EndIf}
  ${EndIf}

  DetailPrint "Тихая установка PostgreSQL (2–5 минут)…"
  !insertmacro RUN "Установка PostgreSQL из $PgInstaller" '"$PgInstaller" --mode unattended --unattendedmodeui none --superpassword "$PgPass" --serverport $PgPort --servicename postgresql-x64-${PG_VER} --disable-components stackbuilder'
  ${If} $0 != 0
    !insertmacro LOG "ОТКАЗ: установка PostgreSQL · код $0"
    MessageBox MB_ICONSTOP "Установка PostgreSQL завершилась с кодом $0.$\r$\nПодробности в журнале: $TEMP\SUP-IP-Setup.log$\r$\n$\r$\nЧастые причины: пароль не соответствует политике сложности, порт $PgPort занят, требуется перезагрузка."
    Abort
  ${EndIf}
  StrCpy $PgService "postgresql-x64-${PG_VER}"
  StrCpy $PgHost "127.0.0.1"

  pg_wait_tcp:
  ; ожидание готовности СУБД (ТЗ §7.3 п.7)
  StrCpy $2 0
  pg_wait:
    StrCpy $R7 "try{$$c=New-Object Net.Sockets.TcpClient;$$c.Connect('$PgHost',$PgPort);$$c.Close();exit 0}catch{exit 1}"
    nsExec::ExecToStack 'powershell -NoProfile -Command "$R7"'
    Pop $0
    Pop $3
    ${If} $0 == 0
      !insertmacro LOG "PostgreSQL принимает соединения на $PgHost:$PgPort (попытка $2)"
      Goto pg_ready
    ${EndIf}
    IntOp $2 $2 + 1
    ${If} $2 > 40
      !insertmacro LOG "ОТКАЗ: PostgreSQL не отвечает на $PgHost:$PgPort"
      MessageBox MB_ICONSTOP "PostgreSQL не отвечает на $PgHost:$PgPort.$\r$\nПроверьте службу и параметры подключения.$\r$\nЖурнал: $TEMP\SUP-IP-Setup.log"
      Abort
    ${EndIf}
    Sleep 3000
    Goto pg_wait
  pg_ready:
SectionEnd

Section "Настройка" SEC_CONFIG
  !insertmacro LOG "--- Раздел: мастер настройки ядра ---"
  FileOpen $0 "$INSTDIR\install.ini" w
  FileWrite $0 "[install]$\r$\n"
  FileWrite $0 "pg_host=$PgHost$\r$\n"
  FileWrite $0 "pg_port=$PgPort$\r$\n"
  FileWrite $0 "pg_super=postgres$\r$\n"
  FileWrite $0 "pg_super_pass=$PgPass$\r$\n"
  FileWrite $0 "app_db=supip$\r$\n"
  FileWrite $0 "app_user=supip$\r$\n"
  FileWrite $0 "app_pass=supip$\r$\n"
  FileWrite $0 "port=$Port$\r$\n"
  FileWrite $0 "host=0.0.0.0$\r$\n"
  FileWrite $0 "https=$UseHttps$\r$\n"
  FileWrite $0 "admin_pass=$AdminPass$\r$\n"
  FileWrite $0 "service=1$\r$\n"
  FileClose $0
  !insertmacro RUN "Мастер настройки: БД · схема · HTTPS · служба · брандмауэр" '"$INSTDIR\SUP-IP.exe" --install-auto "$INSTDIR\install.ini"'
  ${If} $0 == 4
    !insertmacro LOG "ВНИМАНИЕ: мастер настройки завершён, но служба не подняла сервер (код 4)"
    !insertmacro RUN "Повторный запуск службы СУП-ИП" '"$INSTDIR\SUP-IP.exe" --service start'
    !insertmacro RUN "Повторная проверка работоспособности" '"$INSTDIR\SUP-IP.exe" --health'
    ${If} $0 != 0
      MessageBox MB_ICONEXCLAMATION "Программа установлена и настроена, но сервер пока не отвечает (код $0).$\r$\n$\r$\nЗапустите службу: меню «Пуск» → СУП-ИП → «Запуск сервера»,$\r$\nили в командной строке от администратора: sc start SUP-IP$\r$\nЗатем проверьте: SUP-IP.exe --health$\r$\n$\r$\nОтчёт: $INSTDIR\data\install-report.txt$\r$\nЖурналы: $INSTDIR\data\logs\installer.log, setup.log, supip.log"
      !insertmacro LOG "СЕРВЕР НЕ ПОДНЯТ: пользователю показана инструкция запуска"
    ${Else}
      !insertmacro LOG "Служба поднята повторным запуском — сервер отвечает"
    ${EndIf}
  ${ElseIf} $0 != 0
    !insertmacro LOG "ОТКАЗ: мастер настройки · код $0"
    MessageBox MB_ICONSTOP "Настройка не завершена (код $0).$\r$\n$\r$\nОтчёт: $INSTDIR\data\install-report.txt$\r$\nЖурнал установки: $INSTDIR\data\logs\installer.log$\r$\nЖурнал настройки: $INSTDIR\data\logs\setup.log$\r$\n$\r$\nПришлите эти файлы для разбора ошибки."
    Abort
  ${EndIf}
  Delete "$INSTDIR\install.ini" ; пароль суперпользователя не хранится на диске
  !insertmacro RUN "Проверка работоспособности" '"$INSTDIR\SUP-IP.exe" --health'
  ${If} $0 == 0
    !insertmacro LOG "Проверка работоспособности: сервер отвечает, установка исправна"
  ${Else}
    !insertmacro LOG "ВНИМАНИЕ: проверка работоспособности · код $0 (сервер не отвечает)"
  ${EndIf}
  ; журнал установки — в каталог программы (доступен из M14)
  FileClose $LogFile
  CopyFiles /SILENT "$TEMP\SUP-IP-Setup.log" "$INSTDIR\data\logs\installer.log"
  FileOpen $LogFile "$LogPath" a
  FileSeek $LogFile 0 END
  !insertmacro LOG "Журнал установки скопирован в $INSTDIR\data\logs\installer.log"
  ; Ярлыки и регистрация
  CreateDirectory "$SMPROGRAMS\SUP-IP"
  CreateShortcut "$SMPROGRAMS\SUP-IP\СУП-ИП (открыть клиент).lnk" "https://localhost:$Port"
  CreateShortcut "$SMPROGRAMS\SUP-IP\Отчёт об установке.lnk" "$INSTDIR\data\install-report.txt"
  CreateShortcut "$SMPROGRAMS\SUP-IP\Журнал установки.lnk" "$INSTDIR\data\logs\installer.log"
  CreateShortcut "$SMPROGRAMS\SUP-IP\Журнал настройки.lnk" "$INSTDIR\data\logs\setup.log"
  CreateShortcut "$SMPROGRAMS\SUP-IP\Журнал работы сервера.lnk" "$INSTDIR\data\logs\supip.log"
  CreateShortcut "$SMPROGRAMS\SUP-IP\Запуск сервера (служба).lnk" "$INSTDIR\SUP-IP.exe" "--service start"
  CreateShortcut "$SMPROGRAMS\SUP-IP\Остановка сервера (служба).lnk" "$INSTDIR\SUP-IP.exe" "--service stop"
  CreateShortcut "$SMPROGRAMS\SUP-IP\Состояние службы.lnk" "$SYSDIR\cmd.exe" '/k ""$INSTDIR\SUP-IP.exe" --service status"'
  CreateShortcut "$SMPROGRAMS\SUP-IP\Проверка работоспособности.lnk" "$SYSDIR\cmd.exe" '/k ""$INSTDIR\SUP-IP.exe" --health"'
  CreateShortcut "$SMPROGRAMS\SUP-IP\Удалить СУП-ИП.lnk" "$INSTDIR\uninstall.exe"
  WriteRegStr HKLM "Software\SUP-IP" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "Software\SUP-IP" "Version" "${VERSION}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\SUP-IP" "DisplayName" "${DISPLAY}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\SUP-IP" "DisplayVersion" "${VERSION}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\SUP-IP" "UninstallString" "$INSTDIR\uninstall.exe"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\SUP-IP" "Publisher" "SUP-IP"
  WriteUninstaller "$INSTDIR\uninstall.exe"
  ${If} $0 == 0
    !insertmacro LOG "УСТАНОВКА ЗАВЕРШЕНА: порт $Port · служба СУП-ИП работает · ярлыки созданы"
  ${Else}
    !insertmacro LOG "УСТАНОВКА ЗАВЕРШЕНА С ПРЕДУПРЕЖДЕНИЕМ: порт $Port · служба не поднята (код $0) · ярлыки созданы"
  ${EndIf}
SectionEnd

Function .onInstFailed
  !insertmacro LOG "УСТАНОВКА ПРЕРВАНА (см. записи выше)"
  FileClose $LogFile
  CreateDirectory "$INSTDIR\data\logs"
  CopyFiles /SILENT "$TEMP\SUP-IP-Setup.log" "$INSTDIR\data\logs\installer.log"
FunctionEnd

Section "Uninstall"
  StrCpy $LogPath "$TEMP\SUP-IP-Uninstall.log"
  !insertmacro LOG_NEW "$TEMP\SUP-IP-Uninstall.log" "$TEMP\SUP-IP-Uninstall.prev.log"
  !insertmacro LOG "Удаление СУП-ИП ${VERSION}"
  !insertmacro URUN "Остановка службы" '"$INSTDIR\SUP-IP.exe" --service stop'
  !insertmacro URUN "Удаление службы" '"$INSTDIR\SUP-IP.exe" --service remove'
  !insertmacro URUN "Удаление правила брандмауэра" 'netsh advfirewall firewall delete rule name=SUP-IP'
  Delete "$INSTDIR\SUP-IP.exe"
  Delete "$INSTDIR\README.md"
  Delete "$INSTDIR\CHANGELOG.md"
  Delete "$INSTDIR\uninstall.exe"
  RMDir /r "$INSTDIR\installer"
  RMDir /r "$SMPROGRAMS\SUP-IP"
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\SUP-IP"
  MessageBox MB_YESNO|MB_DEFBUTTON2|MB_ICONQUESTION "Удалить также данные (БД PostgreSQL, файлы документов, резервные копии, журналы)?$\r$\nПо умолчанию данные СОХРАНЯЮТСЯ." IDYES deldata IDNO keepdata
  deldata:
    RMDir /r "$INSTDIR\data"
    !insertmacro LOG "Каталог данных удалён по выбору пользователя"
    MessageBox MB_ICONINFORMATION "Каталог данных удалён. БД PostgreSQL удалите средствами pgAdmin/psql (DROP DATABASE supip)."
    Goto done
  keepdata:
    DetailPrint "Данные сохранены: $INSTDIR\data и БД PostgreSQL."
    !insertmacro LOG "Данные сохранены"
  done:
  DeleteRegKey HKLM "Software\SUP-IP"
  RMDir "$INSTDIR"
  !insertmacro LOG "Удаление завершено"
  FileClose $LogFile
SectionEnd
