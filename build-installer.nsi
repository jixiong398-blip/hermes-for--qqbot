; ============================================================
;  Hermes QQ Bot — NSIS Installer
;  Build command:  makensis build-installer.nsi
;  Requires:       NSIS 3.x with Modern UI 2
; ============================================================
;  ASSETS REQUIRED (place in E:\ai\bot-template\assets\):
;    - icon.ico       : installer & uninstaller icon (256x256)
;    - welcome.bmp    : welcome/finish page banner (164x314)
;  If missing: NSIS will error. Either create them or
;  comment out the !define MUI_ICON / MUI_WELCOMEFINISHPAGE_BITMAP lines.
; ============================================================

; ── Includes ─────────────────────────────────────────────
!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"

; ── Metadata ─────────────────────────────────────────────
!define PRODUCT_NAME     "Hermes QQ Bot"
!define PRODUCT_VERSION  "0.9.1"
!define PRODUCT_PUBLISHER "Hermes QQ Bot"
!define PRODUCT_WEB_SITE  "https://github.com/jixiong398-blip/hermes-for--qqbot"

Name    "${PRODUCT_NAME} ${PRODUCT_VERSION}"
OutFile "HermesQQBot-${PRODUCT_VERSION}-Setup.exe"

; Default install under %LOCALAPPDATA% — no admin required
InstallDir "$LOCALAPPDATA\${PRODUCT_NAME}"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 128

; Allow reinstall in same dir
!define MUI_ABORTWARNING

; ── Branding ─────────────────────────────────────────────
BrandingText "${PRODUCT_NAME} v${PRODUCT_VERSION}"

; ══════════════════════════════════════════════════════════
;  ICON / BITMAP ASSETS
;  Create or comment out these lines if assets are missing.
; ══════════════════════════════════════════════════════════
; Uncomment when you have assets\icon.ico:
; !define MUI_ICON    "assets\icon.ico"
; !define MUI_UNICON  "assets\icon.ico"

; Uncomment when you have assets\welcome.bmp (164x314):
; !define MUI_WELCOMEFINISHPAGE_BITMAP    "assets\welcome.bmp"
; !define MUI_UNWELCOMEFINISHPAGE_BITMAP  "assets\welcome.bmp"

; ── Finish Page — Launch Dashboard checkbox ──────────────
!define MUI_FINISHPAGE_RUN            "$INSTDIR\start.bat"
!define MUI_FINISHPAGE_RUN_TEXT       "Launch Hermes Dashboard after setup"
!define MUI_FINISHPAGE_RUN_NOTCHECKED

; ── Pages ────────────────────────────────────────────────
; Installer
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

; Uninstaller
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

; ── Language ─────────────────────────────────────────────
!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

; ── Install Types ────────────────────────────────────────
InstType "Full (Recommended)"
InstType "Minimal (Core + Python)"

; ══════════════════════════════════════════════════════════
;  VARIABLES
; ══════════════════════════════════════════════════════════
Var PythonExe         ; resolved path to python.exe
Var PipFailed         ; flag: pip install had errors

; ══════════════════════════════════════════════════════════
;  SECTIONS
; ══════════════════════════════════════════════════════════

; ── Hermes Core (REQUIRED) ───────────────────────────────
Section "!Hermes Core" SecCore
  SectionIn RO         ; read-only → always installed
  SectionIn 1 2        ; included in both Full and Minimal

  SetOutPath "$INSTDIR"

  DetailPrint "Copying Hermes engine (source) ..."
  File /r /x "__pycache__" /x "*.pyc" /x ".git" /x ".gitignore" "hermes"

  DetailPrint "Copying templates ..."
  SetOutPath "$INSTDIR\templates"
  File /r "templates\*.*"

  DetailPrint "Copying modules (Dashboard, Live2D, knowledge) ..."
  SetOutPath "$INSTDIR\modules"
  File /r "modules\*.*"

  DetailPrint "Copying scripts ..."
  SetOutPath "$INSTDIR\scripts"
  File /r "scripts\*.*"

  DetailPrint "Copying batch helpers ..."
  SetOutPath "$INSTDIR"
  File "start.bat"
  File "Stop-All.bat"
  File "FixNapCat.bat"
  File "install.bat"
  File "VERSION"
  File "LICENSE"
  File "README.md"

  ; Optional root files — /nonfatal skips silently if missing at build time
  File /nonfatal "CHANGELOG.md"
  File /nonfatal "UPGRADE.md"
  File /nonfatal "AGENTS.md"
  File /nonfatal "配置API.bat"

  ; Write uninstaller
  WriteUninstaller "$INSTDIR\Uninstall.exe"

  ; Write registry keys for uninstall listing
  WriteRegStr   HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}" \
                    "DisplayName"     "${PRODUCT_NAME}"
  WriteRegStr   HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}" \
                    "DisplayVersion"  "${PRODUCT_VERSION}"
  WriteRegStr   HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}" \
                    "Publisher"       "${PRODUCT_PUBLISHER}"
  WriteRegStr   HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}" \
                    "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr   HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}" \
                    "InstallLocation" "$INSTDIR"
  WriteRegStr   HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}" \
                    "DisplayIcon"     "$INSTDIR\assets\icon.ico"
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}" \
                    "NoModify"        1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}" \
                    "NoRepair"        1

  DetailPrint "Hermes Core installed."
