; СУП-ИП — установщик Windows x64 (ТЗ §7)
; Сборка: makensis installer\setup.nsi  (ожидает sup-ip\dist\SUP-IP.exe)
; Опционально: положите postgresql-16.x-windows-x64.exe рядом со setup.nsi в каталог installer\
;              — он будет включён в установщик для полностью офлайн-развёртывания.
Unicode true
ManifestDPIAware true
!ifndef VERSION
  !define VERSION "1.1.0"
!endif
!define APPNAME "SUP-IP"
!define DISPLAY "СУП-ИП — управление проектами"
Name "${DISPLAY} ${VERSION}"
OutFile "..\dist\SUP-IP-Setup-${VERSION}.exe"
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
!define MUI_ABORTWARNING
!insertmacro MUI_PAGE_DIRECTORY
Page custom ConfigPage ConfigPageLeave
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Russian"

Var PgPass
Var Port
Var AdminPass
Var UseHttps
Var PgInstaller
Var PgService

Section "Проверки" SEC_CHECK
  ; ТЗ §7.2: версия Windows, разрядность, дисковое пространство
  ${IfNot} ${AtLeastWin10}
    MessageBox MB_ICONSTOP "Требуется Windows 10/11 x64."
    Abort
  ${EndIf}
  ${If} ${RunningX64}
  ${Else}
    MessageBox MB_ICONSTOP "Требуется 64-разрядная Windows."
    Abort
  ${EndIf}
  System::Call 'kernel32::GetDiskFreeSpaceExW(t "$WINDIR", *l .r1, , )'
  System::Int64Op $1 / 1048576
  Pop $2
  ${If} $2 < 1024
    MessageBox MB_ICONSTOP "Недостаточно места на диске (нужно ≥ 1 ГБ)."
    Abort
  ${EndIf}
SectionEnd

Section "Файлы программы" SEC_FILES
  ; Обновление (ТЗ §7.6): копия → остановка службы → замена → миграции → запуск
  IfFileExists "$INSTDIR\SUP-IP.exe" 0 fresh
    DetailPrint "Обновление: резервная копия БД…"
    nsExec::ExecToLog '"$INSTDIR\SUP-IP.exe" --backup-now'
    DetailPrint "Обновление: остановка службы…"
    nsExec::ExecToLog '"$INSTDIR\SUP-IP.exe" --service stop'
    Sleep 2000
    Goto copy
  fresh:
  copy:
  SetOutPath "$INSTDIR"
  File "..\dist\SUP-IP.exe"
  File "..\..\README.md"
  File "..\..\CHANGELOG.md"
  SetOutPath "$INSTDIR\installer"
  File "setup.nsi"
  !if /FILEEXISTS "postgresql-setup.exe"
    File "postgresql-setup.exe"
    StrCpy $PgInstaller "$INSTDIR\installer\postgresql-setup.exe"
  !endif
SectionEnd

Section "PostgreSQL" SEC_PG
  ; ТЗ §7.3: установка и инициализация PostgreSQL (офлайн-установщик, если включён в пакет)
  StrCpy $PgService ""
  ${For} $1 14 17
    nsExec::ExecToLog 'sc query postgresql-x64-$1'
    Pop $0
    ${If} $0 == 0
      StrCpy $PgService "postgresql-x64-$1"
      DetailPrint "Найдена служба PostgreSQL: $PgService"
      ${Break}
    ${EndIf}
  ${Next}
  ${If} $PgService != ""
    nsExec::ExecToLog 'sc start $PgService'
    DetailPrint "Служба PostgreSQL уже установлена."
    Goto pg_done
  ${EndIf}
  ${If} $PgInstaller != ""
    DetailPrint "Установка PostgreSQL (автономно, ~2 мин)…"
    nsExec::ExecToLog '"$PgInstaller" --mode unattended --unattendedmodeui none --superpassword "$PgPass" --serverport 5432 --servicename postgresql-x64-16'
    Pop $0
    ${If} $0 != 0
      MessageBox MB_ICONSTOP "Установка PostgreSQL завершилась с кодом $0.$\nУстановите PostgreSQL 15/16 вручную и повторите установку СУП-ИП."
      Abort
    ${EndIf}
    StrCpy $PgService "postgresql-x64-16"
  ${Else}
    MessageBox MB_ICONSTOP "PostgreSQL не найден и не включён в пакет.$\nУстановите PostgreSQL 15/16 офлайн-установщиком, затем повторите установку СУП-ИП.$\n(Скачать заранее: enterprisedb.com/downloads/postgres-postgresql-downloads)"
    Abort
  ${EndIf}
  pg_done:
  ; ожидание готовности СУБД (ТЗ §7.3 п.7)
  StrCpy $2 0
  pg_wait:
    nsExec::ExecToLog 'sc query $PgService'
    Pop $0
    ${If} $0 == 0
      Goto pg_ready
    ${EndIf}
    IntOp $2 $2 + 1
    ${If} $2 > 30
      MessageBox MB_ICONSTOP "Служба PostgreSQL не запускается. Проверьте службы Windows."
      Abort
    ${EndIf}
    Sleep 2000
    Goto pg_wait
  pg_ready:
