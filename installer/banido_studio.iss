; Script do Inno Setup (https://jrsoftware.org/isinfo.php) que gera BanidoStudio-Setup.exe.
; O .exe e pequeno: leva so o codigo. Python, PyTorch, FFmpeg e MKVToolNix sao baixados na instalacao.
; Build local:  ISCC /DAppVersion=0.1.0 installer\banido_studio.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{B7E0C8A4-5D61-4E0B-9C9B-BA41D0570A01}
AppName=Banido Studio AI
AppVersion={#AppVersion}
AppPublisher=Banido Studio
DefaultDirName={localappdata}\BanidoStudio
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=build
OutputBaseFilename=BanidoStudio-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=Banido Studio AI

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Files]
Source: "..\*"; DestDir: "{app}\fonte"; Excludes: ".git\*,.venv\*,venv\*,data\*,installer\build\*,__pycache__\*,.env"; Flags: recursesubdirs createallsubdirs ignoreversion

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\fonte\installer\Instalar.ps1"" -InstallDir ""{app}"" -FonteDir ""{app}\fonte"""; StatusMsg: "Baixando e configurando Python, IA, FFmpeg e MKVToolNix (pode levar varios minutos)..."; Flags: waituntilterminated

[UninstallDelete]
; A pasta codigo\data (gravacoes, banco, cortes) NAO e removida.
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\tools"
Type: filesandordirs; Name: "{app}\cache"
Type: filesandordirs; Name: "{app}\fonte"
Type: files; Name: "{userdesktop}\Banido Studio AI.lnk"
