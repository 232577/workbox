; 干净安装包只读取构建脚本已验证的 payload，不读取正式目录的 data。
#ifndef PayloadDir
  #error 请通过 scripts/build_installer.py 生成并验证安装载荷。
#endif
#ifndef OutputDir
  #error 必须指定独立的输出目录。
#endif
#ifndef PayloadManifestSHA256
  #error 必须提供本次载荷清单的 SHA-256。
#endif
#ifndef UpdaterSHA256
  #error 必须提供本次独立更新助手的 SHA-256。
#endif
#if !FileExists(PayloadDir + "\WorkBoxUpdater.exe")
  #error 新安装载荷必须包含已校验的独立更新助手。
#endif

[Setup]
AppId={{94062298-8991-4FF2-9E56-6B837B20A5FD}
AppName=WorkBox 工作箱
AppVersion=1.0.0
AppVerName=WorkBox 工作箱 1.0.0
AppPublisher=WorkBox
DefaultDirName={localappdata}\Programs\WorkBox
DefaultGroupName=WorkBox 工作箱
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible and not arm64
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
DisableProgramGroupPage=no
DisableDirPage=no
UsePreviousAppDir=yes
Uninstallable=yes
UninstallDisplayIcon={app}\WorkBox.exe
CloseApplications=no
RestartApplications=no
SetupIconFile={#AppIcon}
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
OutputDir={#OutputDir}
OutputBaseFilename=WorkBox-1.0.0-Setup-x64

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Messages]
WelcomeLabel2=本向导将在你的电脑安装 WorkBox 工作箱。%n%n安装包不包含原电脑的项目、工作文件或交付记录，首次启动自动建立空白数据目录。%n%n建议使用默认安装位置。更新已有安装前，请先从工作箱点击“退出”。

[Tasks]
Name: "desktopicon"; Description: "在桌面创建工作箱快捷方式"; Flags: checkedonce

[Files]
; 把助手放在压缩流前面，安装前即可提取并检查；只安装一份。
Source: "{#PayloadDir}\WorkBoxUpdater.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Excludes: "\WorkBoxUpdater.exe"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\WorkBox 工作箱"; Filename: "{app}\WorkBox.exe"; WorkingDir: "{app}"
Name: "{group}\安装与使用说明"; Filename: "{app}\安装说明.txt"
Name: "{group}\卸载 WorkBox 工作箱"; Filename: "{uninstallexe}"
Name: "{autodesktop}\WorkBox 工作箱"; Filename: "{app}\WorkBox.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\WorkBox.exe"; Parameters: "--panel"; Description: "启动 WorkBox 工作箱"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent; Check: CanLaunchWorkBox

[Code]
var
  OnlineTargetDir: String;
  RuntimeFinalized: Boolean;
  HelperReportCounter: Integer;

function IsOnlineUpdate: Boolean;
begin
  Result := ExpandConstant('{param:WORKBOXUPDATE|0}') = '1';
end;

function CanLaunchWorkBox: Boolean;
begin
  Result := RuntimeFinalized and not IsOnlineUpdate;
end;

function QuotedPath(Value: String): String;
var
  Index: Integer;
begin
  { Windows 命令行末尾的反斜线必须加倍，避免把结束引号当作路径文字。 }
  Result := Value;
  Index := Length(Value);
  while Index > 0 do begin
    if Value[Index] <> '\' then break;
    Result := Result + '\';
    Index := Index - 1;
  end;
  Result := '"' + Result + '"';
end;

function RunVerifiedHelper(const HelperPath, Arguments: String): Boolean;
var
  ExitCode: Integer;
  ReportFile: String;
  ReportText: AnsiString;
  ReportInfo: TFindRec;
begin
  Result := False;
  if CompareText(GetSHA256OfFile(HelperPath), '{#UpdaterSHA256}') <> 0 then begin
    Log('The full-install helper digest differs; no helper executed.');
    exit;
  end;
  HelperReportCounter := HelperReportCounter + 1;
  ReportFile := ExpandConstant('{tmp}\workbox-full-install-') + IntToStr(HelperReportCounter) + '.json';
  if FileExists(ReportFile) then begin
    Log('Existing installer diagnostic retained; no helper executed.');
    exit;
  end;
  if not Exec(HelperPath, Arguments + ' --install-report ' + QuotedPath(ReportFile),
      '', SW_HIDE, ewWaitUntilTerminated, ExitCode) then begin
    Log('The full-install helper could not start: ' + IntToStr(ExitCode));
    exit;
  end;
  { 诊断只进入安装日志，不用它替代助手退出码决定成功。 }
  if FindFirst(ReportFile, ReportInfo) then begin
    try
      if (ReportInfo.SizeHigh = 0) and (ReportInfo.SizeLow <= 16384) then
        if LoadStringFromFile(ReportFile, ReportText) then
          Log('Full-install diagnostic: ' + UTF8Decode(ReportText));
    finally
      FindClose(ReportInfo);
    end;
  end;
  Log('Full-install helper exit code: ' + IntToStr(ExitCode));
  Result := ExitCode = 0;
end;

function InitializeSetup: Boolean;
begin
  RuntimeFinalized := False;
  HelperReportCounter := 0;
  Result := True;
  if IsOnlineUpdate then begin
    OnlineTargetDir := ExpandConstant('{param:DIR|}');
    Result := (OnlineTargetDir <> '') and
      (ExtractFileDrive(OnlineTargetDir) <> '') and
      FileExists(AddBackslash(OnlineTargetDir) + 'WorkBox.exe');
    if not Result then
      MsgBox('在线升级必须指定已有的原安装目录，未执行安装。', mbError, MB_OK);
  end;
end;

procedure InitializeWizard;
begin
  if IsOnlineUpdate then begin
    WizardForm.DirEdit.Text := OnlineTargetDir;
    WizardForm.DirEdit.Enabled := False;
    WizardForm.DirBrowseButton.Enabled := False;
  end;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := IsOnlineUpdate and (PageID = wpSelectDir);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  HelperPath: String;
begin
  Result := '';
  if IsOnlineUpdate and
    (CompareText(ExpandFileName(ExpandConstant('{app}')), ExpandFileName(OnlineTargetDir)) <> 0) then
    Result := '在线升级目标与原安装目录不一致，已停止安装。';
  if Result <> '' then exit;
  try
    ExtractTemporaryFile('WorkBoxUpdater.exe');
    HelperPath := ExpandConstant('{tmp}\WorkBoxUpdater.exe');
    if not RunVerifiedHelper(HelperPath, '--preflight-full-install --app-dir ' +
        QuotedPath(ExpandConstant('{app}')) + ' --expected-manifest-sha256 ' +
        '{#PayloadManifestSHA256}' + ' --noninteractive') then
      Result := '安装前检查未通过。请先退出 WorkBox；若存在未完成的程序更新，请保留恢复记录并先完成恢复。';
  except
    Log(GetExceptionMessage);
    Result := '无法完成安装前检查，尚未替换程序文件。请保留安装日志。';
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Arguments: String;
begin
  if CurStep = ssPostInstall then begin
    try
      Arguments := '--finalize-full-install --app-dir ' + QuotedPath(ExpandConstant('{app}')) +
        ' --expected-manifest-sha256 ' + '{#PayloadManifestSHA256}' + ' --noninteractive';
      RuntimeFinalized := RunVerifiedHelper(ExpandConstant('{app}\WorkBoxUpdater.exe'), Arguments);
    except
      Log(GetExceptionMessage);
      RuntimeFinalized := False;
    end;
    if not RuntimeFinalized then begin
      { 已安装文件不在此处自动回滚；助手的原文件和续接计划必须保留。 }
      Log('Program files installed; runtime finalization incomplete. Recovery records retained.');
      if not WizardSilent then
        SuppressibleMsgBox('程序文件已安装，但运行库整理尚未完成。未删除未知文件，也未回滚数据库。' + #13#10 +
          '请保留安装日志和程序目录中的恢复记录，处理后重新运行此安装包；暂不自动启动 WorkBox。',
          mbError, MB_OK, IDOK);
    end;
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (CurPageID = wpFinished) and not RuntimeFinalized then
    WizardForm.FinishedLabel.Caption := '程序文件已安装，但运行库整理尚未完成。请保留安装日志和恢复记录，处理后重新运行此安装包。';
end;

function GetCustomSetupExitCode: Integer;
begin
  if RuntimeFinalized then
    Result := 0
  else
    Result := 20;
end;

// 不登记 data，也不使用 InstallDelete / UninstallDelete / 卸载删除脚本。
// 程序运行后创建的 data 在卸载或重装时保留。
