@echo off
rem ===========================================================================
rem  Hive installer for Windows: installs or re-installs the `hive` command
rem  from this project directory, using uv.
rem
rem    install.bat            install / re-install
rem    install.bat /test      run the test suite first; install only if it passes
rem    install.bat /nopath    don't touch your PATH (uv tool update-shell)
rem    install.bat /yes       answer "yes" to every question (unattended)
rem    install.bat -h         help (also --help)
rem ===========================================================================
setlocal EnableExtensions

rem Colours: ANSI sequences, which Windows 10+ consoles show. NO_COLOR=1 turns them off.
rem (The FOR line captures the ESC character from a PROMPT expansion.)
set "ESC="
if not defined NO_COLOR for /f "delims=#" %%E in ('"prompt #$E# & for %%E in (1) do rem"') do set "ESC=%%E"
set "G=" & set "Y=" & set "RD=" & set "CY=" & set "B=" & set "DIM=" & set "R="
if defined ESC (
    rem Hive's palette (see src/hive/ui.py) in 24-bit colour: emerald, honey, coral, sky, slate.
    set "G=%ESC%[38;2;40;220;110m" & set "Y=%ESC%[38;2;255;200;30m"
    set "RD=%ESC%[38;2;255;85;105m" & set "CY=%ESC%[38;2;40;200;255m"
    set "B=%ESC%[1m" & set "DIM=%ESC%[38;2;130;138;165m" & set "R=%ESC%[0m"
)

set "RUN_TESTS=0"
set "UPDATE_PATH=1"
set "ASSUME_YES=0"
rem The help switch is checked before the loop: a question mark is a wildcard in FOR lists.
if "%~1"=="/?" goto :usage
for %%A in (%*) do (
    if /I "%%~A"=="/test"   set "RUN_TESTS=1"
    if /I "%%~A"=="--test"  set "RUN_TESTS=1"
    if /I "%%~A"=="/nopath" set "UPDATE_PATH=0"
    if /I "%%~A"=="--no-path" set "UPDATE_PATH=0"
    if /I "%%~A"=="/yes"    set "ASSUME_YES=1"
    if /I "%%~A"=="-y"      set "ASSUME_YES=1"
    if /I "%%~A"=="-h"      goto :usage
    if /I "%%~A"=="--help"  goto :usage
)

rem Work from the folder this script is in, wherever it was started from.
cd /d "%~dp0" || goto :fail_nodir
echo.
echo %B%%CY%=== Hive installer ===%R%
echo %DIM%Project: %CD%%R%
echo.

rem --- 1. Is this the Hive project? ------------------------------------------
if not exist "pyproject.toml" goto :fail_project
findstr /R /C:"^name *= *\"hive\"" pyproject.toml >nul 2>&1 || goto :fail_project
if not exist "src\hive\__init__.py" goto :fail_project
echo %G%[ok]%R% Hive project found.

rem --- 2. uv -------------------------------------------------------------------
where uv >nul 2>&1
if errorlevel 1 call :install_uv || goto :fail
for /f "delims=" %%V in ('uv --version 2^>nul') do echo %G%[ok]%R% %%V

