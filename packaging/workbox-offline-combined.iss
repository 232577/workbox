#ifndef WorkstationInstaller
  #error Verified workstation installer is required
#endif
#ifndef OfficeInstaller
  #error Verified office installer is required
#endif
#ifndef WorkstationSHA256
  #error Workstation installer SHA-256 is required
#endif
#ifndef OfficeSHA256
  #error Office installer SHA-256 is required
#endif

[Setup]
#ifdef CombinedTrial
AppName=WorkBox 整套离线安装 隔离验收
#else
AppName=WorkBox 整套离线安装
#endif
AppVersion=1.0.0
AppPublisher=WorkBox
DefaultDirName={tmp}\WorkBoxCombined
CreateAppDir=no
DisableWelcomePage=no
DisableDirPage=yes
DisableProgramGroupPage=yes
Uninstallable=no
CreateUninstallRegKey=no
UsePreviousAppDir=no
UsePreviousSetupType=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible and not arm64
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
CloseApplications=no
RestartApplications=no
WizardStyle=modern
Compression=none
SolidCompression=no
OutputDir={#OutputDir}
OutputBaseFilename=WorkBox-1.0.0-Complete-Offline-x64

[Languages]
Name: "chinesesimp"; MessagesFile: "{#ChineseMessagesFile}"

[Messages]
WelcomeLabel2=本文件内含工作站和办公室服务器的完整安装器。请根据这台电脑的用途选择需要安装的部分。安装不需要联网。%n%n升级工作站前请先从原 WorkBox 面板退出。

[Types]
Name: "workstation"; Description: "安装工作站"
Name: "office"; Description: "安装办公室服务器"
Name: "both"; Description: "安装工作站和办公室服务器"
Name: "custom"; Description: "自定义"; Flags: iscustom

[Components]
Name: "workstation"; Description: "工作站"; Types: workstation both
Name: "office"; Description: "办公室服务器"; Types: office both

[Files]
Source: "{#WorkstationInstaller}"; DestDir: "{tmp}\workbox-child-installers"; DestName: "workstation.exe"; Components: workstation; Flags: deleteafterinstall
Source: "{#OfficeInstaller}"; DestDir: "{tmp}\workbox-child-installers"; DestName: "office.exe"; Components: office; Flags: deleteafterinstall

[Code]
var
  FinalStatus: Integer;
  Summary: String;
  RoleFailed: Boolean;

procedure InitializeWizard();
begin
  FinalStatus := 20;
  Summary := '安装未完成。请查看安装日志后重新运行。';
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  if WizardIsComponentSelected('workstation') or WizardIsComponentSelected('office') then
    Result := ''
  else
    Result := '请至少选择一项安装内容。';
end;

function CheckRole(const Role, Pin: String): Boolean;
var FileName: String;
begin
  FileName := ExpandConstant('{tmp}\workbox-child-installers\') + Role + '.exe';
  Result := False;
  if FileExists(FileName) then Result := CompareText(GetSHA256OfFile(FileName), Pin) = 0;
  if not Result then Log('Role ' + Role + ': checksum mismatch');
end;

function RunRole(const Role, Caption: String): String;
var Root, ChildPath, ChildLog, Arguments: String; Content: AnsiString; ExitCode, ShowState: Integer;
begin
  if not WizardIsComponentSelected(Role) then begin
    Log('Role ' + Role + ': skipped');
    Result := Caption + '未选择';
    exit;
  end;
  Root := ExpandConstant('{tmp}\workbox-child-installers');
  ChildPath := AddBackslash(Root) + Role + '.exe';
  ChildLog := AddBackslash(Root) + Role + '.log';
  Arguments := '/SP- /NORESTART /LOG="' + ChildLog + '"';
#ifdef CombinedTrial
  if Role = 'workstation' then Arguments := Arguments + ' /WORKBOXTRIAL=1 /NOICONS';
#endif
  ShowState := SW_SHOWNORMAL;
  if WizardSilent then begin
    Arguments := Arguments + ' /VERYSILENT /SUPPRESSMSGBOXES';
    ShowState := SW_HIDE;
  end;
  WizardForm.StatusLabel.Caption := '正在运行' + Caption + '安装器。';
  WizardForm.Repaint();
  Log('Launching role ' + Role);
  if not Exec(ChildPath, Arguments, Root, ShowState, ewWaitUntilTerminated, ExitCode) then begin
    RoleFailed := True;
    Log('Role ' + Role + ': failed to launch (' + IntToStr(ExitCode) + ')');
    Result := Caption + '未能打开安装器，请重新运行并仅选这一项。';
  end else if ExitCode <> 0 then begin
    RoleFailed := True;
    Log('Role ' + Role + ': failed (exit ' + IntToStr(ExitCode) + ')');
    Result := Caption + '未完成，退出码 ' + IntToStr(ExitCode) + '。可重新运行并仅选这一项。';
  end else if not LoadStringFromFile(ChildLog, Content) then begin
    RoleFailed := True;
    Log('Role ' + Role + ': failed (missing child log)');
    Result := Caption + '的安装结果无法核对，请检查后重试。';
  end else if Pos('CurStepChanged raised an exception.', String(Content)) <> 0 then begin
    RoleFailed := True;
    Log('Role ' + Role + ': failed (post-installation exception)');
    Result := Caption + '未通过安装后的检查。可重新运行并仅选这一项。';
  end else begin
    Log('Role ' + Role + ': success');
    Result := Caption + '安装完成。';
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var WorkstationValid, OfficeValid: Boolean; WorkstationResult, OfficeResult: String;
begin
  if CurStep <> ssPostInstall then exit;
  WorkstationValid := True;
  OfficeValid := True;
  if WizardIsComponentSelected('workstation') then WorkstationValid := CheckRole('workstation', '{#WorkstationSHA256}');
  if WizardIsComponentSelected('office') then OfficeValid := CheckRole('office', '{#OfficeSHA256}');
  if not (WorkstationValid and OfficeValid) then begin
    FinalStatus := 21;
    Summary := '安装器校验失败，本次没有运行任何角色的安装器。请重新取得整套安装包。';
  end else begin
    WorkstationResult := RunRole('workstation', '工作站');
    OfficeResult := RunRole('office', '办公室服务器');
    Summary := WorkstationResult + #13#10 + #13#10 + OfficeResult;
    if RoleFailed then FinalStatus := 22
    else FinalStatus := 0;
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then begin
    WizardForm.FinishedLabel.Caption := Summary;
    if FinalStatus <> 0 then WizardForm.FinishedHeadingLabel.Caption := '部分或全部安装未完成';
  end;
end;

function GetCustomSetupExitCode(): Integer;
begin
  Result := FinalStatus;
end;
