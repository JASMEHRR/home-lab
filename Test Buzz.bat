@echo off
rem Flashes the buzzer lights, then shows button presses for 30 seconds.
cd /d "%~dp0"
py -3.12 -u buzz.py 30
pause
