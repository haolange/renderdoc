@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%"
set "RDX_TOOLS_ROOT=%SCRIPT_DIR:~0,-1%"
if not defined RDX_USE_UV set "RDX_USE_UV=0"
set "RDX_NON_INTERACTIVE_FLAG="
if /i "%~1"=="--non-interactive" (
  set "RDX_NON_INTERACTIVE=1"
  set "RDX_NON_INTERACTIVE_FLAG=1"
  shift
)

set "PYTHON_EXE=python"
where python >nul 2>&1
if errorlevel 1 (
  where py >nul 2>&1
  if errorlevel 1 (
    echo [RDX] ERROR: Python 3.10+ not found.
    popd >nul
    exit /b 2
  ) else (
    set "PYTHON_EXE=py -3"
  )
)

if "%~1"=="" goto :help

if /i "%~1"=="mcp" goto :dispatch_mcp
if /i "%~1"=="cli" goto :dispatch_cli

if /i "%~1"=="--help" goto :help
if /i "%~1"=="-h" goto :help

echo [RDX] ERROR: unknown command "%~1"
goto :help_err

:dispatch_mcp
shift
call :run_mcp %1 %2 %3 %4 %5 %6 %7 %8 %9
set "EC=%ERRORLEVEL%"
popd >nul
exit /b %EC%

:dispatch_cli
shift
call :run_cli %1 %2 %3 %4 %5 %6 %7 %8 %9
set "EC=%ERRORLEVEL%"
popd >nul
exit /b %EC%

:run_mcp
call :run_python mcp\\run_mcp.py %*
exit /b %ERRORLEVEL%

:run_cli
call :run_python cli\\run_cli.py %*
exit /b %ERRORLEVEL%

:run_python
if /i "%RDX_USE_UV%"=="1" if not defined RDX_UV_FALLBACK_DONE (
  where uv >nul 2>&1
  if not errorlevel 1 (
    uv run --project . python %*
    if not errorlevel 1 exit /b 0
    set "RDX_UV_FALLBACK_DONE=1"
  )
)
if "%PYTHON_EXE%"=="py -3" (
  py -3 %*
) else (
  python %*
)
exit /b %ERRORLEVEL%

:help
echo [RDX] Usage:
echo [RDX]   rdx.bat [--non-interactive] mcp [--ensure-env] [--transport stdio^|sse^|streamable-http]
echo [RDX]   rdx.bat [--non-interactive] cli ^<args...^>
popd >nul
exit /b 0

:help_err
echo [RDX] Try: rdx.bat --help
popd >nul
exit /b 2