SectionEnd


; ── Python 3.12 Runtime ──────────────────────────────────
Section "Python 3.12 Runtime" SecPython
  SectionIn 1 2        ; both Full and Minimal

  StrCpy $PipFailed "0"

  ; -------------------------------------------------------
  ; Step A — Install Python 3.12 from bundled installer
  ; -------------------------------------------------------
  ${If} ${FileExists} "python-installer.exe"
    SetOutPath "$PLUGINSDIR"
    File "python-installer.exe"

    DetailPrint "Installing Python 3.12 (silent, ~2 min) ..."
    ; /quiet = no UI, InstallAllUsers=0 = per-user,
    ; PrependPath=0 = do NOT touch system PATH,
    ; Include_test=0 = skip test suite
    ExecWait '"$PLUGINSDIR\python-installer.exe" /quiet InstallAllUsers=0 PrependPath=0 Include_test=0' $0

    ${If} $0 != 0
      DetailPrint "[WARNING] Python installer returned exit code $0"
      DetailPrint "          Falling back — will try python on PATH."
    ${EndIf}
  ${Else}
    DetailPrint "[NOTE] python-installer.exe not bundled — relying on system Python."
  ${EndIf}

  ; -------------------------------------------------------
  ; Step B — Resolve Python executable path
  ; -------------------------------------------------------
  StrCpy $PythonExe ""
  StrCpy $R0 "$LOCALAPPDATA\Programs\Python\Python312\python.exe"
  ${If} ${FileExists} $R0
    StrCpy $PythonExe $R0
    DetailPrint "Found Python 3.12 at: $PythonExe"
  ${Else}
    ; Fallback: scan PATH
    nsExec::ExecToStack 'where python 2>nul'
    Pop $0
    Pop $1
    ${If} $0 == 0
      ; Extract first line (first match)
      StrCpy $PythonExe $1
      DetailPrint "Found Python on PATH: $PythonExe"
    ${Else}
      DetailPrint "[ERROR] Python 3.12 is required but could not be found."
      DetailPrint "          Install Python 3.12 manually, then re-run this installer."
      MessageBox MB_OK|MB_ICONSTOP \
        "Python 3.12 could not be located.$\n$\n\
         Please install Python 3.12 from https://python.org $\n\
         (make sure 'Add to PATH' is checked), then re-run this installer."
      Return
    ${EndIf}
  ${EndIf}

  ; -------------------------------------------------------
  ; Step C — Create virtual environment
  ; -------------------------------------------------------
  DetailPrint "Creating virtual environment at $INSTDIR\.venv ..."
  nsExec::ExecToLog '"$PythonExe" -m venv "$INSTDIR\.venv"'
  Pop $0
  ${If} $0 != 0
    DetailPrint "[ERROR] Failed to create virtual environment (exit code $0)."
    MessageBox MB_OK|MB_ICONSTOP \
      "Could not create Python virtual environment.$\n$\n\
       Make sure Python 3.12 is properly installed and has venv support."
    Return
  ${EndIf}

  ; -------------------------------------------------------
  ; Step D — pip install Hermes engine (editable)
  ; -------------------------------------------------------
  DetailPrint "Upgrading pip ..."
  nsExec::ExecToLog '"$INSTDIR\.venv\Scripts\python.exe" -m pip install --upgrade pip --quiet'

  DetailPrint "Installing Hermes engine (pip install -e) ..."
  nsExec::ExecToLog '"$INSTDIR\.venv\Scripts\python.exe" -m pip install -e "$INSTDIR\hermes" --no-deps'
  Pop $0
  ${If} $0 != 0
    DetailPrint "[WARNING] pip install --no-deps failed (code $0). Trying full install..."
    nsExec::ExecToLog '"$INSTDIR\.venv\Scripts\python.exe" -m pip install -e "$INSTDIR\hermes"'
    Pop $0
  ${EndIf}

  ; -------------------------------------------------------
  ; Step E — Install Python dependencies (~100 MB, internet)
  ; -------------------------------------------------------
  DetailPrint "Installing Python dependencies (~100 MB, internet required) ..."
  DetailPrint "(This may take 5–15 minutes depending on your connection)"
  nsExec::ExecToLog '"$INSTDIR\.venv\Scripts\python.exe" -m pip install -r "$INSTDIR\hermes\requirements.txt"'
  Pop $0

  ${If} $0 != 0
    StrCpy $PipFailed "1"
    DetailPrint "╔════════════════════════════════════════════════╗"
    DetailPrint "║ [WARNING] Pip dependency install had errors.  ║"
    DetailPrint "║                                              ║"
    DetailPrint "║ You can retry manually:                      ║"
    DetailPrint "║   cd $INSTDIR                                ║"
    DetailPrint '║   .venv\Scripts\pip install -r hermes\requirements.txt'
    DetailPrint "║                                              ║"
    DetailPrint "║ Or use the Tsinghua mirror:                  ║"
    DetailPrint '║   .venv\Scripts\pip install -r hermes\requirements.txt \'
    DetailPrint "║     -i https://pypi.tuna.tsinghua.edu.cn/simple"
    DetailPrint "╚════════════════════════════════════════════════╝"
  ${EndIf}

  ; -------------------------------------------------------
  ; Step F — Seed base config files via install.py
  ; -------------------------------------------------------
  DetailPrint "Setting up base configuration (~/.hermes/) ..."
  nsExec::ExecToLog '"$INSTDIR\.venv\Scripts\python.exe" "$INSTDIR\scripts\install.py"'
  Pop $0

  ; Also copy missing config files directly
  Call SeedConfigFiles

  DetailPrint "Python setup complete."
