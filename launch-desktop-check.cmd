@echo off
setlocal
if not exist "%~dp0.runtime\windows-packaging\launch-desktop-check.cmd" (
  echo Desktop check launcher is missing from this project.
  pause
  exit /b 1
)
call "%~dp0.runtime\windows-packaging\launch-desktop-check.cmd"
endlocal
