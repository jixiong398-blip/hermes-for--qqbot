; ============================================================
;  Hermes QQ Bot 鈥?Inno Setup Installer
;  Build: ISCC.exe build-installer.iss
; ============================================================

#define MyAppName     "Hermes QQ Bot"
#define MyAppVersion  "0.10.0"
#define MyAppPublisher "Hermes QQ Bot"
#define MyAppURL      "https://github.com/jixiong398-blip/hermes-for--qqbot"
#define MyAppExeName  "start.bat"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={localappdata}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=.
OutputBaseFilename=HermesQQBot-{#MyAppVersion}-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
UninstallDisplayName={#MyAppName} {#MyAppVersion}
VersionInfoVersion={#MyAppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Types]
Name: "full";    Description: "Full installation (recommended)"
Name: "compact"; Description: "Core only (no Live2D)"
Name: "custom";  Description: "Custom installation"; Flags: iscustom

[Components]
Name: "core";    Description: "Hermes Core Engine (required)"; Types: full compact custom; Flags: fixed
Name: "live2d";  Description: "Live2D Desktop Pet (Cubism 4/5, ~320MB)"; Types: full
Name: "napcat";  Description: "NapCat QQ Bridge"; Types: full compact
Name: "dash";    Description: "Web Dashboard"; Types: full compact; Flags: fixed

[Files]
; 鈹€鈹€ Core scripts 鈹€鈹€
Source: "install.bat";      DestDir: "{app}"; Flags: ignoreversion
Source: "start.bat";        DestDir: "{app}"; Flags: ignoreversion
Source: "Stop-All.bat";     DestDir: "{app}"; Flags: ignoreversion
Source: "FixNapCat.bat";    DestDir: "{app}"; Flags: ignoreversion
Source: "VERSION";          DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE";          DestDir: "{app}"; Flags: ignoreversion
Source: "README.md";        DestDir: "{app}"; Flags: ignoreversion
Source: "CHANGELOG.md";     DestDir: "{app}"; Flags: ignoreversion
Source: "UPGRADE.md";       DestDir: "{app}"; Flags: ignoreversion

; 鈹€鈹€ Offline packages 鈹€鈹€
Source: "python-installer.exe"; DestDir: "{app}"; Flags: ignoreversion; Components: core
Source: "nodejs.zip";           DestDir: "{app}"; Flags: ignoreversion; Components: live2d
Source: "electron-offline.zip.001"; DestDir: "{app}"; Flags: ignoreversion; Components: live2d
Source: "electron-offline.zip.002"; DestDir: "{app}"; Flags: ignoreversion; Components: live2d

; 鈹€鈹€ Scripts 鈹€鈹€
Source: "scripts\*";        DestDir: "{app}\scripts"; Flags: ignoreversion recursesubdirs; Components: core

; 鈹€鈹€ Templates 鈹€鈹€
Source: "templates\*";      DestDir: "{app}\templates"; Flags: ignoreversion recursesubdirs; Components: core

; 鈹€鈹€ Hermes engine 鈹€鈹€
Source: "hermes\*";         DestDir: "{app}\hermes"; Flags: ignoreversion recursesubdirs; Components: core; Excludes: "*.pyc,__pycache__\*,*.db,*.log,.git\*"

; 鈹€鈹€ Modules 鈹€鈹€
Source: "modules\dashboard\*"; DestDir: "{app}\modules\dashboard"; Flags: ignoreversion recursesubdirs; Components: dash
Source: "modules\live2d\*";    DestDir: "{app}\modules\live2d";    Flags: ignoreversion recursesubdirs; Components: live2d
Source: "modules\knowledge\.gitkeep"; DestDir: "{app}\modules\knowledge"; Flags: ignoreversion; Components: core

; 鈹€鈹€ NapCat 鈹€鈹€
Source: "napcat\*"; DestDir: "{app}\napcat"; Flags: ignoreversion recursesubdirs; Components: napcat; Excludes: "napcat\napcat\cache\,napcat\napcat\logs\,napcat\napcat\config\"

; 鈹€鈹€ Node.js portable (bundle from zip) 鈹€鈹€
Source: "nodejs.zip";       DestDir: "{app}"; Flags: ignoreversion; Components: live2d

[Icons]
Name: "{group}\Launch Dashboard";  Filename: "{app}\start.bat"; WorkingDir: "{app}"
Name: "{group}\Configure API Key"; Filename: "{app}\\PeiZhiAPI.bat"; WorkingDir: "{app}"
Name: "{group}\Fix NapCat Ports";  Filename: "{app}\FixNapCat.bat"; WorkingDir: "{app}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\start.bat"; WorkingDir: "{app}"; Comment: "Launch Hermes QQ Bot"

[Run]
Filename: "{app}\install.bat"; Description: "Run setup script (install Python/venv/deps)"; Flags: postinstall nowait skipifsilent shellexec
Filename: "{app}\README.md"; Description: "View README"; Flags: postinstall nowait skipifsilent shellexec

[UninstallDelete]
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\node"
Type: files; Name: "{app}\electron-offline.zip"

[Code]
function InitializeSetup: Boolean;
begin
  Result := True;
end;

function InitializeUninstall: Boolean;
begin
  if MsgBox('鏄惁淇濈暀鐢ㄦ埛鏁版嵁锛坈onfig.yaml, SOUL.md, .env锛夛紵', mbConfirmation, MB_YESNO) = IDYES then
  begin
    // Files in %USERPROFILE%\.hermes\ are NOT in install dir,
    // so they're automatically preserved.
  end;
  Result := True;
end;