SectionEnd


; ── NapCat QQ Bridge ─────────────────────────────────────
Section "NapCat QQ Bridge" SecNapCat
  SectionIn 1           ; Full only

  SetOutPath "$INSTDIR\napcat"
  File /r /x "beacon_report.log" /x "*.log" "napcat\*.*"

  DetailPrint "NapCat QQ Bridge installed."
  DetailPrint "NOTE: You must run NapCat and scan the QR code with QQ to log in."
SectionEnd


; ── Node.js Runtime ──────────────────────────────────────
;  NOTE FOR BUILDER: Pre-extract nodejs.zip → node\ before
;  running makensis, so the node/ directory is ready to bundle.
; ─────────────────────────────────────────────────────────
Section "Node.js Runtime" SecNode
  SectionIn 1           ; Full only

  SetOutPath "$INSTDIR\node"

  ; /nonfatal = skip silently if node/ hasn't been pre-extracted
  File /nonfatal /r "node\*.*"

  ${If} ${FileExists} "$INSTDIR\node\node.exe"
    DetailPrint "Node.js runtime installed."
  ${Else}
    DetailPrint "[SKIP] node/ not pre-extracted — Node.js not bundled."
    DetailPrint "       Extract nodejs.zip to node\ and rebuild installer."
  ${EndIf}
SectionEnd


; ── Start Menu Shortcuts ─────────────────────────────────
Section "Start Menu Shortcuts" SecShortcuts
  SectionIn 1 2        ; both Full and Minimal

  CreateDirectory "$SMPROGRAMS\${PRODUCT_NAME}"

  ; Start Hermes (Dashboard)
  CreateShortcut "$SMPROGRAMS\${PRODUCT_NAME}\Start Hermes Dashboard.lnk" \
    "$INSTDIR\start.bat" \
    "" \
    "$INSTDIR\assets\icon.ico" 0

  ; Stop All Services
  CreateShortcut "$SMPROGRAMS\${PRODUCT_NAME}\Stop All Services.lnk" \
    "$INSTDIR\Stop-All.bat" \
    "" \
    "$INSTDIR\Uninstall.exe" 0

  ; Uninstall
  CreateShortcut "$SMPROGRAMS\${PRODUCT_NAME}\Uninstall Hermes QQ Bot.lnk" \
    "$INSTDIR\Uninstall.exe" \
    "" \
    "$INSTDIR\Uninstall.exe" 0

  DetailPrint "Start Menu shortcuts created."
