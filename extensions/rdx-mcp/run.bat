@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem RDX-MCP one-click runner (Windows)
rem - Double-click to run stdio transport (default)
rem - Or run from terminal: run.bat --transport sse --host 127.0.0.1 --port 8765
rem
rem Required (for RenderDoc features):
rem   RDX_RENDERDOC_PATH = directory that contains the RenderDoc Python module (so `import renderdoc` works)
rem
rem Optional:
rem   RDX_LOG_LEVEL      = INFO / DEBUG / WARNING ...
rem   RDX_ARTIFACT_DIR   = artifact store root (default: /tmp/rdx-artifacts in server.py)
rem   RDX_DB_DIR         = db root (default: /tmp/rdx-db in server.py)
rem   RDX_KB_INDEX_DIRS  = directories to index (see docs; Windows multi-dir has ':' caveat)

chcp 65001 >nul 2>&1

set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%" >nul
for %%I in ("%SCRIPT_DIR%..\\..") do set "REPO_ROOT=%%~fI"

rem Optional local overrides (do not commit secrets/paths into this file).
rem Create a sibling file `run.env.bat` to set env vars, e.g.:
rem   set "RDX_RENDERDOC_PATH=D:\Path\To\RenderDoc\python"
rem   set "RDX_ARTIFACT_DIR=D:\rdx\artifacts"
if exist "%SCRIPT_DIR%run.env.bat" (
  call "%SCRIPT_DIR%run.env.bat"
)

set "RDX_RENDERDOC_AUTO="
if not defined RDX_RENDERDOC_PATH (
  if exist "%REPO_ROOT%\x64\Development\pymodules\renderdoc.pyd" (
    set "RDX_RENDERDOC_PATH=%REPO_ROOT%\x64\Development\pymodules"
    set "PATH=%REPO_ROOT%\x64\Development;!PATH!"
    set "RDX_RENDERDOC_AUTO=1"
  )
)
if not defined RDX_RENDERDOC_PATH (
  if exist "%REPO_ROOT%\x64\Release\pymodules\renderdoc.pyd" (
    set "RDX_RENDERDOC_PATH=%REPO_ROOT%\x64\Release\pymodules"
    set "PATH=%REPO_ROOT%\x64\Release;!PATH!"
    set "RDX_RENDERDOC_AUTO=1"
  )
)
if not defined RDX_RENDERDOC_PATH (
  if exist "%REPO_ROOT%\Win32\Development\pymodules\renderdoc.pyd" (
    set "RDX_RENDERDOC_PATH=%REPO_ROOT%\Win32\Development\pymodules"
    set "PATH=%REPO_ROOT%\Win32\Development;!PATH!"
    set "RDX_RENDERDOC_AUTO=1"
  )
)
if not defined RDX_RENDERDOC_PATH (
  if exist "%REPO_ROOT%\Win32\Release\pymodules\renderdoc.pyd" (
    set "RDX_RENDERDOC_PATH=%REPO_ROOT%\Win32\Release\pymodules"
    set "PATH=%REPO_ROOT%\Win32\Release;!PATH!"
    set "RDX_RENDERDOC_AUTO=1"
  )
)

if defined RDX_RENDERDOC_AUTO (
  echo [RDX-MCP] Using RenderDoc module: %RDX_RENDERDOC_PATH%
)

if "%RDX_RENDERDOC_PATH%"=="" (
  echo [RDX-MCP] ERROR: RenderDoc python module not found.
  echo [RDX-MCP] RDX-MCP requires a local RenderDoc source build to provide MCP tools.
  echo [RDX-MCP] Build renderdoc.sln ^> pyrenderdoc_module ^(x64 Development^) to generate:
  echo [RDX-MCP]   %REPO_ROOT%\x64\Development\pymodules\renderdoc.pyd
  echo [RDX-MCP] Then set RDX_RENDERDOC_PATH or keep the default build layout.
  set "EXITCODE=1"
  goto :end
)

if not exist "%RDX_RENDERDOC_PATH%\renderdoc.pyd" (
  echo [RDX-MCP] ERROR: renderdoc.pyd not found under "%RDX_RENDERDOC_PATH%".
  echo [RDX-MCP] Check your build output or RDX_RENDERDOC_PATH value.
  set "EXITCODE=1"
  goto :end
)

for %%I in ("%RDX_RENDERDOC_PATH%\\..") do set "RDX_RENDERDOC_DLL_DIR=%%~fI"
if exist "%RDX_RENDERDOC_DLL_DIR%\renderdoc.dll" (
  set "PATH=%RDX_RENDERDOC_DLL_DIR%;!PATH!"
) else (
  echo [RDX-MCP] WARNING: renderdoc.dll not found under "%RDX_RENDERDOC_DLL_DIR%".
  echo [RDX-MCP]          If `import renderdoc` fails, add the folder to PATH.
  echo.
)

if "%RDX_LOG_LEVEL%"=="" set "RDX_LOG_LEVEL=INFO"

