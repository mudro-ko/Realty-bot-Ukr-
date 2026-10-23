@echo off
chcp 65001 >nul
set "DIR=%~dp0"
set "LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Realty bot.lnk"
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK); $s.TargetPath='%DIR%start.bat'; $s.WorkingDirectory='%DIR%'; $s.WindowStyle=7; $s.Save()"
if exist "%LNK%" ( echo Готово: бот стартуватиме згорнутим при вході у Windows. ) else ( echo [!] Не вдалося створити ярлик автозапуску. )
pause
