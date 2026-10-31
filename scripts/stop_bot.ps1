# Stop the QQ bot: AstrBot (python main.py), NapCat's own QQ.exe, and the local voice service
# (cover\rvc_server.py / cover_job.py). Only these.
# Your normal QQ (installed elsewhere) is not touched.
$me = $PID
$procs = Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $me }
$astr = $procs | Where-Object { $_.CommandLine -and $_.CommandLine -like '*\AstrBot\main.py*' -and $_.Name -like 'python*' }
$nap  = $procs | Where-Object { $_.ExecutablePath -and $_.ExecutablePath -like '*\NapCat\NapCat.*.Shell\*' }
$voice = $procs | Where-Object { $_.CommandLine -and ($_.CommandLine -like '*\cover\rvc_server.py*' -or $_.CommandLine -like '*\cover\cover_job.py*') }
foreach ($p in @($nap) + @($astr) + @($voice)) {
    if ($p) {
        Write-Host ("Stopping {0} (PID {1})" -f $p.Name, $p.ProcessId)
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
}
if (-not $astr -and -not $nap -and -not $voice) { Write-Host 'Nothing to stop: the bot is not running.' }