SectionEnd


; ══════════════════════════════════════════════════════════
;  SECTION DESCRIPTIONS  (shown on Components page)
; ══════════════════════════════════════════════════════════
!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecCore} \
    "Core chat engine, templates, modules (Dashboard + Live2D), and scripts.$\n$\nREQUIRED — cannot be deselected."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecPython} \
    "Python 3.12 runtime + all Hermes Python dependencies.$\n$\nIncludes automatic virtual environment setup and pip package install (~100 MB download). Internet connection required for dependency install."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecNapCat} \
    "NapCat QQ protocol bridge — connects Hermes to QQ.$\n$\nAfter install, you must manually run NapCat and scan the QR code with your QQ mobile app to log in."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecNode} \
    "Node.js portable runtime (v22.x). Required for the Live2D desktop mascot engine.$\n$\nSkip if you don't plan to use the Live2D character display."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecShortcuts} \
    "Creates Start Menu shortcuts for quick access to the Dashboard, Stop All, and Uninstall."
!insertmacro MUI_FUNCTION_DESCRIPTION_END


; ══════════════════════════════════════════════════════════
;  CALLBACK: .onInit
; ══════════════════════════════════════════════════════════
Function .onInit
  ; Check for existing installation
  ReadRegStr $R0 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}" \
                     "InstallLocation"
  ${If} $R0 != ""
    ${If} $R0 == "$INSTDIR"
      MessageBox MB_YESNO|MB_ICONQUESTION \
        "Hermes QQ Bot is already installed at:$\n$R0$\n$\nReinstall / upgrade in the same location?" \
        IDYES no_abort
    ${Else}
      MessageBox MB_YESNO|MB_ICONQUESTION \
        "Hermes QQ Bot is already installed at:$\n$R0$\n$\nDo you still want to install to $INSTDIR?" \
        IDYES no_abort
    ${EndIf}
    Abort
    no_abort:
  ${EndIf}

  ; If using MUI_LANGDLL (language selection at start), uncomment:
  ; !insertmacro MUI_LANGDLL_DISPLAY
FunctionEnd


; ══════════════════════════════════════════════════════════
;  CALLBACK: .onInstSuccess
; ══════════════════════════════════════════════════════════
Function .onInstSuccess
  ${If} $PipFailed == "1"
    MessageBox MB_OK|MB_ICONWARNING \
      "Installation completed with warnings.$\n$\n\
       Some Python dependencies could not be installed.$\n\
       Please check your internet connection and run:$\n$\n  \
       cd $INSTDIR$\n  \
       .venv\Scripts\pip install -r hermes\requirements.txt"
  ${EndIf}
FunctionEnd


; ══════════════════════════════════════════════════════════
;  HELPER: SeedConfigFiles
;  Copies missing config files to ~/.hermes/
; ══════════════════════════════════════════════════════════
Function SeedConfigFiles
  ; Ensure HERMES_HOME exists
  CreateDirectory "$PROFILE\.hermes"

  ; config.yaml — only create if missing
  ${IfNot} ${FileExists} "$PROFILE\.hermes\config.yaml"
    ${If} ${FileExists} "$INSTDIR\templates\config-template.yaml"
      CopyFiles /SILENT "$INSTDIR\templates\config-template.yaml" \
                         "$PROFILE\.hermes\config.yaml"
      DetailPrint "  Created config.yaml from template."
    ${EndIf}
  ${Else}
    DetailPrint "  config.yaml already exists — preserved."
  ${EndIf}

  ; SOUL.md — seeded from template; user can edit later
  ${IfNot} ${FileExists} "$PROFILE\.hermes\SOUL.md"
    ${If} ${FileExists} "$INSTDIR\templates\SOUL-template.md"
      CopyFiles /SILENT "$INSTDIR\templates\SOUL-template.md" \
                         "$PROFILE\.hermes\SOUL.md"
      DetailPrint "  Seeded SOUL.md (character template). Customize with your own character!"
    ${EndIf}
  ${Else}
    DetailPrint "  SOUL.md already exists — preserved."
  ${EndIf}

  ; CORTEX.md
  ${IfNot} ${FileExists} "$PROFILE\.hermes\CORTEX.md"
    ${If} ${FileExists} "$INSTDIR\templates\CORTEX.md"
      CopyFiles /SILENT "$INSTDIR\templates\CORTEX.md" \
                         "$PROFILE\.hermes\CORTEX.md"
      DetailPrint "  Seeded CORTEX.md."
    ${EndIf}
  ${Else}
    DetailPrint "  CORTEX.md already exists — preserved."
  ${EndIf}

  ; CEREBELLUM.md
  ${IfNot} ${FileExists} "$PROFILE\.hermes\CEREBELLUM.md"
    ${If} ${FileExists} "$INSTDIR\templates\CEREBELLUM.md"
      CopyFiles /SILENT "$INSTDIR\templates\CEREBELLUM.md" \
                         "$PROFILE\.hermes\CEREBELLUM.md"
      DetailPrint "  Seeded CEREBELLUM.md."
    ${EndIf}
  ${Else}
    DetailPrint "  CEREBELLUM.md already exists — preserved."
  ${EndIf}

  ; .env template
  ${IfNot} ${FileExists} "$PROFILE\.hermes\.env"
    ${If} ${FileExists} "$INSTDIR\templates\.env.template"
      CopyFiles /SILENT "$INSTDIR\templates\.env.template" \
                         "$PROFILE\.hermes\.env"
      DetailPrint "  Seeded .env (edit to add your API keys!)."
    ${EndIf}
  ${EndIf}

  ; Ensure knowledge/ directory exists
  CreateDirectory "$PROFILE\.hermes\knowledge"
