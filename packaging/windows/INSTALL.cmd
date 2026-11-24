@echo off
if defined DP_DEBUG echo on
rem Dealer Revenue Rescue installer. Double-click this file.
rem Makes no network connection. Needs Python 3.10+ with Tcl/Tk from python.org.
setlocal
pushd "%~dp0"
set "PYTHONPATH=%~dp0src"
set "PYCMD="
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 set "PYCMD=py -3"
if defined PYCMD goto run
python -c "import sys" >nul 2>nul
if not errorlevel 1 set "PYCMD=python"
if defined PYCMD goto run
echo Python 3 was not found on this PC.
echo Install Python 3.10 or newer from the python.org Windows installer.
echo Keep "tcl/tk and IDLE" and "py launcher" ticked, then run INSTALL.cmd again.
set "RESULT=1"
goto finish
:run
echo Using: %PYCMD%
%PYCMD% -m dealerpaperwork.install --source "%~dp0." %*
set "RESULT=%ERRORLEVEL%"
:finish
popd
echo.
if not defined DP_NO_PAUSE pause
exit /b %RESULT%
