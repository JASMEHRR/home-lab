@echo off
rem Opens the live EyeToy window. Keys: s = snapshot (saved in "captures"), q or Esc = quit.
cd /d "%~dp0"
py -3.12 eyetoy.py %*
if errorlevel 1 pause
