@echo off
rem Called inside the entry point's SETLOCAL; never search PATH or change it.
set "YUE2_POWERSHELL=%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe"
if exist "%YUE2_POWERSHELL%" exit /b 0
set "YUE2_POWERSHELL=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if exist "%YUE2_POWERSHELL%" exit /b 0
set "YUE2_POWERSHELL="
echo [FAILED] Windows PowerShell system component was not found.
echo Expected: %SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe
echo Repair the Windows component; changing PATH or downloading music models is not required.
exit /b 6
