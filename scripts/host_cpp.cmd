@echo off
setlocal

if "%~3"=="" (
  echo Usage: host_cpp.cmd input.cpp -o output
  exit /b 1
)

set "VCVARS=C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat"
if not exist "%VCVARS%" (
  echo vcvars64.bat not found: %VCVARS%
  exit /b 1
)

call "%VCVARS%" >nul
if errorlevel 1 exit /b %errorlevel%

set "INPUT=%~1"
set "OUTPUT=%~3"
set "OUTDIR=%~dp3"
set "BASENAME=%~n3"
set "TEMPEXE=%OUTDIR%%BASENAME%.exe"
set "TEMPOBJ=%OUTDIR%%BASENAME%.obj"

if not exist "%OUTDIR%" mkdir "%OUTDIR%"
if errorlevel 1 exit /b %errorlevel%

cl /nologo /EHsc /O2 /Fo:"%TEMPOBJ%" /Fe:"%TEMPEXE%" "%INPUT%"
if errorlevel 1 exit /b %errorlevel%

copy /Y "%TEMPEXE%" "%OUTPUT%" >nul
if errorlevel 1 exit /b %errorlevel%

exit /b 0
