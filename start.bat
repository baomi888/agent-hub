@echo off
REM 集合式 Agent 一键启动入口
REM 双击此文件即可启动前后端服务
REM 实际逻辑在 start.ps1 中（PowerShell，避免编码与执行策略问题）

cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