rem --- 3. git (agents need it; the install itself doesn't) -----------------------
where git >nul 2>&1
if errorlevel 1 (
    echo %Y%[warn]%R% git was not found on PATH. Hive agents need git; install Git for Windows:
    echo        https://git-scm.com/download/win
) else (
    for /f "delims=" %%V in ('git --version') do echo %G%[ok]%R% %%V
)

rem --- 4. Running Hive processes lock hive.exe ----------------------------------
rem (Windows tools are called by full path: from Git Bash, GNU find comes first.)
tasklist /FI "IMAGENAME eq hive.exe" /NH 2>nul | "%SystemRoot%\System32\find.exe" /I "hive.exe" >nul
if not errorlevel 1 call :stop_running || goto :fail

rem --- 5. Optional: tests -----------------------------------------------------------
if "%RUN_TESTS%"=="1" (
    echo.
    echo %CY%==^>%R% Running the test suite ^(this takes about a minute^)...
    uv run --quiet pytest -q -p no:logging
    if errorlevel 1 goto :fail_tests
    echo %G%[ok]%R% Tests passed.
)

rem --- 6. Install / re-install ------------------------------------------------------
echo.
echo %CY%==^>%R% Installing Hive ^(clean rebuild^)...
rem --reinstall rebuilds even when the version number is unchanged, so no stale build
rem survives; --force replaces an existing `hive` executable.
uv tool install --force --reinstall .
if errorlevel 1 goto :fail_install

set "BIN="
for /f "delims=" %%D in ('uv tool dir --bin') do set "BIN=%%D"
if not defined BIN goto :fail_install
if not exist "%BIN%\hive.exe" goto :fail_install
set "HIVE_VERSION="
for /f "delims=" %%V in ('"%BIN%\hive.exe" --version') do set "HIVE_VERSION=%%V"
if not defined HIVE_VERSION goto :fail_install
echo %G%[ok]%R% Installed hive %HIVE_VERSION% at %BIN%\hive.exe

rem --- 7. PATH -------------------------------------------------------------------
rem Agent CLIs start `hive mcp` / `hive hook` by name, so `hive` must be on PATH.
set "PATH_NOTE="
if "%UPDATE_PATH%"=="1" (
    uv tool update-shell >nul 2>&1
)
set "FIRST_HIVE="
for /f "delims=" %%P in ('where hive 2^>nul') do if not defined FIRST_HIVE set "FIRST_HIVE=%%P"
if not defined FIRST_HIVE (
    if "%UPDATE_PATH%"=="1" (
        set "PATH_NOTE=Open a NEW terminal: %BIN% was added to your PATH."
    ) else (
        set "PATH_NOTE=Add %BIN% to your PATH yourself, or re-run without /nopath."
    )
) else (
    if /I not "%FIRST_HIVE%"=="%BIN%\hive.exe" (
        echo %Y%[warn]%R% Another `hive` comes first on PATH: %FIRST_HIVE%
        echo        Remove it ^(e.g. an old `pip install --user hive`: pip uninstall hive^)
        echo        so that %BIN%\hive.exe is used.
    ) else (
        echo %G%[ok]%R% `hive` on PATH points to the new install.
    )
)

rem --- Done ----------------------------------------------------------------------
echo.
echo %B%%G%=== Hive %HIVE_VERSION% installed ===%R%
if defined PATH_NOTE echo %Y%NOTE:%R% %PATH_NOTE%
echo.
echo %B%Next steps after an upgrade:%R%
echo   1. Restart `hive up` on this machine.
echo   2. In each project using Hive: `hive init`, then commit and push the refreshed files.
echo   3. Upgrade the Hive server too ^(docs\DEPLOYMENT.md, "Upgrading"^): server and agent
echo      machines must run the same version.
echo.
endlocal
exit /b 0

rem ===========================================================================
:install_uv
echo %Y%[!!]%R% uv was not found. Hive is installed with uv ^(https://docs.astral.sh/uv/^).
call :confirm "Install uv now with its official installer"
if errorlevel 1 (
    echo Install uv yourself, then run this script again:
    echo   %CY%powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"%R%
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
if errorlevel 1 exit /b 1
rem The installer puts uv in %USERPROFILE%\.local\bin; use it in this window right away.
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv >nul 2>&1
if errorlevel 1 (
    echo uv was installed but can't be found; open a new terminal and run this script again.
    exit /b 1
)
exit /b 0

:stop_running
echo %Y%[!!]%R% Hive is running ^(hive.exe^). Windows can't replace hive.exe while it runs.
echo      Stop `hive up` / `hive chat` yourself ^(Ctrl+C in their windows^), or let this
echo      script stop them. Any agent turn in progress is interrupted.
call :confirm "Stop all running hive.exe processes now"
if errorlevel 1 (
    echo Stop Hive, then run this script again.
    exit /b 1
)
taskkill /IM hive.exe /F >nul 2>&1
rem Give Windows a moment to release the file (ping works without a console,
rem unlike timeout).
"%SystemRoot%\System32\ping.exe" -n 3 127.0.0.1 >nul
tasklist /FI "IMAGENAME eq hive.exe" /NH 2>nul | "%SystemRoot%\System32\find.exe" /I "hive.exe" >nul
if not errorlevel 1 (
    echo Could not stop every hive.exe; close them manually and run this script again.
    exit /b 1
)
echo %G%[ok]%R% Stopped running Hive processes.
exit /b 0

:confirm
rem Usage: call :confirm "Question"  -> errorlevel 0 for yes, 1 for no
if "%ASSUME_YES%"=="1" exit /b 0
choice /C YN /M "%~1"
if errorlevel 2 exit /b 1
exit /b 0

:usage
echo %B%Usage:%R% install.bat [/test] [/nopath] [/yes]
echo   /test     run the test suite first; install only if it passes
echo   /nopath   don't add uv's tool folder to your PATH
echo   /yes      answer "yes" to every question
exit /b 0

:fail_nodir
echo %RD%[error]%R% Could not change to the script's folder.
exit /b 1
:fail_project
echo %RD%[error]%R% This doesn't look like the Hive project ^(pyproject.toml with name = "hive",
echo         src\hive\^). Put install.bat in the project root and run it from there.
exit /b 1
:fail_tests
echo %RD%[error]%R% Tests failed; nothing was installed. Fix the failures, or run without /test.
exit /b 1
:fail_install
echo %RD%[error]%R% Installation failed. The messages above say why; common causes:
echo         - hive.exe still in use ^(close `hive up` / `hive chat` windows^)
echo         - no network access to download dependencies
exit /b 1
:fail
echo %RD%[error]%R% Installation stopped.
exit /b 1