SectionEnd

Section "Настройка" SEC_CONFIG
  ; Параметры мастера → install.ini → автоматическая настройка ядра
  FileOpen $0 "$INSTDIR\install.ini" w
  FileWrite $0 "[install]$\r$\n"
  FileWrite $0 "pg_host=127.0.0.1$\r$\n"
  FileWrite $0 "pg_port=5432$\r$\n"
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
  DetailPrint "Мастер настройки: БД, схема, HTTPS, служба, брандмауэр…"
  nsExec::ExecToLog '"$INSTDIR\SUP-IP.exe" --install-auto "$INSTDIR\install.ini"'
  Pop $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "Настройка не завершена (код $0).$\nОтчёт: $INSTDIR\data\install-report.txt$\nПри обновлении откат выполняется вручную: восстановите БД из data\backups (M14 или --install)."
    Abort
  ${EndIf}
  Delete "$INSTDIR\install.ini" ; пароль суперпользователя не хранится на диске
  ; Проверка работоспособности (ТЗ §7.3 п.14)
  nsExec::ExecToLog '"$INSTDIR\SUP-IP.exe" --health'
  ; Ярлыки и регистрация
  CreateDirectory "$SMPROGRAMS\SUP-IP"
  CreateShortcut "$SMPROGRAMS\SUP-IP\СУП-ИП (открыть клиент).lnk" "https://localhost:$Port"
  CreateShortcut "$SMPROGRAMS\SUP-IP\Отчёт об установке.lnk" "$INSTDIR\data\install-report.txt"
  CreateShortcut "$SMPROGRAMS\SUP-IP\Удалить СУП-ИП.lnk" "$INSTDIR\uninstall.exe"
  WriteRegStr HKLM "Software\SUP-IP" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "Software\SUP-IP" "Version" "${VERSION}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\SUP-IP" "DisplayName" "${DISPLAY}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\SUP-IP" "DisplayVersion" "${VERSION}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\SUP-IP" "UninstallString" "$INSTDIR\uninstall.exe"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\SUP-IP" "Publisher" "SUP-IP"
  WriteUninstaller "$INSTDIR\uninstall.exe"
SectionEnd

Function ConfigPage
  nsDialogs::Create 1018
  Pop $0
  ${NSD_CreateLabel} 0 0 100% 24u "Параметры установки СУП-ИП (ТЗ §7.3). PostgreSQL должен быть установлен (или включён в пакет)."
  Pop $0
  ${NSD_CreateLabel} 0 30u 60% 12u "Пароль суперпользователя PostgreSQL (postgres):"
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
FunctionEnd

Section "Uninstall"
  ; ТЗ §7.7: данные сохраняются по умолчанию
  nsExec::ExecToLog '"$INSTDIR\SUP-IP.exe" --service stop'
  nsExec::ExecToLog '"$INSTDIR\SUP-IP.exe" --service remove'
  nsExec::ExecToLog 'netsh advfirewall firewall delete rule name=SUP-IP'
  Delete "$INSTDIR\SUP-IP.exe"
  Delete "$INSTDIR\README.md"
  Delete "$INSTDIR\CHANGELOG.md"
  Delete "$INSTDIR\uninstall.exe"
  RMDir /r "$INSTDIR\installer"
  RMDir /r "$SMPROGRAMS\SUP-IP"
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\SUP-IP"
  MessageBox MB_YESNO|MB_DEFBUTTON2|MB_ICONQUESTION "Удалить также данные (БД PostgreSQL, файлы документов, резервные копии)?$\nПо умолчанию данные СОХРАНЯЮТСЯ." IDYES deldata IDNO keepdata
  deldata:
    RMDir /r "$INSTDIR\data"
    nsExec::ExecToLog 'sc stop postgresql-x64-16'
    MessageBox MB_ICONINFORMATION "Каталог данных удалён. БД PostgreSQL удалите средствами pgAdmin/psql (DROP DATABASE supip)."
    Goto done
  keepdata:
    DetailPrint "Данные сохранены: $INSTDIR\data и БД PostgreSQL."
  done:
  DeleteRegKey HKLM "Software\SUP-IP"
  RMDir "$INSTDIR"
SectionEnd