; DouyinCut Windows Installer Script for Inno Setup 6
; Generates a current-user offline installer that preserves work data on uninstall

#define AppName "DouyinCut"
#ifndef AppVersion
  #error AppVersion must be supplied by the build script from SPEC.md
#endif
#define AppPublisher "DouyinCut"
#define AppURL "https://github.com/Anony0S/DouyinCut"
#define AppExeName "douyincut.exe"
#define RuntimeDirName "DouyinCutRuntime"

[Setup]
AppId={{8F3E7A2B-4C9D-4E6A-9B1F-2D5C8A7E6F3B}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
; Current user install - no admin required
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\..\desktop\src-tauri\target\release\installer
OutputBaseFilename=DouyinCut-{#AppVersion}-setup
SetupIconFile=..\..\desktop\src-tauri\icons\icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Updates use the app's own graceful shutdown, never Restart Manager termination.
CloseApplications=no
RestartApplications=no
; Preserve work data on uninstall
UninstallFilesDir={app}\uninst

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Main executable
Source: "..\..\desktop\src-tauri\target\release\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; Frozen runtime
Source: "..\..\desktop\src-tauri\runtime\{#RuntimeDirName}\*"; DestDir: "{app}\runtime"; Flags: ignoreversion recursesubdirs createallsubdirs
; Media tools (FFmpeg)
Source: "..\..\desktop\src-tauri\runtime\media\*"; DestDir: "{app}\media"; Flags: ignoreversion recursesubdirs createallsubdirs
; Frontend assets
Source: "..\..\desktop\dist\*"; DestDir: "{app}\frontend"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
#include "update.iss"

function InitializeSetup(): Boolean;
begin
  Result := WaitForUpdateExit();
  if not Result then
    Exit;

  // Check if the application is running
  if CheckForMutexes('Local\DouyinCut-InstallerGuard') then
  begin
    Result := False;
    MsgBox('DouyinCut 正在运行。请先完全退出，再重新运行安装程序。', mbError, MB_OK);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if CheckForMutexes('Local\DouyinCut-InstallerGuard') then
    Result := 'DouyinCut 正在运行。请完全退出后重试安装。';
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;

  // Check if the application is running during uninstall
  if CheckForMutexes('Local\DouyinCut-InstallerGuard') then
  begin
    Result := False;
    MsgBox('DouyinCut 正在运行。请先完全退出，再重新运行卸载程序。', mbError, MB_OK);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  WorkDirConfigPath: String;
  WorkDir: String;
  ResultCode: Integer;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    // Preserve work directory data, settings, and credentials
    // Work directory location is stored in user config, not in app directory
    // So uninstalling the app does NOT delete user data

    if MsgBox('是否保留所有项目数据、录制文件和设置？' + #13#10 +
              '选择"是"将保留工作目录数据（推荐）。' + #13#10 +
              '选择"否"将仅删除程序文件，工作数据仍需手动删除。',
              mbConfirmation, MB_YESNO or MB_DEFBUTTON1) = IDYES then
    begin
      // User chose to keep data - do nothing
      MsgBox('程序文件已删除。' + #13#10 +
             '您的项目数据、录制文件和设置已保留。',
             mbInformation, MB_OK);
    end
    else
    begin
      // User wants guidance on manual deletion
      MsgBox('程序文件已删除。' + #13#10 +
             '如需删除工作数据，请打开工作目录并手动删除。' + #13#10 +
             '工作目录位置保存在：' + #13#10 +
             ExpandConstant('{localappdata}\DouyinCut\config\workspace.json'),
             mbInformation, MB_OK);
    end;
  end;
end;
