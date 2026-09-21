; Meshwright installer - Geekatplay Studio, Vladimir Chopine
;
; Built by packaging\build.ps1, which passes the version and edition:
;     ISCC.exe /DAppVersion=1.3.0 /DEdition=full packaging\meshwright.iss
;
; What it does, and why:
;   * Installs for the current user by default (no administrator prompt) into
;     %LOCALAPPDATA%\Programs\Meshwright. A dialog offers "all users" for anyone who wants it.
;   * Installs the Microsoft Edge WebView2 runtime only if it is genuinely missing. Meshwright
;     draws its window with it; without it pywebview quietly falls back to Internet Explorer's
;     engine, which cannot run the interface at all.
;   * Adds a Start-menu entry, an optional desktop icon and a proper uninstaller.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef Edition
  #define Edition "full"
#endif
#if Edition == "lite"
  #define Suffix "-lite"
#else
  #define Suffix ""
#endif

#define AppName "Meshwright"
#define AppExe "Meshwright.exe"
#define WebView2Setup "redist\MicrosoftEdgeWebview2Setup.exe"

[Setup]
; Never change this GUID: it is how a newer installer recognises and upgrades an older install.
AppId={{5542CFB7-2759-4BA4-AF35-635E23683E74}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Geekatplay Studio
AppPublisherURL=https://www.geekatplay.com
AppSupportURL=https://github.com/GeekatplayStudio/Meshwright/issues
AppUpdatesURL=https://github.com/GeekatplayStudio/Meshwright/releases
AppComments=Mesh analysis, repair and STL preparation for 3D printing. Support development: https://geekatplay.gumroad.com/coffee
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
SetupIconFile=..\ui\assets\icon.ico
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=Meshwright-Setup-{#AppVersion}{#Suffix}
#ifdef Fast
; test builds only (build.ps1 -Fast): trades size for a quick compile
Compression=zip/1
SolidCompression=no
#else
Compression=lzma2/max
SolidCompression=yes
#endif
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
SetupMutex=MeshwrightSetupMutex
VersionInfoVersion={#AppVersion}
VersionInfoCompany=Geekatplay Studio
VersionInfoDescription={#AppName} setup
VersionInfoProductName={#AppName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\Meshwright\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
#ifexist WebView2Setup
Source: "{#WebView2Setup}"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: NeedsWebView2
#endif

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
#ifexist WebView2Setup
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; \
  StatusMsg: "Installing the Microsoft Edge WebView2 runtime (needed to draw Meshwright's window)..."; \
  Check: NeedsWebView2; Flags: waituntilterminated
#endif
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Only what is certainly disposable. The crash-recovery snapshots in sessions\ can be the
; only copy of someone's unsaved work after a crash, so the folders are removed only if
; they are already empty - uninstalling never destroys anything that might matter.
Type: files; Name: "{localappdata}\Meshwright\startup-error.txt"
Type: dirifempty; Name: "{localappdata}\Meshwright\sessions"
Type: dirifempty; Name: "{localappdata}\Meshwright"

[Code]
const
  WebView2Guid = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function WebView2VersionAt(RootKey: Integer; const SubKey: string): string;
begin
  if not RegQueryStringValue(RootKey, SubKey, 'pv', Result) then
    Result := '';
end;

function WebView2Installed: Boolean;
var
  Version: string;
begin
  { The same three places Microsoft documents: 64-bit machine-wide (stored under the 32-bit
    view), 32-bit machine-wide, and the current user. "0.0.0.0" is what an uninstalled
    runtime leaves behind. }
  Version := WebView2VersionAt(HKLM32, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\' + WebView2Guid);
  if (Version = '') or (Version = '0.0.0.0') then
    Version := WebView2VersionAt(HKLM64, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\' + WebView2Guid);
  if (Version = '') or (Version = '0.0.0.0') then
    Version := WebView2VersionAt(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\' + WebView2Guid);
  Result := (Version <> '') and (Version <> '0.0.0.0');
end;

function NeedsWebView2: Boolean;
begin
  Result := not WebView2Installed;
end;
