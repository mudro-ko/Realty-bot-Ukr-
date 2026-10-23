@echo off
chcp 65001 >nul
title Auto bot
cd /d "%~dp0"

rem --- Python ---
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY (
  echo [!] Python не знайдено. Встанови: winget install Python.Python.3.12
  pause & exit /b 1
)

rem --- віртуальне середовище і залежності (перший запуск ~1 хв) ---
if not exist ".venv\Scripts\python.exe" (
  echo Створюю середовище .venv ...
  %PY% -m venv .venv || ( echo [!] Не вдалося створити .venv & pause & exit /b 1 )
)
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt || ( echo [!] Помилка встановлення залежностей & pause & exit /b 1 )

rem --- токен ---
if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo.
  echo Впиши токен бота у файл .env, що зараз відкриється, збережи і закрий Блокнот.
  notepad ".env"
)

echo.
echo Бот запущено. Не закривай це вікно (можна згорнути). Зупинка: Ctrl+C.
echo Лог: data\bot.log
echo.
:loop
".venv\Scripts\python.exe" auto_bot.py
if errorlevel 2 ( pause & exit /b 1 )
echo [!] Бот зупинився, перезапуск через 30 с ... (закрий вікно, щоб вийти)
timeout /t 30 /nobreak >nul
goto loop
