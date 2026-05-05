@echo off
REM launch_app.bat — double-click to run img_player from the conda env.
REM Activates the ``img_player`` Miniforge env, then ``python -m img_player``.
REM No need to remember commands or open Miniforge Prompt manually.

setlocal enableextensions
set ENV_NAME=img_player

REM ---- Move into the bat's own directory ------------------------------
pushd "%~dp0"

REM ---- Locate conda activate script -----------------------------------
REM Same probing order as build_exe.bat. AppData\Local\miniforge3 is the
REM "Just Me" install path used by Miniforge's default installer on
REM Windows; it isn't covered by build_exe.bat so we add it here.
set ACTIVATE=
if exist "%LOCALAPPDATA%\miniforge3\Scripts\activate.bat" set ACTIVATE="%LOCALAPPDATA%\miniforge3\Scripts\activate.bat"
if not defined ACTIVATE if exist "%USERPROFILE%\miniforge3\Scripts\activate.bat" set ACTIVATE="%USERPROFILE%\miniforge3\Scripts\activate.bat"
if not defined ACTIVATE if exist "%USERPROFILE%\miniconda3\Scripts\activate.bat" set ACTIVATE="%USERPROFILE%\miniconda3\Scripts\activate.bat"
if not defined ACTIVATE if exist "%USERPROFILE%\anaconda3\Scripts\activate.bat" set ACTIVATE="%USERPROFILE%\anaconda3\Scripts\activate.bat"
if not defined ACTIVATE if exist "%ProgramData%\miniforge3\Scripts\activate.bat" set ACTIVATE="%ProgramData%\miniforge3\Scripts\activate.bat"
if not defined ACTIVATE (
    echo.
    echo [launch_app] No conda / miniforge install found in the usual places.
    echo Install Miniforge from https://github.com/conda-forge/miniforge
    pause
    exit /b 1
)

REM ---- Activate env ---------------------------------------------------
call %ACTIVATE% %ENV_NAME%
if errorlevel 1 (
    echo.
    echo [launch_app] Failed to activate conda env "%ENV_NAME%".
    echo Create it once with:
    echo   conda env create -f environment.yml
    pause
    exit /b 1
)

REM ---- Run the app ----------------------------------------------------
python -m img_player %*
set EXIT_CODE=%ERRORLEVEL%

REM Pause only on failure so the user can see the traceback. A clean
REM exit closes the cmd window silently — the GUI was the point, not
REM the console.
if %EXIT_CODE% NEQ 0 (
    echo.
    echo [launch_app] img_player exited with code %EXIT_CODE%.
    pause
)

popd
endlocal & exit /b %EXIT_CODE%
