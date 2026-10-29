; ViDeck Pulse — instalador (Inno Setup 6). Compilar:  ISCC.exe /DAppVersion=2.0.0 installer\ViDeckPulse.iss
; Por defecto se instala para todos los usuarios en «Archivos de programa» (pide permiso de administrador); al empezar, el instalador deja elegir
; «solo para mí», que instala sin administrador en %LOCALAPPDATA%\Programs\ViDeck Pulse. Los datos del usuario van siempre a %APPDATA%\ViDeck Pulse
; (config.json, tracks_db.json, componentes descargados…), así que actualizar o desinstalar no los toca salvo que el usuario lo pida.
; Se compila y prueba (instalación y desinstalación silenciosas) en GitHub Actions: .github/workflows/release.yml.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{2346AE5F-C9FD-4434-8134-5459A0C6A696}
AppName=ViDeck Pulse
AppVersion={#AppVersion}
AppPublisher=JFX
DefaultDirName={autopf}\ViDeck Pulse
DefaultGroupName=ViDeck Pulse
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=ViDeckPulse-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\ViDeckPulse.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\LICENSE
ShowLanguageDialog=yes
LanguageDetectionMethod=uilanguage

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked

[Files]
Source: "..\dist\ViDeckPulse\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\ViDeck Pulse"; Filename: "{app}\ViDeckPulse.exe"
Name: "{autodesktop}\ViDeck Pulse"; Filename: "{app}\ViDeckPulse.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ViDeckPulse.exe"; Description: "{cm:LaunchProgram,ViDeck Pulse}"; Flags: nowait postinstall skipifsilent

[Code]
// El idioma que se elige al empezar la instalación pasa a ser el idioma de ViDeck Pulse: se deja anotado en la carpeta de datos
// (solo en una instalación nueva: si ya hay config.json, el programa conserva el idioma que el usuario tenía).
procedure CurStepChanged(CurStep: TSetupStep);
var
  DataDir, Code: String;
begin
  if CurStep = ssPostInstall then
  begin
    DataDir := ExpandConstant('{userappdata}\ViDeck Pulse');
    if not FileExists(DataDir + '\config.json') then
    begin
      if ActiveLanguage = 'english' then Code := 'en' else Code := 'es';
      ForceDirectories(DataDir);
      SaveStringToFile(DataDir + '\install_language.txt', Code, False);
    end;
  end;
end;

// Al desinstalar: ¿conservar los datos del usuario (vínculos, ajustes, componentes descargados)?
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{userappdata}\ViDeck Pulse');
    if DirExists(DataDir) then
      if MsgBox('¿Quieres borrar también tus datos de ViDeck Pulse (vínculos, ajustes y componentes descargados)?' + #13#10 +
                'Do you also want to delete your ViDeck Pulse data (links, settings and downloaded components)?' + #13#10#13#10 + DataDir,
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
