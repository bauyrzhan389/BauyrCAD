; Inno Setup script - builds BauyrCAD-<version>-setup.exe from dist\BauyrCAD
;   iscc packaging\installer.iss
#define AppName "BauyrCAD"
#ifndef AppVersion
  #define AppVersion "0.4.0"
#endif

[Setup]
AppId={{6C1C3E8A-7E5B-4E0B-9A7F-2B5C1D0BA0CD}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=BauyrCAD
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\BauyrCAD.exe
OutputDir=..\dist
OutputBaseFilename=BauyrCAD-{#AppVersion}-setup
SetupIconFile=bauyrcad.ico
Compression=lzma2/max
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
ChangesAssociations=yes
LicenseFile=..\LICENSE
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: unchecked
Name: "assoc"; Description: "Open .bcad files with {#AppName}"

[Files]
Source: "..\dist\BauyrCAD\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\examples\*.bcad"; DestDir: "{app}\examples"; Flags: ignoreversion
Source: "..\examples\*.step"; DestDir: "{app}\examples"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\BauyrCAD.exe"
Name: "{group}\Examples"; Filename: "{app}\examples"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\BauyrCAD.exe"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "Software\Classes\.bcad"; ValueType: string; ValueName: ""; ValueData: "BauyrCAD.Part"; Flags: uninsdeletevalue; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\BauyrCAD.Part"; ValueType: string; ValueName: ""; ValueData: "BauyrCAD Part"; Flags: uninsdeletekey; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\BauyrCAD.Part\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\BauyrCAD.exe,0"; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\BauyrCAD.Part\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\BauyrCAD.exe"" ""%1"""; Tasks: assoc

[Run]
Filename: "{app}\BauyrCAD.exe"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
