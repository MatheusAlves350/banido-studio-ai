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

[Tasks]
Name: "desktopicon"; Description: "Criar um atalho na Area de Trabalho"; GroupDescription: "Atalhos:"

[Files]
Source: "..\*"; DestDir: "{app}\fonte"; Excludes: ".git\*,.venv\*,venv\*,data\*,installer\build\*,__pycache__\*,.env"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Banido Studio AI"; Filename: "{app}\Banido_Studio.bat"; WorkingDir: "{app}"
Name: "{autodesktop}\Banido Studio AI"; Filename: "{app}\Banido_Studio.bat"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; Uma janela de console mostra o andamento (downloads grandes: 10 a 30 minutos). Nao fechar.
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\fonte\installer\Instalar.ps1"" -InstallDir ""{app}"" -FonteDir ""{app}\fonte"" -DadosDir ""{code:DadosDir}"" -SemAtalho -SemPerguntas"; StatusMsg: "Baixando e configurando Python, IA, FFmpeg e MKVToolNix (pode levar varios minutos)..."; Flags: waituntilterminated
Filename: "{app}\Banido_Studio.bat"; Description: "Abrir o Banido Studio AI agora"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent shellexec

[UninstallRun]
; Remove programa, bibliotecas e atalhos. Os dados do usuario (gravacoes, cortes, banco, modelos) sao MANTIDOS.
; Para apagar tambem os dados, use Desinstalar_Banido_Studio.bat e digite APAGAR quando perguntado.
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\codigo\installer\Desinstalar.ps1"" -InstallDir ""{app}"" -Quieto"; Flags: waituntilterminated runhidden; RunOnceId: "RemoverBanidoStudio"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\tools"
Type: filesandordirs; Name: "{app}\cache"
Type: filesandordirs; Name: "{app}\fonte"

[Code]
var
  DadosPage: TInputDirWizardPage;

// Letra do disco com mais espaco livre (ignora A: e B:).
function MelhorDisco(): String;
var
  Letra: Char;
  Livre, Total, Melhor: Cardinal;
begin
  Result := 'C';
  Melhor := 0;
  for Letra := 'C' to 'Z' do
  begin
    if GetSpaceOnDisk(Letra + ':\', True, Livre, Total) then
      if Livre > Melhor then
      begin
        Melhor := Livre;
        Result := Letra;
      end;
  end;
end;

procedure InitializeWizard;
begin
  DadosPage := CreateInputDirPage(wpSelectDir,
    'Pasta de dados',
    'Onde guardar gravacoes, cortes e modelos de IA?',
    'Escolha uma pasta em um disco com bastante espaco livre (recomendado: 15 GB ou mais, de preferencia SSD). ' +
    'Ja sugerimos o disco com mais espaco; para trocar, clique em Procurar. ' +
    'Durante a instalacao uma janela preta mostra o andamento: nao feche.',
    False, '');
  DadosPage.Add('');
  DadosPage.Values[0] := MelhorDisco() + ':\BanidoStudio\data';
end;

function DadosDir(Param: String): String;
begin
  Result := RemoveBackslash(DadosPage.Values[0]);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Livre, Total: Cardinal;
  Pasta: String;
begin
  Result := True;

  if CurPageID = wpSelectDir then
  begin
    if GetSpaceOnDisk(ExtractFileDrive(WizardDirValue) + '\', True, Livre, Total) then
      if Livre < 10240 then
        Result := MsgBox('O disco do programa tem apenas ' + IntToStr(Livre div 1024) +
          ' GB livres (sao necessarios cerca de 10 GB). Continuar mesmo assim?',
          mbConfirmation, MB_YESNO) = IDYES;
  end;

  if CurPageID = DadosPage.ID then
  begin
    Pasta := DadosPage.Values[0];
    if (Length(Pasta) < 3) or (Pasta[2] <> ':') then
    begin
      MsgBox('Escolha uma pasta completa, por exemplo E:\BanidoStudio\data.', mbError, MB_OK);
      Result := False;
    end
    else if GetSpaceOnDisk(ExtractFileDrive(Pasta) + '\', True, Livre, Total) then
      if Livre < 15360 then
        Result := MsgBox('A pasta escolhida tem apenas ' + IntToStr(Livre div 1024) +
          ' GB livres (recomendado: 15 GB; o modelo Whisper sozinho usa 3 GB). Continuar mesmo assim?',
          mbConfirmation, MB_YESNO) = IDYES;
  end;
end;
