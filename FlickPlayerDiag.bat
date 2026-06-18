@echo off
REM FlickPlayerDiag.bat — standalone copy of FlickPlayer.bat with the
REM cache eviction diagnostics turned on. Writes "[diag-evict]" lines
REM to flick.log showing which frames the cache evicts vs keeps and
REM why. Use to debug "the cache empties from the wrong side"; switch
REM back to FlickPlayer.bat for normal use.

setlocal enableextensions
set ENV_NAME=img_player

REM ---- THE diagnostic switch (the whole point of this launcher) ------
set FLICK_DIAG_EVICT=1

pushd "%~dp0"

REM ---- Locate conda activate script -----------------------------------
set ACTIVATE=
if exist "%LOCALAPPDATA%\miniforge3\Scripts\activate.bat" set ACTIVATE="%LOCALAPPDATA%\miniforge3\Scripts\activate.bat"
if not defined ACTIVATE if exist "%USERPROFILE%\miniforge3\Scripts\activate.bat" set ACTIVATE="%USERPROFILE%\miniforge3\Scripts\activate.bat"
if not defined ACTIVATE if exist "%USERPROFILE%\miniconda3\Scripts\activate.bat" set ACTIVATE="%USERPROFILE%\miniconda3\Scripts\activate.bat"
if not defined ACTIVATE if exist "%USERPROFILE%\anaconda3\Scripts\activate.bat" set ACTIVATE="%USERPROFILE%\anaconda3\Scripts\activate.bat"
if not defined ACTIVATE if exist "%ProgramData%\miniforge3\Scripts\activate.bat" set ACTIVATE="%ProgramData%\miniforge3\Scripts\activate.bat"
if not defined ACTIVATE (
    echo [FlickPlayerDiag] No conda / miniforge install found.
    pause
    exit /b 1
)

call %ACTIVATE% %ENV_NAME%
if errorlevel 1 (
    echo [FlickPlayerDiag] Failed to activate conda env "%ENV_NAME%".
    pause
    exit /b 1
)

REM ---- Force THIS repo's source onto the import path ------------------
set PYTHONPATH=%~dp0src;%PYTHONPATH%

echo [FlickPlayerDiag] FLICK_DIAG_EVICT=%FLICK_DIAG_EVICT% — eviction log ON
python -m img_player %*
set EXIT_CODE=%ERRORLEVEL%

if %EXIT_CODE% NEQ 0 (
    echo.
    echo [FlickPlayerDiag] Flick Player exited with code %EXIT_CODE%.
    pause
)

popd
endlocal & exit /b %EXIT_CODE%
