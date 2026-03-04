@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem RDX launcher (Windows) - single entrypoint for CLI + MCP.
rem
rem Double-click (no args):
rem   1) Choose CLI or MCP
rem   2) Always opens a new console window for the selected runtime
rem
rem Direct mode (for scripts):
rem   rdx.bat <rdx cli args...>
rem   rdx.bat mcp <mcp launcher args...>

chcp 65001 >nul 2>&1

set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%"
set "EXITCODE=0"
set "RDX_PAUSE_ON_ERROR=0"
if "%~1"=="" set "RDX_PAUSE_ON_ERROR=1"
set "RDX_NON_INTERACTIVE_FLAG="
if defined RDX_NON_INTERACTIVE set "RDX_NON_INTERACTIVE_FLAG=--non-interactive"
if /i "%~1"=="--non-interactive" (
  set "RDX_NON_INTERACTIVE=1"
  set "RDX_NON_INTERACTIVE_FLAG=--non-interactive"
  shift
)

set "RDX_ENSURE_MODE="
set "RDX_ENSURE_SCAN="
for %%A in (%*) do (
  if defined RDX_ENSURE_SCAN (
    if /i "%%A"=="internet" set "RDX_ENSURE_MODE=--mode internet"
    if /i "%%A"=="lan" set "RDX_ENSURE_MODE=--mode lan"
    set "RDX_ENSURE_SCAN="
  ) else if /i "%%A"=="--mode" (
    set "RDX_ENSURE_SCAN=1"
  )
)

set "RDX_USE_UV=0"
set "PYTHON_EXE=python"
set "UV_CMD=uv"

where python >nul 2>&1
if errorlevel 1 (
  where py >nul 2>&1
  if errorlevel 1 (
    echo [RDX] ERROR: Python not found. Install Python 3.10+ first.
    set "EXITCODE=2"
    goto :exit_now
  ) else (
    set "PYTHON_EXE=py -3"
  )
)

where uv >nul 2>&1
if not errorlevel 1 (
  set "RDX_USE_UV=1"
  set "UV_CMD=uv"
) else (
  if "%PYTHON_EXE%"=="py -3" (
    py -3 -m uv --version >nul 2>&1
  ) else (
    python -m uv --version >nul 2>&1
  )
  if not errorlevel 1 (
    set "RDX_USE_UV=1"
    if "%PYTHON_EXE%"=="py -3" (
      set "UV_CMD=py -3 -m uv"
    ) else (
      set "UV_CMD=python -m uv"
    )
  )
)

call :run_launcher_plain --ensure-env !RDX_ENSURE_MODE! !RDX_NON_INTERACTIVE_FLAG!
if errorlevel 1 (
  echo [RDX] ERROR: Environment check failed. Follow the printed suggestions and retry.
  set "EXITCODE=1"
  goto :exit_now
)

if /i "%~1"=="__window_cli" goto :window_cli
if /i "%~1"=="__window_mcp" goto :window_mcp

