; Inno Setup script: turns dist\Moodboard\ into dist\Moodboard-Setup-<version>.exe.
; Normally run by packaging\build.ps1, which passes the version on the command line.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef AppVersionNumeric
  #define AppVersionNumeric "1.0.0.0"
#endif

[Setup]
; AppId ties installs, upgrades and the uninstaller together. Never change it.
AppId={{6E2DE5F2-2A32-4933-ACA5-12D5352A49FB}
AppName=Moodboard
AppVersion={#AppVersion}
AppVerName=Moodboard {#AppVersion}
AppPublisher=Moodboard
VersionInfoVersion={#AppVersionNumeric}
DefaultDirName={autopf}\Moodboard
DefaultGroupName=Moodboard
DisableProgramGroupPage=yes
; Installs for the current user without asking for admin rights; the first
; page still offers "Install for all users" (which does ask).
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=Moodboard-Setup-{#AppVersion}
SetupIconFile=..\assets\moodboard.ico
UninstallDisplayIcon={app}\Moodboard.exe
UninstallDisplayName=Moodboard
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
; Offer to close a running Moodboard when upgrading.
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; Upgrades: clear the previous version's libraries so stale files don't linger.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\Moodboard\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Moodboard"; Filename: "{app}\Moodboard.exe"
Name: "{autodesktop}\Moodboard"; Filename: "{app}\Moodboard.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Moodboard.exe"; Description: "{cm:LaunchProgram,Moodboard}"; Flags: nowait postinstall skipifsilent
