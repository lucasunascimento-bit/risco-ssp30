Set oShell = CreateObject("WScript.Shell")
oShell.Run "powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File ""C:\Users\lucasn\risco_ssp30\watchdog_server.ps1""", 0, False
