@echo off
REM 集合式 Agent 一键停止入口
REM 双击此文件即可停止前后端服务

cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1"