if /i "%~1"=="mcp" (
  shift
  call :run_mcp %*
  goto :exit_now
)
if /i "%~1"=="server" (
  shift
  call :run_mcp %*
  goto :exit_now
)
if /i "%~1"=="cli" (
  if /i "%~2"=="commands" (
    echo [RDX] Usage: rdx.bat cli commands
    call :run_cli rdx --help
    call :run_cli rdx daemon -h
    call :run_cli rdx capture -h
    call :run_cli rdx vfs -h
    call :run_cli rdx diff -h
    call :run_cli rdx assert -h
    call :run_cli rdx call -h
    set "EXITCODE=0"
    goto :exit_now
  )
  if /i "%~2"=="mcp-tools" (
    call :run_python -c "import json,pathlib;d=json.loads(pathlib.Path('rdx/spec/tool_catalog_196.json').read_text(encoding='utf-8'));print('[RDX] MCP tool count: ' + str(d.get('tool_count',0)));print('\n'.join(sorted(t.get('name','') for t in d.get('tools',[]) if t.get('name'))))"
    set "EXITCODE=!ERRORLEVEL!"
    goto :exit_now
  )
  if /i "%~2"=="mcp-tool" (
    if "%~3"=="" (
      echo [RDX] Usage: rdx.bat cli mcp-tool ^<rd.tool.name^>
      set "EXITCODE=2"
      goto :exit_now
    )
    call :run_python -c "import json,pathlib;name='%~3';d=json.loads(pathlib.Path('rdx/spec/tool_catalog_196.json').read_text(encoding='utf-8'));m={t.get('name'):t for t in d.get('tools',[])}.get(name);print('[RDX] NOT FOUND: '+name) if m is None else print('[RDX] name: '+m.get('name','')+'\n[RDX] group: '+m.get('group','')+'\n[RDX] params: '+', '.join(m.get('param_names',[]))+'\n[RDX] desc: '+m.get('description',''))"
    set "EXITCODE=!ERRORLEVEL!"
    goto :exit_now
  )

  if "%~2"=="-h" (
    echo [RDX] Usage: rdx.bat cli mcp-tools ^| mcp-tool ^| commands
    set "EXITCODE=0"
    goto :exit_now
  )
  if "%~2"=="--help" (
    echo [RDX] Usage: rdx.bat cli mcp-tools ^| mcp-tool ^| commands
    set "EXITCODE=0"
    goto :exit_now
  )

  call :run_cli %2 %3 %4 %5 %6 %7 %8 %9
  set "EXITCODE=!ERRORLEVEL!"
  goto :exit_now
)
if not "%~1"=="" goto :direct_cli
if defined RDX_NON_INTERACTIVE (
  echo [RDX] Non-interactive mode needs explicit target.
  echo [RDX] Example:
  echo [RDX]   rdx.bat --non-interactive cli --help
  echo [RDX]   rdx.bat --non-interactive mcp --mode lan --transport sse
  set "EXITCODE=2"
  goto :exit_now
)

goto :launcher_menu

:launcher_menu
echo.
echo [RDX] Select launch mode:
echo [RDX]   C = CLI window
echo [RDX]   M = MCP server window
echo [RDX]   Q = Quit
choice /c CMQ /n /m "[RDX] Choice: "
if errorlevel 3 goto :exit_now
if errorlevel 2 goto :launcher_mcp
start "RDX CLI" "%~f0" __window_cli
echo [RDX] Opened CLI window.
goto :exit_now

:launcher_mcp
echo.
echo [RDX] MCP network mode:
echo [RDX]   L = LAN
echo [RDX]   I = INTERNET
choice /c LI /n /m "[RDX] Choice: "
set "RDX_MODE=lan"
if errorlevel 2 set "RDX_MODE=internet"

set "RDX_TRANSPORT=auto"
if /i "%RDX_MODE%"=="internet" (
  echo.
  echo [RDX] Internet transport:
  echo [RDX]   H = HTTP ^(streamable-http^)
  echo [RDX]   S = SSE
  choice /c HS /n /m "[RDX] Choice: "
  if errorlevel 2 (
    set "RDX_TRANSPORT=sse"
  ) else (
    set "RDX_TRANSPORT=streamable-http"
  )
)

if not defined RDX_TRANSPORT set "RDX_TRANSPORT="
if /i "%RDX_TRANSPORT%"=="auto" set "RDX_TRANSPORT="
set "RDX_TRANSPORT_ARG="
if defined RDX_TRANSPORT set "RDX_TRANSPORT_ARG=--transport %RDX_TRANSPORT%"

start "RDX MCP" "%~f0" __window_mcp %RDX_MODE% %RDX_TRANSPORT%
echo [RDX] Opened MCP window.
goto :exit_now

:window_cli
title RDX CLI Console
if "%RDX_LOG_LEVEL%"=="" set "RDX_LOG_LEVEL=INFO"
echo.
echo [RDX] CLI console is ready.
echo [RDX] Input rdx arguments only ^(without leading "rdx"^).
echo [RDX] Example:
echo [RDX]   capture open --file "C:\Users\User\Desktop\03.rdc" --connect
if defined RDX_ARGS echo [RDX] Runtime args: %RDX_ARGS%
echo.

:cli_loop
set "USER_CMD="
set /p "USER_CMD=[RDX-CLI] : "
if errorlevel 1 (
  echo [RDX] Input stream closed. Exiting CLI console.
  goto :exit_now
)
if not defined USER_CMD goto :cli_loop

