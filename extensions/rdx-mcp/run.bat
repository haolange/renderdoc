@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem RDX-MCP one-click runner (Windows)
rem - Double-click to run with a prompt (LAN or Internet via ngrok, HTTP/SSE selection for Internet)
rem - Or run from terminal: run.bat --transport stdio
rem
rem Required (for RenderDoc features):
rem   RDX_RENDERDOC_PATH = directory that contains the RenderDoc Python module (so `import renderdoc` works)
rem
rem Optional:
rem   RDX_LOG_LEVEL      = INFO / DEBUG / WARNING ...
rem   RDX_ARTIFACT_DIR   = artifact store root (default: /tmp/rdx-artifacts in server.py)

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

rem Optional: prompt for default rdc capture directories (stored in local .rdx_mcp.json).
set "RDX_SETUP_ENV=%TEMP%\\rdx_mcp_setup_env.bat"
%PY_CMD% "%SCRIPT_DIR%run_autoconfig.py" --prepare-rdc --env "%RDX_SETUP_ENV%"
if errorlevel 1 (
  echo [RDX-MCP] WARNING: Failed to prepare RDC directories; continuing.
) else (
  if exist "%RDX_SETUP_ENV%" (
    call "%RDX_SETUP_ENV%"
    del "%RDX_SETUP_ENV%" >nul 2>&1
  )
)

if "%~1"=="" goto :select_mode
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

:select_mode
choice /c LI /n /m "[RDX-MCP] Mode: L=LAN, I=INTERNET : "
set "RDX_MODE=lan"
if errorlevel 2 set "RDX_MODE=internet"
echo [RDX-MCP] Mode selected: %RDX_MODE%
set "RDX_ENV_FILE=%TEMP%\\rdx_mcp_env.bat"
%PY_CMD% "%SCRIPT_DIR%run_autoconfig.py" --mode %RDX_MODE% --env "%RDX_ENV_FILE%"
if errorlevel 1 (
  echo [RDX-MCP] ERROR: Failed to prepare SSE config.
  set "EXITCODE=1"
  goto :end
)
if exist "%RDX_ENV_FILE%" (
  call "%RDX_ENV_FILE%"
  del "%RDX_ENV_FILE%" >nul 2>&1
)
goto :after_auto_sse
