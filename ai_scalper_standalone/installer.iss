; installer.iss — скрипт для Inno Setup (бесплатный компилятор установщиков
; Windows: https://jrsoftware.org/isdl.php). Собирает AI_Scalper_Setup.exe
; из уже готового dist\AI_Scalper_Pro.exe (сначала запусти build_exe.bat!).
;
; Как собрать установщик:
;   1) build_exe.bat  -> появится dist\AI_Scalper_Pro.exe
;   2) Открой этот файл (installer.iss) в Inno Setup Compiler и нажми Compile
;      (или из командной строки: ISCC installer.iss)
;   3) Готовый установщик появится в installer_output\AI_Scalper_Setup.exe
;
; Установка идёт в %LOCALAPPDATA% (папка текущего пользователя) — прав
; администратора НЕ требуется, всё максимально просто.

#define MyAppName "AI Scalper Pro"
#define MyAppVersion "1.0"
#define MyAppExeName "AI_Scalper_Pro.exe"

[Setup]
AppId={{8F2C1A6E-3B7D-4E12-9C4A-AI-SCALPER-PRO}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={localappdata}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputBaseFilename=AI_Scalper_Setup
OutputDir=installer_output
Compression=lzma
SolidCompression=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Files]
; Программа собрана ПАПКОЙ, а не одним файлом. Один файл при каждом запуске
; распаковывал во временную папку около 55 МБ — отсюда ошибки про _MEI
; (init.tcl, base_library.zip, "Failed to remove temporary directory") и
; подозрения антивируса. Папка не распаковывается вовсе: файлы уже на месте.
;
; ВАЖНО: config.py из этой папки ИСКЛЮЧЁН. При сборке он кладётся рядом с
; программой, чтобы проверить, что она вообще запускается. Но здесь у строки
; стоит ignoreversion, то есть «перезаписывать всегда» — и без исключения
; установщик затирал бы личный config.py владельца при каждом обновлении:
; ключи, пароли, пары, профиль. Настройки ставятся отдельной строкой ниже,
; с флагом onlyifdoesntexist.
Source: "dist\AI_Scalper_Pro\*"; DestDir: "{app}"; Excludes: "config.py"; Flags: ignoreversion recursesubdirs createallsubdirs

; config.py -- ставится ТОЛЬКО если у пользователя его ещё нет (чтобы при
; переустановке/обновлении не затереть его настройки -- ключи AI, пары, профиль).
Source: "config.py"; DestDir: "{app}"; Flags: onlyifdoesntexist

; Справочные файлы (не обязательны для работы, но полезны)
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "BUILD.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Настройки (config.py)"; Filename: "{app}\config.py"
Name: "{group}\Удалить {#MyAppName}"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Логи и CSV-лог сделок остаются на диске при удалении (на всякий случай,
; вдруг нужна история) -- если хочешь их тоже удалять, раскомментируй:
; Type: files; Name: "{app}\scalper.log"
; Type: files; Name: "{app}\trades_log.csv"
