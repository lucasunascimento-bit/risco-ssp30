@echo off
cd /d C:\Users\lucasn\risco_ssp30

:: Verifica se o servidor ja ta rodando
curl -s --max-time 1 http://localhost:5000/ping >nul 2>&1
if %errorlevel%==0 (
    start "" "http://localhost:5000/"
    exit
)

:: Sobe o servidor em background
start "" /min cmd /c python on_way_server.py

:: Aguarda o servidor responder (ate 15s)
set /a tentativa=0
:loop
timeout /t 1 /nobreak >nul
curl -s --max-time 1 http://localhost:5000/ping >nul 2>&1
if %errorlevel%==0 goto pronto
set /a tentativa+=1
if %tentativa% lss 15 goto loop

:pronto
start "" "http://localhost:5000/"
