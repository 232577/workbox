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
#ifndef ChineseMessagesFile
  #define ChineseMessagesFile "compiler:Languages\ChineseSimplified.isl"
#endif
#ifdef WorkstationPayload
  #if WorkstationPayload != "1"
    #error WorkstationPayload must be 1
  #endif
  #ifdef TeamServerPayloadDir
    #error WorkstationPayload cannot include TeamServerPayloadDir
  #endif
  #ifdef ChromiumManifestSHA256
    #error WorkstationPayload cannot include standalone Chromium
  #endif
  #define IsolatedTrialSupport
#endif
#ifdef TeamServerPayloadDir
  #define IsolatedTrialSupport
#endif
#ifdef TeamServerPayloadDir
  #ifndef TeamServerManifestSHA256
    #error 缺少固定团队服务器安装清单的 SHA-256。
  #endif
  #ifndef TeamServerMaxAppDir
    #error 缺少团队服务器的 Windows 安装目录长度限额。
  #endif
  #ifndef TeamServerVersion
    #error 缺少团队服务器程序目录的构建标识。
  #endif
  #ifndef ChromiumManifestSHA256
    #error 统一安装包需要经过核验的离线 Chromium。
  #endif
  #ifndef TeamServerFilesManifest
    #error 缺少团队服务器的完整文件摘要。
  #endif
  #ifndef TeamServerFilesManifestSHA256
    #error 缺少团队服务器完整文件摘要的 SHA-256。
  #endif
  #define TeamServerFolder "{app}\TeamServer\" + TeamServerVersion
#endif

