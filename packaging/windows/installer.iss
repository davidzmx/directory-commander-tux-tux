; Instalador de Windows. Se genera con: python packaging/build.py installer
; Para el usuario actual, sin permisos de administrador. Quien quiera puede elegir instalarlo para todos.
#define AppName "Directory Commander Tux Tux"
#define AppId "directory-commander-tux-tux"
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{7E3C9B52-4A1D-4F6E-9C28-5D1B7A3F8E60}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\dist
OutputBaseFilename={#AppId}-{#AppVersion}-windows-setup
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#AppId}.exe
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\..\LICENSE
CloseApplications=yes

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\..\dist\{#AppId}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppId}.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppId}.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppId}.exe"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Si reemplazaba al Explorador de archivos, lo devuelve: si no, las carpetas dejarían de abrirse.
Filename: "{app}\{#AppId}.exe"; Parameters: "--restore-file-manager"; Flags: runhidden; RunOnceId: "RestoreFileManager"