FunctionEnd


; ══════════════════════════════════════════════════════════
;  UNINSTALLER
; ══════════════════════════════════════════════════════════
Section "Uninstall"

  ; ── Ask if user wants to keep personal data ──
  MessageBox MB_YESNO|MB_ICONQUESTION \
    "Keep your bot data?$\n$\n\
     This includes:$\n  \
     • Chat memories (memory_store.db)$\n  \
     • Session state (state.db)$\n  \
     • Config (config.yaml, SOUL.md, CORTEX.md)$\n  \
     • Knowledge base files$\n$\n\
     All in: $PROFILE\.hermes\$\n$\n\
     Click YES to keep data (recommended for reinstall).$\n\
     Click NO to delete everything." \
    IDYES keep_userdata

  ; Delete ~/.hermes/
  IfFileExists "$PROFILE\.hermes\*.*" 0 keep_userdata
  RMDir /r "$PROFILE\.hermes"
  DetailPrint "Removed user data: $PROFILE\.hermes"

keep_userdata:

  ; ── Remove installed directories ──
  RMDir /r "$INSTDIR\.venv"
  RMDir /r "$INSTDIR\hermes"
  RMDir /r "$INSTDIR\templates"
  RMDir /r "$INSTDIR\modules"
  RMDir /r "$INSTDIR\scripts"
  RMDir /r "$INSTDIR\napcat"
  RMDir /r "$INSTDIR\node"
  RMDir /r "$INSTDIR\assets"

  ; ── Remove loose files ──
  Delete "$INSTDIR\start.bat"
  Delete "$INSTDIR\Stop-All.bat"
  Delete "$INSTDIR\FixNapCat.bat"
  Delete "$INSTDIR\install.bat"
  Delete "$INSTDIR\VERSION"
  Delete "$INSTDIR\LICENSE"
  Delete "$INSTDIR\README.md"
  Delete "$INSTDIR\CHANGELOG.md"
  Delete "$INSTDIR\UPGRADE.md"
  Delete "$INSTDIR\AGENTS.md"
  Delete "$INSTDIR\配置API.bat"
  Delete "$INSTDIR\Uninstall.exe"

  ; ── Remove install directory (fails safely if not empty) ──
  RMDir "$INSTDIR"

  ; ── Remove Start Menu shortcuts ──
  RMDir /r "$SMPROGRAMS\${PRODUCT_NAME}"

  ; ── Remove from Windows uninstall registry ──
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}"

  DetailPrint "Hermes QQ Bot uninstalled."
SectionEnd


; ══════════════════════════════════════════════════════════
;  UNINSTALLER CALLBACK
; ══════════════════════════════════════════════════════════
Function un.onUninstSuccess
  HideWindow
  MessageBox MB_OK|MB_ICONINFORMATION \
    "Hermes QQ Bot has been removed from your computer."
FunctionEnd

Function un.onInit
  MessageBox MB_YESNO|MB_ICONQUESTION \
    "Are you sure you want to uninstall Hermes QQ Bot?" \
    IDYES un_continue
  Abort
un_continue:
FunctionEnd