[Setup]
#ifdef IsolatedTrialSupport
AppId={code:WorkBoxAppId}
DefaultDirName={code:WorkBoxDefaultDirectory}
DefaultGroupName={code:WorkBoxDefaultGroup}
#else
AppId={{94062298-8991-4FF2-9E56-6B837B20A5FD}
DefaultDirName={localappdata}\Programs\WorkBox
DefaultGroupName=WorkBox 工作箱
#endif
AppName=WorkBox 工作箱
AppVersion=1.0.0
AppVerName=WorkBox 工作箱 1.0.0
AppPublisher=WorkBox
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
#ifdef IsolatedTrialSupport
UsePreviousSetupType=no
UsePreviousLanguage=no
#endif
#ifdef TeamServerPayloadDir
OutputBaseFilename=WorkBox-1.0.0-Team-Setup-x64
#else
#ifdef WorkstationPayload
OutputBaseFilename=WorkBox-1.0.0-Workstation-Setup-x64
#else
OutputBaseFilename=WorkBox-1.0.0-Setup-x64
#endif
#endif

[Languages]
Name: "chinesesimp"; MessagesFile: "{#ChineseMessagesFile}"

[Messages]
#ifdef WorkstationPayload
WelcomeLabel2=本向导安装 WorkBox 工作站，内置离线网页运行组件，用于“这是家”网页与办公室文件、统计面板。%n%n本包不含办公室服务器；服务器管理员请使用单独的服务器角色包。%n%n更新前请先在工作箱点击“退出”。个人材料与账号不随本安装包分发。
#else
#ifdef TeamServerPayloadDir
WelcomeLabel2=本向导安装 WorkBox 工作箱和团队交件面板。%n%n普通成员使用默认工作站。仅负责搭建办公室服务器的电脑需要选择服务器组件。%n%n安装不创建团队账号或启动服务器。更新前请先在工作箱点击“退出”。
#else
WelcomeLabel2=本向导将在你的电脑安装 WorkBox 工作箱。%n%n安装包不包含原电脑的项目、工作文件或交付记录，首次启动自动建立空白数据目录。%n%n建议使用默认安装位置。更新已有安装前，请先从工作箱点击“退出”。
#endif
#endif

#ifdef TeamServerPayloadDir
[Types]
Name: "workstation"; Description: "普通工作站（推荐）"
Name: "office"; Description: "工作站与办公室服务器"

[Components]
Name: "desktop"; Description: "WorkBox 工作箱与团队交件"; Types: workstation office; Flags: fixed
Name: "teamserver"; Description: "办公室服务器（本机管理）"; Types: office
#endif

[Tasks]
Name: "desktopicon"; Description: "在桌面创建工作箱快捷方式"; Flags: checkedonce

[Files]
; 把助手放在压缩流前面，安装前即可提取并检查；只安装一份。
Source: "{#PayloadDir}\WorkBoxUpdater.exe"; DestDir: "{app}"; Flags: ignoreversion
#ifdef TeamServerPayloadDir
Source: "{#TeamServerFilesManifest}"; DestName: "TEAM_SERVER_CONTENTS.sha256"; Flags: dontcopy
#endif
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Excludes: "\WorkBoxUpdater.exe"; Flags: ignoreversion recursesubdirs createallsubdirs
#ifdef TeamServerPayloadDir
Source: "{#TeamServerPayloadDir}\*"; DestDir: "{#TeamServerFolder}"; Components: teamserver; Check: ShouldCopyTeamServer; Flags: ignoreversion recursesubdirs createallsubdirs
#endif

[Icons]
Name: "{group}\WorkBox 工作箱"; Filename: "{app}\WorkBox.exe"; WorkingDir: "{app}"; Check: CanCreateShortcuts
Name: "{group}\安装与使用说明"; Filename: "{app}\安装说明.txt"; Check: CanCreateShortcuts
Name: "{group}\卸载 WorkBox 工作箱"; Filename: "{uninstallexe}"; Check: CanCreateShortcuts
Name: "{autodesktop}\WorkBox 工作箱"; Filename: "{app}\WorkBox.exe"; WorkingDir: "{app}"; Tasks: desktopicon; Check: CanCreateShortcuts
#ifdef TeamServerPayloadDir
Name: "{group}\办公室服务器本机管理"; Filename: "{#TeamServerFolder}\python312\python.exe"; Parameters: "-I -S -B ""{#TeamServerFolder}\scripts\kanboard_local_manager.py"" --selection-file ""{localappdata}\WorkBoxTeamServer\manager-selection.json"""; WorkingDir: "{#TeamServerFolder}"; Components: teamserver; Check: ShouldCreateTeamServerShortcuts
Name: "{group}\办公室服务器配置与启停说明"; Filename: "{#TeamServerFolder}\INSTALL-README.txt"; Components: teamserver; Check: ShouldCreateTeamServerShortcuts
#endif

[Run]
Filename: "{app}\WorkBox.exe"; Parameters: "--panel"; Description: "启动 WorkBox 工作箱"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent; Check: CanLaunchWorkBox

[Code]
var
  OnlineTargetDir: String;
  RuntimeFinalized: Boolean;
  HelperReportCounter: Integer;
#ifdef TeamServerPayloadDir
  ReuseTeamServer: Boolean;
#endif

function IsOnlineUpdate: Boolean;
begin
  Result := ExpandConstant('{param:WORKBOXUPDATE|0}') = '1';
end;

#ifdef WorkstationPayload
function WorkstationTrialMode: String;
begin
  Result := ExpandConstant('{param:WORKBOXTRIAL|}');
end;

function WorkstationTrialDirectory: String;
begin
  if WorkstationTrialMode = 'upgrade' then
    Result := ExpandConstant('{localappdata}\WorkBoxWorkstationUpgradeTrial\program')
  else
    Result := ExpandConstant('{localappdata}\WorkBoxWorkstationTrial\program');
end;
#endif

function IsLocalTrial: Boolean;
begin
#ifdef WorkstationPayload
  Result := (WorkstationTrialMode = '1') or (WorkstationTrialMode = 'upgrade');
#else
#ifdef IsolatedTrialSupport
  Result := ExpandConstant('{param:WORKBOXTRIAL|0}') = '1';
#else
  Result := False;
#endif
#endif
end;

function CanCreateShortcuts: Boolean;
begin
  Result := not IsLocalTrial;
end;

#ifdef IsolatedTrialSupport
function WorkBoxAppId(Param: String): String;
begin
  if IsLocalTrial then begin
#ifdef WorkstationPayload
    if WorkstationTrialMode = 'upgrade' then
      Result := '{5189504C-7ED9-4B90-A4AA-0DCA5BBFB196}'
    else
      Result := '{E806D856-21C2-4D98-BD71-D392DFE76CB8}';
#else
    Result := '{668F49C6-3FDD-4E14-AEAC-C561C9B1F3EB}';
#endif
  end else
    Result := '{94062298-8991-4FF2-9E56-6B837B20A5FD}';
end;

function WorkBoxDefaultDirectory(Param: String): String;
begin
  if IsLocalTrial then
#ifdef WorkstationPayload
    Result := WorkstationTrialDirectory
#else
    Result := ExpandConstant('{localappdata}\WorkBoxUnifiedTrial\program')
#endif
  else
    Result := ExpandConstant('{localappdata}\Programs\WorkBox');
end;

function WorkBoxDefaultGroup(Param: String): String;
begin
  if IsLocalTrial then
#ifdef WorkstationPayload
    Result := 'WorkBox 工作站隔离验收'
#else
    Result := 'WorkBox 联合安装验收'
#endif
  else
    Result := 'WorkBox 工作箱';
end;
#endif

#ifdef TeamServerPayloadDir
function ShouldInstallTeamServer: Boolean;
begin
  Result := not IsOnlineUpdate and WizardIsComponentSelected('teamserver');
end;

function ShouldCreateTeamServerShortcuts: Boolean;
begin
  Result := ShouldInstallTeamServer and CanCreateShortcuts;
end;

function ShouldCopyTeamServer: Boolean;
begin
  Result := ShouldInstallTeamServer and not ReuseTeamServer;
end;

function TeamServerManifestMatches: Boolean;
var
  Receipt: String;
begin
  Receipt := ExpandConstant('{#TeamServerFolder}\install-bundle.json');
  Result := FileExists(Receipt) and
    (CompareText(GetSHA256OfFile(Receipt), '{#TeamServerManifestSHA256}') = 0);
end;

#endif

#ifdef IsolatedTrialSupport
function WorkBoxPathAttributes(FileName: String): Cardinal;
  external 'GetFileAttributesW@kernel32.dll stdcall';

function IsReparsePoint(const Path: String): Boolean;
var
  Attributes: Cardinal;
begin
  Attributes := WorkBoxPathAttributes(Path);
  Result := (Attributes <> $FFFFFFFF) and ((Attributes and $400) <> 0);
end;
#endif

#ifdef WorkstationPayload
function NormalizedDirectory(const Path: String): String;
begin
  Result := RemoveBackslashUnlessRoot(ExpandFileName(Path));
end;

function HasAllUsersSwitch: Boolean;
var
  Index: Integer;
begin
  Result := False;
  for Index := 1 to ParamCount do
    if (CompareText(ParamStr(Index), '/ALLUSERS') = 0) or
       (CompareText(Copy(ParamStr(Index), 1, 10), '/ALLUSERS=') = 0) then begin
      Result := True;
      exit;
    end;
end;

function TrialAncestorsSafe: Boolean;
var
  Path, Parent: String;
begin
  Result := False;
  Path := WorkstationTrialDirectory;
  repeat
    if FileExists(Path) or IsReparsePoint(Path) then exit;
    Parent := ExtractFileDir(Path);
    if CompareText(Parent, Path) = 0 then break;
    Path := Parent;
  until Path = '';
  Result := True;
end;

function TrialTreeSafe(const Folder: String): Boolean;
var
  Entry: TFindRec;
begin
  Result := False;
  if IsReparsePoint(Folder) then exit;
  if not DirExists(Folder) then begin
    Result := not FileExists(Folder);
    exit;
  end;
  if not FindFirst(AddBackslash(Folder) + '*', Entry) then exit;
  try
    repeat
      if (Entry.Name <> '.') and (Entry.Name <> '..') then begin
        if (Entry.Attributes and $400) <> 0 then exit;
        if ((Entry.Attributes and $10) <> 0) and
           not TrialTreeSafe(AddBackslash(Folder) + Entry.Name) then exit;
      end;
    until not FindNext(Entry);
  finally
    FindClose(Entry);
  end;
  Result := True;
end;

function IsReservedTrialDirectory(const Directory: String): Boolean;
var
  Target, WorkstationRoot, UpgradeRoot, UnifiedRoot: String;
begin
  Target := AddBackslash(NormalizedDirectory(Directory));
  WorkstationRoot := AddBackslash(ExpandConstant('{localappdata}\WorkBoxWorkstationTrial'));
  UpgradeRoot := AddBackslash(ExpandConstant('{localappdata}\WorkBoxWorkstationUpgradeTrial'));
  UnifiedRoot := AddBackslash(ExpandConstant('{localappdata}\WorkBoxUnifiedTrial'));
  Result := (CompareText(Copy(Target, 1, Length(WorkstationRoot)), WorkstationRoot) = 0) or
    (CompareText(Copy(Target, 1, Length(UpgradeRoot)), UpgradeRoot) = 0) or
    (CompareText(Copy(Target, 1, Length(UnifiedRoot)), UnifiedRoot) = 0);
end;

function WorkstationPathPreflight(const Directory: String): String;
begin
  Result := '';
  if IsLocalTrial then begin
    if HasAllUsersSwitch or IsAdminInstallMode or
       (CompareText(NormalizedDirectory(Directory), WorkstationTrialDirectory) <> 0) or
       not TrialAncestorsSafe or not TrialTreeSafe(WorkstationTrialDirectory) then
      Result := '工作站验收只允许当前用户的专属程序目录，不能使用公共安装、文件、链接或联接目录。';
  end else if IsReservedTrialDirectory(Directory) then
    Result := '该目录专用于隔离验收。普通安装请选择原安装目录或默认工作箱目录。';
end;
#endif

#ifdef TeamServerPayloadDir
function CountTeamServerFiles(const Folder: String; var Count: Integer): Boolean;
var
  Entry: TFindRec;
  Path: String;
begin
  Result := False;
  if not FindFirst(AddBackslash(Folder) + '*', Entry) then exit;
  try
    repeat
      if (Entry.Name <> '.') and (Entry.Name <> '..') then begin
        if (Entry.Attributes and $400) <> 0 then exit;
        Path := AddBackslash(Folder) + Entry.Name;
        if (Entry.Attributes and $10) <> 0 then begin
          if not CountTeamServerFiles(Path, Count) then exit;
        end else begin
          Count := Count + 1;
        end;
      end;
    until not FindNext(Entry);
  finally
    FindClose(Entry);
  end;
  Result := True;
end;

function TeamServerFilesMatch: Boolean;
var
  Contents, ServerPath, EntryPath, Name, Digest: String;
  Entries: TArrayOfString;
  Index, Count: Integer;
begin
  Result := False;
  if not TeamServerManifestMatches then exit;
  Contents := ExpandConstant('{tmp}\TEAM_SERVER_CONTENTS.sha256');
  if not FileExists(Contents) then ExtractTemporaryFile('TEAM_SERVER_CONTENTS.sha256');
  if CompareText(GetSHA256OfFile(Contents), '{#TeamServerFilesManifestSHA256}') <> 0 then exit;
  if not LoadStringsFromFile(Contents, Entries) or (GetArrayLength(Entries) = 0) then exit;
  ServerPath := ExpandConstant('{#TeamServerFolder}');
  Count := 0;
  if not CountTeamServerFiles(ServerPath, Count) or (Count <> GetArrayLength(Entries)) then exit;
  for Index := 0 to GetArrayLength(Entries) - 1 do begin
    if (Length(Entries[Index]) < 67) or (Copy(Entries[Index], 65, 2) <> '  ') then exit;
    Digest := Copy(Entries[Index], 1, 64);
    Name := Copy(Entries[Index], 67, Length(Entries[Index]));
    StringChangeEx(Name, '/', '\', True);
    EntryPath := AddBackslash(ServerPath) + Name;
    if not FileExists(EntryPath) or (CompareText(GetSHA256OfFile(EntryPath), Digest) <> 0) then begin
      Log('Server program file differs: ' + Name);
      exit;
    end;
  end;
  Result := True;
end;

function TeamServerPreflight: String;
var
  ServerPath, ServerParent: String;
begin
  Result := '';
  ReuseTeamServer := False;
  if not ShouldInstallTeamServer then exit;
  if Length(ExpandConstant('{app}')) > {#TeamServerMaxAppDir} then begin
    Result := '办公室服务器的安装路径过长。请为程序选择更短的目录。';
    exit;
  end;
  ServerPath := ExpandConstant('{#TeamServerFolder}');
  ServerParent := ExtractFileDir(ServerPath);
  if IsReparsePoint(ServerParent) or IsReparsePoint(ServerPath) or
     FileExists(ServerParent) or FileExists(ServerPath) then begin
    Result := '办公室服务器程序目录已被文件、链接或联接占用。请保留现有内容并选择其他程序目录。';
    exit;
  end;
  if DirExists(ServerPath) then begin
    ReuseTeamServer := TeamServerFilesMatch;
    if not ReuseTeamServer then
      Result := '同版本服务器程序校验失败。请保留现有数据和安装日志，先修复程序目录。';
  end;
end;
#endif

function CanLaunchWorkBox: Boolean;
begin
  Result := RuntimeFinalized and not IsOnlineUpdate;
  if IsLocalTrial then Result := False;
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
#ifdef WorkstationPayload
var
  ExplicitDirectory, TrialError, TrialMode: String;
#endif
begin
  RuntimeFinalized := False;
  HelperReportCounter := 0;
  Result := True;
  if IsLocalTrial and IsOnlineUpdate then begin
    Result := False;
    SuppressibleMsgBox('本机验收模式不能用于在线更新。', mbError, MB_OK, IDOK);
    exit;
  end;
#ifdef WorkstationPayload
  TrialMode := WorkstationTrialMode;
  if (TrialMode <> '') and (TrialMode <> '0') and not IsLocalTrial then begin
    Result := False;
    SuppressibleMsgBox('无效的工作站验收模式，未开始安装。', mbError, MB_OK, IDOK);
    exit;
  end;
  if IsLocalTrial then begin
    ExplicitDirectory := ExpandConstant('{param:DIR|}');
    if ExplicitDirectory = '' then ExplicitDirectory := WorkstationTrialDirectory;
    TrialError := WorkstationPathPreflight(ExplicitDirectory);
    if TrialError <> '' then begin
      Result := False;
      SuppressibleMsgBox(TrialError, mbError, MB_OK, IDOK);
      exit;
    end;
  end;
#endif
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
#ifdef WorkstationPayload
  if IsLocalTrial then begin
    { 不沿用注册表或 /LOADINF 记录的其他目录，静默安装也走此处。 }
    WizardForm.DirEdit.Text := WorkstationTrialDirectory;
    WizardForm.DirEdit.Enabled := False;
    WizardForm.DirBrowseButton.Enabled := False;
  end;
#endif
  if IsOnlineUpdate then begin
    WizardForm.DirEdit.Text := OnlineTargetDir;
    WizardForm.DirEdit.Enabled := False;
    WizardForm.DirBrowseButton.Enabled := False;
  end;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := IsOnlineUpdate and (PageID = wpSelectDir);
#ifdef IsolatedTrialSupport
  Result := Result or
    (IsLocalTrial and ((PageID = wpSelectDir) or (PageID = wpSelectProgramGroup)));
#endif
#ifdef TeamServerPayloadDir
  Result := Result or (IsOnlineUpdate and (PageID = wpSelectComponents));
#endif
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  HelperPath: String;
#ifdef TeamServerPayloadDir
  TrialParent: String;
#endif
begin
  Result := '';
#ifdef WorkstationPayload
  Result := WorkstationPathPreflight(ExpandConstant('{app}'));
  if Result <> '' then exit;
#endif
#ifdef TeamServerPayloadDir
  if IsLocalTrial then begin
    TrialParent := ExpandConstant('{localappdata}\WorkBoxUnifiedTrial');
    if (CompareText(ExpandFileName(ExpandConstant('{app}')), WorkBoxDefaultDirectory('')) <> 0) or
       FileExists(TrialParent) or IsReparsePoint(TrialParent) or
       IsReparsePoint(WorkBoxDefaultDirectory('')) then begin
      Result := '本机验收只允许安装到专属程序目录。';
      exit;
    end;
  end;
#endif
  if IsOnlineUpdate and
    (CompareText(ExpandFileName(ExpandConstant('{app}')), ExpandFileName(OnlineTargetDir)) <> 0) then
    Result := '在线升级目标与原安装目录不一致，已停止安装。';
  if Result <> '' then exit;
#ifdef TeamServerPayloadDir
  Result := TeamServerPreflight;
  if Result <> '' then exit;
#endif
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
#ifdef ChromiumManifestSHA256
      if RuntimeFinalized and
         (CompareText(GetSHA256OfFile(ExpandConstant('{app}\Chromium\CHROMIUM_CONTENTS.sha256')),
          '{#ChromiumManifestSHA256}') <> 0) then begin
        Log('The installed Chromium manifest differs.');
        RuntimeFinalized := False;
      end;
#endif
#ifdef TeamServerPayloadDir
      if RuntimeFinalized and ShouldCopyTeamServer and not TeamServerFilesMatch then begin
        Log('The selected Team Server program files differ.');
        RuntimeFinalized := False;
      end;
#endif
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
