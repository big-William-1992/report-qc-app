; Inno Setup 安装脚本 —— 医学影像报告质控软件
; 用法：先用 PyInstaller 生成 dist\报告质控软件\ 目录，再用 Inno Setup Compiler 打开本文件 Build
; 输出： installer\医学影像报告质控软件_Setup.exe
; 可选：若提供图标，把 .ico 放到 assets\app.ico 并取消下行注释：
; SetupIconFile=assets\app.ico

#define MyAppName "医学影像报告质控软件"
#define MyAppVersion "1.0"
#define MyAppPublisher "Radiology QC"
#define MyAppURL "https://example.com"
#define MyAppExeName "报告质控软件.exe"

[Setup]
AppId={{8F2C1A3B-6D4E-4C9A-9B21-7E33C5A8D201}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=installer
OutputBaseFilename={#MyAppName}_Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=lowest

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Default.isl"

[Files]
; 把 PyInstaller 产物整个目录打进安装包（含 exe 与 assets/）
Source: "dist\{#MyAppExeName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent
