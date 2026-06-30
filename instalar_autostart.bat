@echo off
echo Instalando auto-start do servidor SSP30...

set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set VBS=C:\Users\lucasn\risco_ssp30\start_watchdog.vbs
set ATALHO=%STARTUP%\servidor_ssp30.vbs

copy /Y "%VBS%" "%ATALHO%" >nul

echo.
echo Verificando se o servidor ja esta rodando...
powershell -Command "try { Invoke-RestMethod http://localhost:5000/ping -TimeoutSec 2 | Out-Null; Write-Host 'Servidor ja esta rodando.' } catch { Write-Host 'Iniciando servidor agora...'; Start-Process wscript.exe -ArgumentList '%ATALHO%' }"

echo.
echo Pronto! O servidor vai iniciar automaticamente com o Windows.
echo Log de atividade: C:\Users\lucasn\risco_ssp30\server.log
echo.
pause
