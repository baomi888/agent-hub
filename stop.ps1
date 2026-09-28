# 集合式 Agent 一键停止脚本（PowerShell）
# 由 stop.bat 调用

$BACKEND_PORT = 8000
$FRONTEND_PORT = 3000

function Stop-ByPort($port) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($conns) {
        foreach ($c in $conns) {
            try {
                Stop-Process -Id $c.OwningProcess -Force -ErrorAction Stop
                Write-Host "[OK] 已停止端口 $port 上的进程 (PID: $($c.OwningProcess))" -ForegroundColor Green
            } catch {
                Write-Host "[!] 停止端口 $port 进程失败: $($_.Exception.Message)" -ForegroundColor Yellow
            }
        }
    } else {
        Write-Host "[-] 端口 $port 无运行中的服务" -ForegroundColor Gray
    }
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Magenta
Write-Host "   集合式 Agent  停止服务" -ForegroundColor Magenta
Write-Host "============================================" -ForegroundColor Magenta
Write-Host ""

Stop-ByPort $BACKEND_PORT
Stop-ByPort $FRONTEND_PORT

# 清理可能残留的 uvicorn / next 子进程
$uvicorn = Get-Process -Name python -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like "*uvicorn*" }
if ($uvicorn) { $uvicorn | Stop-Process -Force -ErrorAction SilentlyContinue }

Write-Host ""
Write-Host "[完成] 所有服务已停止" -ForegroundColor Green
Write-Host ""
Start-Sleep -Seconds 1
