@echo off
py -3 -X utf8 "%~dp0desk.py" %*
exit /b %errorlevel%
