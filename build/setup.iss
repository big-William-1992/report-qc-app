#codepage 65001

[Setup]
AppName=医学影像报告质控软件
AppVersion=1.0
AppPublisher=报告质控软件
DefaultDirName={autopf}\报告质控软件
DefaultGroupName=报告质控软件
OutputDir=..\installer
OutputBaseFilename=ReportQcSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64
UninstallDisplayIcon={app}\报告质控软件.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\dist\报告质控软件\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\报告质控软件"; Filename: "{app}\报告质控软件.exe"
Name: "{autodesktop}\报告质控软件"; Filename: "{app}\报告质控软件.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式:"; Flags: unchecked

[Run]
Filename: "{app}\报告质控软件.exe"; Description: "启动 报告质控软件"; Flags: nowait postinstall skipifsilent
