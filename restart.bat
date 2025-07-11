@echo off
echo Kuafor Yonetim Sistemi yeniden baslatiliyor...
taskkill /f /im fidel.exe >nul 2>&1
timeout /t 2 /nobreak >nul
start fidel.exe
echo Uygulama yeniden baslatildi!
timeout /t 3