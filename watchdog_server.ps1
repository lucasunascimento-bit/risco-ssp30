$python  = "C:\Users\lucasn\AppData\Local\Programs\Python\Python312\python.exe"
$script  = "C:\Users\lucasn\risco_ssp30\on_way_server.py"
$workdir = "C:\Users\lucasn\risco_ssp30"
$log     = "C:\Users\lucasn\risco_ssp30\server.log"
$intervalo = 30

while ($true) {
    $rodando = $false
    try {
        $r = Invoke-RestMethod -Uri "http://localhost:5000/ping" -TimeoutSec 3
        if ($r.ok) { $rodando = $true }
    } catch {}

    if (-not $rodando) {
        $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        Add-Content $log "[$ts] Servidor offline — reiniciando..."
        Start-Process -FilePath $python `
            -ArgumentList $script `
            -WorkingDirectory $workdir `
            -WindowStyle Hidden `
            -RedirectStandardOutput "$workdir\server_stdout.log" `
            -RedirectStandardError  "$workdir\server_stderr.log"
        Start-Sleep 5
        try {
            $r2 = Invoke-RestMethod -Uri "http://localhost:5000/ping" -TimeoutSec 5
            if ($r2.ok) {
                Add-Content $log "[$ts] Servidor reiniciado com sucesso."
            }
        } catch {
            Add-Content $log "[$ts] AVISO: servidor não respondeu após reiniciar."
        }
    }

    Start-Sleep $intervalo
}
