; Script de Inno Setup para DashEyes (Windows x64)

[Setup]
AppId={{8F4E7A2C-5B19-4D8E-9C3A-7E21B6D0F4A1}
AppName=DashEyes
AppVersion=2.0.0
AppPublisher=DashEyes
DefaultDirName={autopf}\DashEyes
DefaultGroupName=DashEyes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=.
OutputBaseFilename=DashEyes_Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\DashEyes.exe

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Dirs]
Name: "{app}"; Permissions: users-modify
Name: "{app}\.dasheyes"; Permissions: users-modify

[Files]
Source: "dist\DashEyes\DashEyes.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\DashEyes\*"; DestDir: "{app}"; Excludes: "DashEyes-Diagnostic.exe,.dasheyes\*"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\DashEyes"; Filename: "{app}\DashEyes.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\DashEyes"; Filename: "{app}\DashEyes.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\DashEyes.exe"; Description: "{cm:LaunchProgram,DashEyes}"; Flags: nowait postinstall skipifsilent