set "USER_CMD_HEAD="
set "USER_CMD_ARG1="
for /f "tokens=1,2" %%A in ("%USER_CMD%") do (
  set "USER_CMD_HEAD=%%~A"
  set "USER_CMD_ARG1=%%~B"
)
if /i "%USER_CMD_HEAD%"=="exit" goto :exit_now
if /i "%USER_CMD_HEAD%"=="quit" goto :exit_now
if /i "%USER_CMD_HEAD%"=="help" (
  echo [RDX] RDX-CLI builtins:
  echo [RDX]   help ^| -h ^| --help
  echo [RDX]   commands [-h]
  echo [RDX]   mcp-tools [help]
  echo [RDX]   mcp-tool ^<rd.tool.name^> [help]
  echo [RDX]   exit ^| quit
  echo.
  goto :cli_loop
)
if /i "%USER_CMD_HEAD%"=="-h" (
  echo [RDX] RDX-CLI builtins:
  echo [RDX]   help ^| -h ^| --help
  echo [RDX]   commands [-h]
  echo [RDX]   mcp-tools [help]
  echo [RDX]   mcp-tool ^<rd.tool.name^> [help]
  echo.
  goto :cli_loop
)
if /i "%USER_CMD_HEAD%"=="--help" (
  echo [RDX] RDX-CLI builtins:
  echo [RDX]   help ^| -h ^| --help
  echo [RDX]   commands [-h]
  echo [RDX]   mcp-tools [help]
  echo [RDX]   mcp-tool ^<rd.tool.name^> [help]
  echo.
  goto :cli_loop
)
if /i "%USER_CMD_HEAD%"=="commands" (
  if /i "%USER_CMD_ARG1%"=="-h" (
    echo [RDX] Usage: commands
    echo [RDX] Print CLI command tree for rdx.
    goto :cli_loop
  )
  if /i "%USER_CMD_ARG1%"=="--help" (
    echo [RDX] Usage: commands
    echo [RDX] Print CLI command tree for rdx.
    goto :cli_loop
  )
  echo [RDX] CLI command tree:
  call :run_cli rdx --help
  call :run_cli rdx daemon -h
  call :run_cli rdx capture -h
  call :run_cli rdx vfs -h
  call :run_cli rdx diff -h
  call :run_cli rdx assert -h
  call :run_cli rdx call -h
  echo.
  goto :cli_loop
)
if /i "%USER_CMD_HEAD%"=="mcp-tool" (
  if /i "%USER_CMD_ARG1%"=="-h" (
    echo [RDX] Usage: mcp-tool ^<rd.tool.name^>
    echo.
    goto :cli_loop
  )
  if /i "%USER_CMD_ARG1%"=="--help" (
    echo [RDX] Usage: mcp-tool ^<rd.tool.name^>
    echo.
    goto :cli_loop
  )
  if not defined USER_CMD_ARG1 (
    echo [RDX] Usage: mcp-tool ^<rd.tool.name^>
    echo.
    goto :cli_loop
  )
  call :run_python -c "import json,pathlib;name='%USER_CMD_ARG1%';d=json.loads(pathlib.Path('rdx/spec/tool_catalog_196.json').read_text(encoding='utf-8'));m={t.get('name'):t for t in d.get('tools',[])}.get(name);print('[RDX] NOT FOUND: '+name) if m is None else print('[RDX] name: '+m.get('name','')+'\n[RDX] group: '+m.get('group','')+'\n[RDX] params: '+', '.join(m.get('param_names',[]))+'\n[RDX] desc: '+m.get('description',''))"
  echo [RDX] Exit code: !ERRORLEVEL!
  echo.
  goto :cli_loop
)
if /i "%USER_CMD_HEAD%"=="mcp-tools" (
  if /i "%USER_CMD_ARG1%"=="-h" (
    echo [RDX] Usage: mcp-tools
    echo.
    goto :cli_loop
  )
  if /i "%USER_CMD_ARG1%"=="--help" (
    echo [RDX] Usage: mcp-tools
    echo.
    goto :cli_loop
  )
  call :run_python -c "import json,pathlib;d=json.loads(pathlib.Path('rdx/spec/tool_catalog_196.json').read_text(encoding='utf-8'));print('[RDX] MCP tool count: ' + str(d.get('tool_count',0)));print('\n'.join(sorted(t.get('name','') for t in d.get('tools',[]) if t.get('name'))))"
  set "EXITCODE=!ERRORLEVEL!"
  echo [RDX] Exit code: !EXITCODE!
  echo.
  goto :cli_loop
)

call :run_cli !USER_CMD!
set "EXITCODE=!ERRORLEVEL!"
echo [RDX] Exit code: !EXITCODE!
echo.
goto :cli_loop

