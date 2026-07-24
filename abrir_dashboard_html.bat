@echo off
set PYTHON="C:\Users\lucasn\AppData\Local\Programs\Python\Python312\python.exe"
set DIR=C:\Users\lucasn\risco_ssp30

:: Verifica se o servidor ON WAY ja esta rodando
powershell -Command "try { Invoke-RestMethod http://localhost:5000/ping -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if errorlevel 1 (
    echo Iniciando servidor ON WAY...
    start "" /B %PYTHON% "%DIR%\on_way_server.py"
    timeout /t 3 /nobreak >nul
)

echo Gerando Dashboard HTML Risco SSP30...
%PYTHON% "%DIR%\gerar_dashboard.py"
pause
