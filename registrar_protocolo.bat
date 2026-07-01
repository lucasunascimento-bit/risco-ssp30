@echo off
echo Registrando protocolo ssp30:// para iniciar servidor...

set PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe
set SCRIPT=C:\Users\lucasn\risco_ssp30\iniciar_servidor.ps1
set CMD="%PS%" -ExecutionPolicy Bypass -WindowStyle Hidden -File "%SCRIPT%"
set BASE=HKCU\Software\Classes\ssp30

reg add "%BASE%"                        /ve /d "URL:SSP30 Server" /f >nul
reg add "%BASE%"                        /v "URL Protocol" /d "" /f   >nul
reg add "%BASE%\shell\open\command"     /ve /d %CMD% /f              >nul

echo.
echo Protocolo ssp30:// registrado com sucesso!
echo Agora o botao "Iniciar Servidor" nos dashboards ja funciona.
echo.
pause
