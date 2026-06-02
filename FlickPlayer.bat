@echo off
REM FlickPlayer.bat — double-click, or drag a sequence folder / file
REM onto this .bat, to run Flick Player from the conda dev env.
REM Activates the ``img_player`` Miniforge env (the env name stays as
REM ``img_player`` for legacy reasons — renaming a conda env requires
REM recreating it) then runs ``python -m img_player`` with any args
REM that were passed in or dropped on the .bat. Use the standalone
REM FlickPlayer.exe bundle (see BUILD.md) for end-users — this
REM launcher is for developers running from a source checkout.

setlocal enableextensions
set ENV_NAME=img_player

REM ---- Move into the bat's own directory ------------------------------
pushd "%~dp0"

REM ---- Locate conda activate script -----------------------------------
REM AppData\Local\miniforge3 is the default "Just Me" install path used
REM by Miniforge's Windows installer, so we probe it first.
set ACTIVATE=
if exist "%LOCALAPPDATA%\miniforge3\Scripts\activate.bat" set ACTIVATE="%LOCALAPPDATA%\miniforge3\Scripts\activate.bat"
if not defined ACTIVATE if exist "%USERPROFILE%\miniforge3\Scripts\activate.bat" set ACTIVATE="%USERPROFILE%\miniforge3\Scripts\activate.bat"
if not defined ACTIVATE if exist "%USERPROFILE%\miniconda3\Scripts\activate.bat" set ACTIVATE="%USERPROFILE%\miniconda3\Scripts\activate.bat"
if not defined ACTIVATE if exist "%USERPROFILE%\anaconda3\Scripts\activate.bat" set ACTIVATE="%USERPROFILE%\anaconda3\Scripts\activate.bat"
if not defined ACTIVATE if exist "%ProgramData%\miniforge3\Scripts\activate.bat" set ACTIVATE="%ProgramData%\miniforge3\Scripts\activate.bat"
if not defined ACTIVATE (
    echo.
    echo [FlickPlayer] No conda / miniforge install found in the usual places.
    echo Looked in:
    echo   %%LOCALAPPDATA%%\miniforge3
    echo   %%USERPROFILE%%\miniforge3
    echo   %%USERPROFILE%%\miniconda3
    echo   %%USERPROFILE%%\anaconda3
    echo   %%ProgramData%%\miniforge3
    echo.
    echo Install Miniforge from https://github.com/conda-forge/miniforge
    pause
    exit /b 1
)

REM ---- Activate env ---------------------------------------------------
call %ACTIVATE% %ENV_NAME%
if errorlevel 1 (
    echo.
    echo [FlickPlayer] Failed to activate conda env "%ENV_NAME%".
    echo Create it once with:
    echo   conda env create -f environment.yml
    pause
    exit /b 1
)

REM ---- Force THIS repo's source onto the import path ------------------
REM ``python -m img_player`` would otherwise import whatever
REM ``img_player`` is pip-installed in the conda env. On a machine
REM whose env has a stale editable / non-editable install, that can
REM be an OLD version — the classic "the launcher runs 1.5.5 while
REM the repo is on 1.8" symptom. Prepending this repo's ``src``
REM guarantees the launcher always runs THIS checkout's code,
REM regardless of the env's install state.
set PYTHONPATH=%~dp0src;%PYTHONPATH%

REM ---- Run the app ----------------------------------------------------
python -m img_player %*
set EXIT_CODE=%ERRORLEVEL%

REM Pause only on failure so the user can see the traceback. A clean
REM exit closes the cmd window silently — the GUI was the point, not
REM the console.
if %EXIT_CODE% NEQ 0 (
    echo.
    echo [FlickPlayer] Flick Player exited with code %EXIT_CODE%.
    pause
)

popd
endlocal & exit /b %EXIT_CODE%
