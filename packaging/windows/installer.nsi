; Hypixel SkyBlock API Tracker —— Windows 安装器（NSIS / MUI2）
;
; 由 .github/workflows/release.yml 调用：
;   makensis /DTAG=v3.0.0 packaging/windows/installer.nsi
; 产物：packaging/out/HypixelTracker-Setup-<TAG>.exe
;
; 多语言：安装界面提供 简中 / English / 日本語 / Deutsch / Español / Français / Русский / Português(BR)，
; 语言选择跟随系统，用户可在安装开始时切换。
; 安装内容与 zip 版一致（二进制 + templates/static/locales + 种子数据 + 8 种语言说明）。

Unicode true
!include "MUI2.nsh"
!include "FileFunc.nsh"

!ifndef TAG
  !define TAG "v3.0.0"
!endif

Name "Hypixel SkyBlock API Tracker"
OutFile "..\out\HypixelTracker-Setup-${TAG}.exe"
InstallDir "$PROGRAMFILES64\HypixelTracker"
InstallDirRegKey HKLM "Software\HypixelTracker" "InstallDir"
RequestExecutionLevel admin
SetCompressor /SOLID lzma
BrandingText "Hypixel SkyBlock API Tracker ${TAG}"

!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_NOAUTOCLOSE

!define MUI_FINISHPAGE_TEXT "$(FINISH_TEXT)"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "Japanese"
!insertmacro MUI_LANGUAGE "German"
!insertmacro MUI_LANGUAGE "Spanish"
!insertmacro MUI_LANGUAGE "French"
!insertmacro MUI_LANGUAGE "Russian"
!insertmacro MUI_LANGUAGE "PortugueseBR"

; 完成页的一句话说明（8 种语言）
LangString FINISH_TEXT ${LANG_ENGLISH}    "Run tracker-server, then open http://127.0.0.1:8000 in your browser.${\r$\n}Data is stored in %APPDATA%\HypixelTracker."
LangString FINISH_TEXT ${LANG_SIMPCHINESE} "运行 tracker-server，然后用浏览器打开 http://127.0.0.1:8000。${\r$\n}数据保存在 %APPDATA%\HypixelTracker。"
LangString FINISH_TEXT ${LANG_JAPANESE}   "tracker-server を実行し、ブラウザで http://127.0.0.1:8000 を開いてください。${\r$\n}データは %APPDATA%\HypixelTracker に保存されます。"
LangString FINISH_TEXT ${LANG_GERMAN}     "Starten Sie tracker-server und öffnen Sie http://127.0.0.1:8000 im Browser.${\r$\n}Daten liegen in %APPDATA%\HypixelTracker."
LangString FINISH_TEXT ${LANG_SPANISH}    "Ejecuta tracker-server y abre http://127.0.0.1:8000 en el navegador.${\r$\n}Los datos se guardan en %APPDATA%\HypixelTracker."
LangString FINISH_TEXT ${LANG_FRENCH}     "Lancez tracker-server puis ouvrez http://127.0.0.1:8000 dans votre navigateur.${\r$\n}Les données sont dans %APPDATA%\HypixelTracker."
LangString FINISH_TEXT ${LANG_RUSSIAN}    "Запустите tracker-server и откройте http://127.0.0.1:8000 в браузере.${\r$\n}Данные хранятся в %APPDATA%\HypixelTracker."
LangString FINISH_TEXT ${LANG_PORTUGUESEBR} "Execute tracker-server e abra http://127.0.0.1:8000 no navegador.${\r$\n}Os dados ficam em %APPDATA%\HypixelTracker."

Section "HypixelTracker" SecMain
  SetOutPath "$INSTDIR"
  ; 二进制 + 模板 + 前端资源 + 语言包 + 种子数据 + 多语言说明
  File /r "..\stage\*.*"

  CreateDirectory "$SMPROGRAMS\Hypixel SkyBlock API Tracker"
  CreateShortcut "$SMPROGRAMS\Hypixel SkyBlock API Tracker\tracker-server.lnk" "$INSTDIR\tracker-server.exe"
  CreateShortcut "$SMPROGRAMS\Hypixel SkyBlock API Tracker\README-zh.lnk" "$INSTDIR\README-zh.txt"
  CreateShortcut "$SMPROGRAMS\Hypixel SkyBlock API Tracker\README-en.lnk" "$INSTDIR\README-en.txt"

  WriteUninstaller "$INSTDIR\uninstall.exe"
  WriteRegStr HKLM "Software\HypixelTracker" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\HypixelTracker" "DisplayName" "Hypixel SkyBlock API Tracker"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\HypixelTracker" "DisplayVersion" "${TAG}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\HypixelTracker" "Publisher" "xiaowangdeqilin"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\HypixelTracker" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\HypixelTracker" "NoModify" 1
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\HypixelTracker" "NoRepair" 1
SectionEnd

Section "Uninstall"
  Delete "$INSTDIR\tracker-server.exe"
  Delete "$INSTDIR\tracker-build.exe"
  Delete "$INSTDIR\uninstall.exe"
  RMDir /r "$INSTDIR\templates"
  RMDir /r "$INSTDIR\static"
  RMDir /r "$INSTDIR\locales"
  RMDir /r "$INSTDIR\data"
  Delete "$INSTDIR\*.txt"
  Delete "$INSTDIR\*.md"
  RMDir "$INSTDIR"
  RMDir /r "$SMPROGRAMS\Hypixel SkyBlock API Tracker"
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\HypixelTracker"
  DeleteRegKey HKLM "Software\HypixelTracker"
  ; 用户数据（%APPDATA%\HypixelTracker）保留，卸载不删历史数据
SectionEnd
