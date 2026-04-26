@echo off
REM build_exe.bat — produces dist\img_player\img_player.exe (and friends).
REM Double-click or run from a developer prompt. Activates the conda env
REM and invokes PyInstaller via the spec file at the repo root.

setlocal enableextensions
set ENV_NAME=img_player

REM ---- Locate conda activate script -----------------------------------
set ACTIVATE=
if exist "%USERPROFILE%\miniforge3\Scripts\activate.bat" set ACTIVATE="%USERPROFILE%\miniforge3\Scripts\activate.bat"
if not defined ACTIVATE if exist "%USERPROFILE%\miniconda3\Scripts\activate.bat" set ACTIVATE="%USERPROFILE%\miniconda3\Scripts\activate.bat"
if not defined ACTIVATE if exist "%USERPROFILE%\anaconda3\Scripts\activate.bat" set ACTIVATE="%USERPROFILE%\anaconda3\Scripts\activate.bat"
if not defined ACTIVATE if exist "%ProgramData%\miniforge3\Scripts\activate.bat" set ACTIVATE="%ProgramData%\miniforge3\Scripts\activate.bat"
if not defined ACTIVATE (
    echo.
    echo [build_exe] No conda / miniforge install found in the usual places.
    echo Install Miniforge from https://github.com/conda-forge/miniforge
    pause
    exit /b 1
)

REM ---- Activate env ----------------------------------------------------
call %ACTIVATE% %ENV_NAME%
if errorlevel 1 (
    echo.
    echo [build_exe] Failed to activate conda env "%ENV_NAME%".
    echo Create it once with:
    echo   conda env create -f environment.yml
    echo Then install the build extras:
    echo   pip install -e .[build]
    pause
    exit /b 1
)

REM ---- Make sure PyInstaller is installed ------------------------------
python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo [build_exe] PyInstaller is not installed. Installing the build extras now...
    pip install -e .[build]
    if errorlevel 1 (
        echo [build_exe] pip install failed. Aborting.
        pause
        exit /b 1
    )
)

REM ---- Clean previous build outputs -----------------------------------
pushd "%~dp0"
if exist build  rmdir /s /q build
if exist dist   rmdir /s /q dist

REM ---- Run PyInstaller -------------------------------------------------
echo.
echo [build_exe] Running PyInstaller (this takes a few minutes)...
echo.
pyinstaller img_player.spec --noconfirm
set EXIT_CODE=%ERRORLEVEL%

if %EXIT_CODE% EQU 0 (
    echo.
    echo [build_exe] Done. Bundle is in:
    echo   %CD%\dist\img_player\
    echo.
    echo Test it with:
    echo   dist\img_player\img_player.exe --version
    echo.
    echo To deploy: copy the entire dist\img_player\ folder to the target
    echo machine. The .exe finds its DLLs via the _internal subfolder.
)

popd
endlocal & exit /b %EXIT_CODE%
