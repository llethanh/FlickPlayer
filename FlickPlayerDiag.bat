@echo off
REM FlickPlayerDiag.bat — temp diagnostic launcher for the 30 fps bug.
REM Sets FLICK_DIAG=1 so the controller + GL viewport emit per-second
REM cadence / paint logs into %LOCALAPPDATA%\FlickPlayer\flick.log,
REM then calls the normal FlickPlayer.bat with whatever args you pass.
REM Use exactly like FlickPlayer.bat (double-click, or drag a file
REM onto it). After running and exiting, share the tail of flick.log.

setlocal enableextensions
set FLICK_DIAG=1
call "%~dp0FlickPlayer.bat" %*
endlocal
