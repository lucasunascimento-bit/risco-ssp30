$python  = "C:\Users\lucasn\AppData\Local\Programs\Python\Python312\python.exe"
$script  = "C:\Users\lucasn\risco_ssp30\on_way_server.py"
$workdir = "C:\Users\lucasn\risco_ssp30"

try {
    Invoke-RestMethod -Uri "http://localhost:5000/ping" -TimeoutSec 2 | Out-Null
} catch {
    Start-Process -FilePath $python `
        -ArgumentList $script `
        -WorkingDirectory $workdir `
        -WindowStyle Hidden `
        -RedirectStandardOutput "$workdir\server_stdout.log" `
        -RedirectStandardError  "$workdir\server_stderr.log"
}
