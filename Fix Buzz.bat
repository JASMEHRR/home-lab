@echo off
rem Run this if the Buzz controllers stop working (e.g. plugged into a different USB port).
rem Forces Windows' normal game-controller driver onto the Buzz receiver. Asks for admin once.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0fix_buzz_driver.ps1"
