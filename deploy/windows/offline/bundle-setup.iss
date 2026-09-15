; The server installer owns persistent state; this EXE extracts then waits.
#ifndef BundleDir
  #error BundleDir is required
#endif
[Setup]
AppId=SimulationWorkbenchOfflineInstaller
AppName=Simulation Workbench
AppVersion={#ReleaseId}
CreateAppDir=no
Uninstallable=no
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.20348
OutputDir={#OutputDir}
OutputBaseFilename=simworkbench-windows-offline-{#ReleaseId}
Compression=lzma2/fast
SolidCompression=yes
DisableWelcomePage=no
DisableDirPage=yes
DisableProgramGroupPage=yes
SetupLogging=yes
[Files]
Source: "{#BundleDir}\*"; DestDir: "{tmp}\payload"; Flags: recursesubdirs createallsubdirs ignoreversion
[Code]
var
  InstallCode: Integer;
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
      '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{tmp}\payload\install.ps1') + '"',
      ExpandConstant('{tmp}\payload'), SW_SHOWNORMAL, ewWaitUntilTerminated, InstallCode) then
      InstallCode := 1;
    if InstallCode <> 0 then
      RaiseException('Installation did not complete. Review the installer console and persistent state logs before retrying.');
  end;
end;
function GetCustomSetupExitCode: Integer;
begin
  Result := InstallCode;
end;