:window_mcp
title RDX MCP Server
if "%RDX_LOG_LEVEL%"=="" set "RDX_LOG_LEVEL=INFO"

set "RDX_MODE=%~2"
if "%RDX_MODE%"=="" set "RDX_MODE=lan"
set "RDX_TRANSPORT=%~3"
if /i "%RDX_TRANSPORT%"=="auto" set "RDX_TRANSPORT="
if /i "%RDX_TRANSPORT%"=="http" set "RDX_TRANSPORT=streamable-http"

if /i "%RDX_TRANSPORT%"=="sse" set "RDX_TRANSPORT=sse"
if /i "%RDX_TRANSPORT%"=="streamable-http" set "RDX_TRANSPORT=streamable-http"

if /i "%RDX_TRANSPORT%"=="stdio" set "RDX_TRANSPORT=stdio"

echo.
echo [RDX] MCP server starting...
echo [RDX]   mode: %RDX_MODE%
if defined RDX_TRANSPORT echo [RDX]   transport override: %RDX_TRANSPORT%
if /i "%RDX_MODE%"=="internet" (
  call :run_launcher_plain --ensure-env --mode internet !RDX_NON_INTERACTIVE_FLAG!
  if errorlevel 1 (
    set "EXITCODE=1"
    goto :exit_now
  )
)

if /i "%RDX_TRANSPORT%"=="" (
  call :run_mcp --mode %RDX_MODE%
) else (
  call :run_mcp --mode %RDX_MODE% --transport %RDX_TRANSPORT%
)
set "EXITCODE=!ERRORLEVEL!"
echo.
echo [RDX] MCP server exited with code !EXITCODE!
if not "!EXITCODE!"=="0" pause
goto :exit_now

:direct_cli
call :run_cli %*
set "EXITCODE=!ERRORLEVEL!"
goto :exit_now

:run_mcp
call :run_launcher !RDX_NON_INTERACTIVE_FLAG! %*
set "EXITCODE=!ERRORLEVEL!"
exit /b !EXITCODE!

:run_cli
set "CLI_FIRST=%~1"
if /i "%CLI_FIRST%"=="rdx" shift
if /i "%CLI_FIRST%"=="cli" shift
if "%RDX_USE_UV%"=="1" (
  call %UV_CMD% run --project . rdx %*
  if not errorlevel 1 (
    set "EXITCODE=!ERRORLEVEL!"
    exit /b !EXITCODE!
  )
  echo [RDX] WARN: uv CLI launch failed, fallback to Python runtime.
  set "RDX_USE_UV=0"
)
if "%PYTHON_EXE%"=="py -3" (
  py -3 -m rdx.cli %*
) else (
  python -m rdx.cli %*
)
set "EXITCODE=!ERRORLEVEL!"
exit /b !EXITCODE!

:run_launcher_plain
if "%PYTHON_EXE%"=="py -3" (
  py -3 rdx_launcher.py %*
) else (
  python rdx_launcher.py %*
)
set "EXITCODE=!ERRORLEVEL!"
exit /b !EXITCODE!

:run_launcher
if "%RDX_USE_UV%"=="1" (
  call %UV_CMD% run --project . python rdx_launcher.py %*
  if not errorlevel 1 (
    set "EXITCODE=!ERRORLEVEL!"
    exit /b !EXITCODE!
  )
  echo [RDX] WARN: uv launcher failed, fallback to Python runtime.
  set "RDX_USE_UV=0"
)
if "%PYTHON_EXE%"=="py -3" (
  py -3 rdx_launcher.py %*
) else (
  python rdx_launcher.py %*
)
set "EXITCODE=!ERRORLEVEL!"
exit /b !EXITCODE!

:run_python
if "%RDX_USE_UV%"=="1" (
  call %UV_CMD% run --project . python %*
  if not errorlevel 1 (
    set "EXITCODE=!ERRORLEVEL!"
    exit /b !EXITCODE!
  )
  echo [RDX] WARN: uv python launch failed, fallback to Python runtime.
  set "RDX_USE_UV=0"
)
if "%PYTHON_EXE%"=="py -3" (
  py -3 %*
) else (
  python %*
)
set "EXITCODE=!ERRORLEVEL!"
exit /b !EXITCODE!

:exit_now
if not "%EXITCODE%"=="0" if "%RDX_PAUSE_ON_ERROR%"=="1" if not defined RDX_NON_INTERACTIVE pause
popd >nul
exit /b %EXITCODE%

