@echo off
echo Instalando auto-start do servidor SSP30 (via registro)...

set PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe
set SCRIPT=C:\Users\lucasn\risco_ssp30\watchdog_server.ps1
set CHAVE=HKCU\Software\Microsoft\Windows\CurrentVersion\Run
set NOME=ServidorSSP30
set CMD="%PS%" -ExecutionPolicy Bypass -WindowStyle Hidden -NonInteractive -File "%SCRIPT%"

rem Remove entrada antiga (VBS) da pasta Startup, se existir
set ATALHO=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\servidor_ssp30.vbs
if exist "%ATALHO%" del /F /Q "%ATALHO%"

rem Registra no HKCU Run — inicia junto com o login do usuario
reg add "%CHAVE%" /v "%NOME%" /t REG_SZ /d %CMD% /f >nul

echo.
echo Registrado em: %CHAVE%\%NOME%
echo Comando:       %CMD%
echo.

rem Inicia o watchdog agora (sem esperar pelo proximo login)
powershell -Command "try { Invoke-RestMethod http://localhost:5000/ping -TimeoutSec 2 | Out-Null; Write-Host 'Servidor ja esta rodando.' } catch { Write-Host 'Iniciando watchdog...'; Start-Process -FilePath '%PS%' -ArgumentList '-ExecutionPolicy Bypass -WindowStyle Hidden -NonInteractive -File ""%SCRIPT%""' }"

echo.
echo Pronto! O servidor iniciara automaticamente em todo login.
echo Log: C:\Users\lucasn\risco_ssp30\server.log
echo.
pause
