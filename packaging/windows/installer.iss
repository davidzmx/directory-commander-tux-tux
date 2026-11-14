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

[CustomMessages]
spanish.ToolsCaption=Programas opcionales
spanish.ToolsDescription=FFmpeg y ExifTool amplían lo que {#AppName} puede mostrar
spanish.ToolsSubCaption=Ninguno es obligatorio: sin ellos el programa funciona igual, solo sin estas funciones.%n%n• FFmpeg saca fotogramas de los vídeos para sus miniaturas y vistas previas, y lee su duración, resolución y códecs.%n• ExifTool muestra en el panel de metadatos los datos de fotos y otros archivos: cámara, fecha de captura, GPS…%n%nSi ya están en el PATH, deja los campos vacíos: se encuentran solos. Puedes configurarlos después en Preferencias › General › Programas.
spanish.FFmpegPrompt=Carpeta que contiene ffmpeg.exe (opcional):
spanish.ExifToolPrompt=Carpeta que contiene exiftool.exe (opcional):
spanish.ExplorerCaption=Explorador de archivos
spanish.ExplorerDescription=Abrir las carpetas con {#AppName}
spanish.ExplorerSubCaption=Si lo eliges, las carpetas se abren en este programa en lugar del Explorador de archivos: al abrirlas desde el escritorio, desde otros programas o con el Explorador. Solo cambia los ajustes de tu usuario y se deshace al desinstalar. La Papelera, el Panel de control y otras carpetas que no están en el disco siguen abriéndose en el Explorador.%n%nPuedes activarlo, desactivarlo o añadir carpetas que sigan abriéndose en el Explorador después, en Preferencias › Administrador de archivos.
spanish.ReplaceOption=Reemplazar al Explorador de archivos
spanish.WinEOption=Abrir también con Win+E
english.ToolsCaption=Optional programs
english.ToolsDescription=FFmpeg and ExifTool extend what {#AppName} can show
english.ToolsSubCaption=Neither is required: without them the program works the same, just without these features.%n%n• FFmpeg takes frames from videos for their thumbnails and previews, and reads their duration, resolution and codecs.%n• ExifTool shows the data of photos and other files in the metadata panel: camera, date taken, GPS…%n%nIf they are already in the PATH, leave the fields empty: they are found on their own. You can set them up later in Preferences › General › Programs.
english.FFmpegPrompt=Folder that contains ffmpeg.exe (optional):
english.ExifToolPrompt=Folder that contains exiftool.exe (optional):
english.ExplorerCaption=File Explorer
english.ExplorerDescription=Open folders with {#AppName}
english.ExplorerSubCaption=If you choose it, folders open in this program instead of File Explorer: when opened from the desktop, from other programs or from File Explorer. It only changes your user's settings and is undone when uninstalling. The Recycle Bin, Control Panel and other folders that are not on disk still open in File Explorer.%n%nYou can turn it on or off, or add folders that keep opening in File Explorer, later in Preferences › File manager.
english.ReplaceOption=Replace File Explorer
english.WinEOption=Also open with Win+E

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

[Code]
// Lo elegido en estas páginas lo aplica el propio programa al terminar, con las mismas funciones que sus preferencias.
var
  ToolsPage: TInputDirWizardPage;
  ExplorerPage: TInputOptionWizardPage;

procedure ExplorerOptionClicked(Sender: TObject);
begin
  ExplorerPage.CheckListBox.ItemEnabled[1] := ExplorerPage.Values[0];
end;

procedure InitializeWizard;
begin
  ToolsPage := CreateInputDirPage(wpSelectTasks, CustomMessage('ToolsCaption'), CustomMessage('ToolsDescription'), CustomMessage('ToolsSubCaption'), False, '');
  ToolsPage.Add(CustomMessage('FFmpegPrompt'));
  ToolsPage.Add(CustomMessage('ExifToolPrompt'));
  ExplorerPage := CreateInputOptionPage(ToolsPage.ID, CustomMessage('ExplorerCaption'), CustomMessage('ExplorerDescription'), CustomMessage('ExplorerSubCaption'), False, False);
  ExplorerPage.Add(CustomMessage('ReplaceOption'));
  ExplorerPage.Add(CustomMessage('WinEOption'));
  ExplorerPage.CheckListBox.OnClickCheck := @ExplorerOptionClicked;
  ExplorerOptionClicked(nil);
end;

// Una ruta entre comillas que acaba en \ escaparía la comilla de cierre: «C:\» se pasa como «C:\.».
function FolderArgument(Name, Folder: String): String;
begin
  Folder := Trim(Folder);
  Result := '';
  if Folder = '' then
    exit;
  if Folder[Length(Folder)] = '\' then
    Folder := Folder + '.';
  Result := ' ' + Name + ' ' + AddQuotes(Folder);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Params: String;
  ResultCode: Integer;
begin
  if CurStep <> ssPostInstall then
    exit;
  Params := FolderArgument('--ffmpeg-dir', ToolsPage.Values[0]) + FolderArgument('--exiftool-dir', ToolsPage.Values[1]);
  if ExplorerPage.Values[0] then
  begin
    Params := Params + ' --replace-file-manager';
    if ExplorerPage.Values[1] then
      Params := Params + ' --win-e';
  end;
  // Con el usuario que lanzó el instalador: si se instala para todos, como administrador, sus ajustes y su registro son los de ese usuario.
  if Params <> '' then
    ExecAsOriginalUser(ExpandConstant('{app}\{#AppId}.exe'), Trim(Params), '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;