rem Prefer `py -3`, fallback to `python`.
set "PY_CMD="
where py >nul 2>&1 && set "PY_CMD=py -3"
if "%PY_CMD%"=="" (
  where python >nul 2>&1 && set "PY_CMD=python"
)

if "%PY_CMD%"=="" (
  echo [RDX-MCP] ERROR: Python not found. Install Python 3.10+ or ensure `py`/`python` is on PATH.
  goto :end
)

set "PIP_USER_FLAG=--user"
if not "%VIRTUAL_ENV%"=="" set "PIP_USER_FLAG="
if not "%CONDA_PREFIX%"=="" set "PIP_USER_FLAG="

rem Ensure Python deps are available (install once if missing).
set "NEED_INSTALL="
call %PY_CMD% -c "import importlib.util,sys;mods=['mcp','pydantic','numpy','PIL','pyarrow','jinja2','aiofiles'];sys.exit(0 if all(importlib.util.find_spec(m) for m in mods) else 1)" >nul 2>&1
if errorlevel 1 set "NEED_INSTALL=1"

if defined NEED_INSTALL (
  echo [RDX-MCP] Python dependencies missing. Installing...
  call %PY_CMD% -m pip --version >nul 2>&1
  if errorlevel 1 (
    echo [RDX-MCP] ERROR: pip not available. Try: %PY_CMD% -m ensurepip --upgrade
    goto :end
  )

  rem Ensure build tooling exists (setuptools/wheel are required by pyproject).
  call %PY_CMD% -m pip install --upgrade pip setuptools wheel
  if errorlevel 1 (
    echo [RDX-MCP] ERROR: Failed to upgrade pip/setuptools/wheel.
    goto :end
  )

  call %PY_CMD% -m pip install "mcp>=1.2.0" "pydantic>=2.0" "numpy>=1.24" "Pillow>=10.0" "pyarrow>=14.0" "jinja2>=3.1" "aiofiles>=23.0" %PIP_USER_FLAG%
  if errorlevel 1 (
    echo [RDX-MCP] ERROR: Failed to install dependencies. Check your network and Python setup.
    goto :end
  )
)

echo [RDX-MCP] Using: %PY_CMD%
echo [RDX-MCP] Working dir: %SCRIPT_DIR%
echo.

rem One-click default: start SSE server for remote clients (e.g. Manus).
rem Override by passing args, e.g.:
rem   run.bat --transport stdio
rem   run.bat --transport sse --host 127.0.0.1 --port 8765
set "RDX_ARGS=%*"
if "%~1"=="" goto :auto_sse_config
:after_auto_sse

%PY_CMD% "%SCRIPT_DIR%run.py" %RDX_ARGS%
set "EXITCODE=%ERRORLEVEL%"

echo.
echo [RDX-MCP] Exited with code %EXITCODE%

:end
popd >nul

rem One-click friendly: pause unless explicitly disabled.
if "%RDX_NO_PAUSE%"=="" pause
exit /b %EXITCODE%

:auto_sse_config
set "RDX_PORT=8765"
set "LAN_IP="
set "RDX_ENV_FILE=%TEMP%\\rdx_mcp_env.bat"
%PY_CMD% "%SCRIPT_DIR%run_autoconfig.py" "%RDX_ENV_FILE%" >nul 2>&1
if exist "%RDX_ENV_FILE%" (
  call "%RDX_ENV_FILE%"
  del "%RDX_ENV_FILE%" >nul 2>&1
)
if "%RDX_PORT%"=="" set "RDX_PORT=8765"
set "RDX_SSE_HOST=0.0.0.0"
set "RDX_SSE_PORT=%RDX_PORT%"
set "RDX_ARGS=--transport sse --host 0.0.0.0 --port %RDX_PORT%"

if not "%LAN_IP%"=="" goto :have_ip
echo [RDX-MCP] Manus config:
echo [RDX-MCP]   Transport: SSE
echo [RDX-MCP]   URL: http://127.0.0.1:%RDX_PORT%/sse  ^(same machine only^)
goto :after_ip_print

:have_ip
echo [RDX-MCP] Manus config:
echo [RDX-MCP]   Transport: SSE
echo [RDX-MCP]   URL: http://%LAN_IP%:%RDX_PORT%/sse
powershell -NoProfile -Command "Set-Clipboard -Value 'http://%LAN_IP%:%RDX_PORT%/sse'" >nul 2>&1
rem Best-effort: open inbound firewall for local network access (may require admin).
netsh advfirewall firewall add rule name="RDX-MCP SSE %RDX_PORT%" dir=in action=allow protocol=TCP localport=%RDX_PORT% >nul 2>&1
if "%ERRORLEVEL%"=="0" goto :after_firewall_note
echo [RDX-MCP] NOTE: Could not add firewall rule (may require admin). If Manus can't connect, allow port %RDX_PORT% in Windows Firewall.
:after_firewall_note
:after_ip_print
echo.
goto :after_auto_sse
