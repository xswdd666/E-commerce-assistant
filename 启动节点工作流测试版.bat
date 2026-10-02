@echo off
cd /d "%~dp0"
set "COMMERCE_STUDIO_PORT=8767"
set "CANVAS_WEB_PORT=3001"
set "VITE_STUDIO_API_PORT=8767"
python -X utf8 -m commerce_studio.launcher
pause
