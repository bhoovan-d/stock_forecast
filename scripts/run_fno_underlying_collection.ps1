$ErrorActionPreference = 'Stop'
$env:PYTHONPATH = 'C:\adiproj2\systems\fno_momentum\src'
Set-Location -LiteralPath 'C:\adiproj2'
& 'C:\Users\bhoov\AppData\Local\Programs\Python\Launcher\py.exe' -m fno_momentum.full_underlying_collection serve
