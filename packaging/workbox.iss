; 干净安装包只读取构建脚本已验证的 payload，不读取正式目录的 data。
#ifndef PayloadDir
  #error 请通过 scripts/build_installer.py 生成并验证安装载荷。
#endif
#ifndef OutputDir
  #error 必须指定独立的输出目录。
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
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\WorkBox 工作箱"; Filename: "{app}\WorkBox.exe"; WorkingDir: "{app}"
Name: "{group}\安装与使用说明"; Filename: "{app}\安装说明.txt"
Name: "{group}\卸载 WorkBox 工作箱"; Filename: "{uninstallexe}"
Name: "{autodesktop}\WorkBox 工作箱"; Filename: "{app}\WorkBox.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\WorkBox.exe"; Parameters: "--panel"; Description: "启动 WorkBox 工作箱"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

; 不登记 data，也不使用 InstallDelete / UninstallDelete / 卸载删除脚本。
; 程序运行后创建的 data 在卸载或重装时保留。
