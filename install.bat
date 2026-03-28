@echo off
chcp 65001 >nul 2>&1
title geo-fix — Установка
echo.
echo ╔══════════════════════════════════════╗
echo ║     geo-fix — Автоматическая        ║
echo ║         установка                   ║
echo ╚══════════════════════════════════════╝
echo.

:: Проверяем, что запущено от администратора (для файрвола, опционально)
set "INSTALL_DIR=%LOCALAPPDATA%\geo-fix"
set "PYTHON_DIR=%INSTALL_DIR%\python"
set "APP_DIR=%INSTALL_DIR%\app"
set "DESKTOP=%USERPROFILE%\Desktop"
set "PYTHON_VERSION=3.12.7"
set "PYTHON_URL=https://www.python.org/ftp/python/%PYTHON_VERSION%/python-%PYTHON_VERSION%-embed-amd64.zip"
:: Pinned SHA-256 for Python 3.12.7 embed AMD64 (verify at https://www.python.org/downloads/release/python-3127/)
set "PYTHON_ZIP_HASH=73AC3E2852AEB3FEDDFEEE3AA1D0EF63997DCBE51DAC24A20C12ACC29E1E7B30"
set "PIP_VERSION=24.3.1"
set "PIP_URL=https://bootstrap.pypa.io/pip/%PIP_VERSION%/get-pip.py"
:: Pinned SHA-256 for get-pip.py v24.3.1 (verify by downloading and computing hash)
set "PIP_HASH=6FB7B781206356F45AD79EFBB19322CAA6C2A5AD39092D0D44D0FEC94117E118"

echo [1/6] Создаю папку установки...
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
if not exist "%PYTHON_DIR%" mkdir "%PYTHON_DIR%"
if not exist "%APP_DIR%" mkdir "%APP_DIR%"

:: Скачиваем Python Embedded
echo [2/6] Скачиваю Python (portable)...
if not exist "%PYTHON_DIR%\python.exe" (
    powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile '%INSTALL_DIR%\python-embed.zip'}" 2>nul
    if errorlevel 1 (
        echo ОШИБКА: Не удалось скачать Python. Проверьте интернет-соединение.
        pause
        exit /b 1
    )
    :: Verify SHA-256 hash
    powershell -Command "& { $h = (Get-FileHash '%INSTALL_DIR%\python-embed.zip' -Algorithm SHA256).Hash; if ($h.ToUpper() -ne '%PYTHON_ZIP_HASH%'.ToUpper()) { Write-Error ('Hash mismatch: expected ' + '%PYTHON_ZIP_HASH%' + ' got ' + $h); exit 1 } }"
    if errorlevel 1 (
        echo ОШИБКА: Хэш Python zip не совпадает. Возможно, файл повреждён или подменён.
        del "%INSTALL_DIR%\python-embed.zip" 2>nul
        pause
        exit /b 2
    )
    echo    Распаковываю...
    powershell -Command "Expand-Archive -Path '%INSTALL_DIR%\python-embed.zip' -DestinationPath '%PYTHON_DIR%' -Force" 2>nul
    del "%INSTALL_DIR%\python-embed.zip" 2>nul

    :: Включаем import site для pip
    powershell -Command "(Get-Content '%PYTHON_DIR%\python312._pth') -replace '#import site','import site' | Set-Content '%PYTHON_DIR%\python312._pth'"
) else (
    echo    Python уже установлен, пропускаю.
)

:: Устанавливаем pip
echo [3/6] Устанавливаю pip...
if not exist "%PYTHON_DIR%\Scripts\pip.exe" (
    powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%PIP_URL%' -OutFile '%INSTALL_DIR%\get-pip.py'}" 2>nul
    :: Verify SHA-256 hash
    powershell -Command "& { $h = (Get-FileHash '%INSTALL_DIR%\get-pip.py' -Algorithm SHA256).Hash; if ($h.ToUpper() -ne '%PIP_HASH%'.ToUpper()) { Write-Error ('Hash mismatch: expected ' + '%PIP_HASH%' + ' got ' + $h); exit 1 } }"
    if errorlevel 1 (
        echo ОШИБКА: Хэш get-pip.py не совпадает. Возможно, файл повреждён или подменён.
        del "%INSTALL_DIR%\get-pip.py" 2>nul
        pause
        exit /b 2
    )
    "%PYTHON_DIR%\python.exe" "%INSTALL_DIR%\get-pip.py" --no-warn-script-location >nul 2>&1
    del "%INSTALL_DIR%\get-pip.py" 2>nul
) else (
    echo    pip уже установлен, пропускаю.
)

:: Устанавливаем зависимости
echo [4/6] Устанавливаю зависимости (mitmproxy, pystray, Pillow)...
"%PYTHON_DIR%\python.exe" -m pip install mitmproxy pystray Pillow --no-warn-script-location -q 2>nul
if errorlevel 1 (
    echo ОШИБКА: Не удалось установить зависимости.
    pause
    exit /b 1
)

:: Копируем исходный код приложения
echo [5/6] Копирую файлы приложения...
xcopy /E /Y /Q "%~dp0src\*" "%APP_DIR%\src\" >nul 2>&1

:: Создаём лаунчер
echo @echo off > "%INSTALL_DIR%\geo-fix.bat"
echo chcp 65001 ^>nul 2^>^&1 >> "%INSTALL_DIR%\geo-fix.bat"
echo "%PYTHON_DIR%\python.exe" "%APP_DIR%\src\main.py" %%* >> "%INSTALL_DIR%\geo-fix.bat"
attrib +R "%INSTALL_DIR%\geo-fix.bat"

:: Создаём ярлыки на рабочем столе
echo [6/6] Создаю ярлыки на рабочем столе...
call :create_shortcut "geo-fix Включить (US)" "US"
call :create_shortcut "geo-fix Включить (DE)" "DE"
call :create_shortcut "geo-fix Включить (NL)" "NL"
call :create_shortcut "geo-fix Включить (GB)" "GB"
call :create_shortcut "geo-fix Выключить" "--stop"

echo.
echo ╔══════════════════════════════════════╗
echo ║     Установка завершена!            ║
echo ╠══════════════════════════════════════╣
echo ║                                      ║
echo ║  На рабочем столе созданы ярлыки:   ║
echo ║                                      ║
echo ║  • geo-fix Включить (US)            ║
echo ║  • geo-fix Включить (DE)            ║
echo ║  • geo-fix Включить (NL)            ║
echo ║  • geo-fix Включить (GB)            ║
echo ║  • geo-fix Выключить                ║
echo ║                                      ║
echo ║  1. Включите VPN                    ║
echo ║  2. Запустите нужный ярлык          ║
echo ║  3. При первом запуске откроется    ║
echo ║     мастер настройки                ║
echo ║                                      ║
echo ╚══════════════════════════════════════╝
echo.
echo Папка установки: %INSTALL_DIR%
echo.
pause
exit /b 0

:create_shortcut
:: Создаёт ярлык .lnk на рабочем столе
set "SHORTCUT_NAME=%~1"
set "SHORTCUT_ARG=%~2"
powershell -Command "& {$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%DESKTOP%\%SHORTCUT_NAME%.lnk'); $s.TargetPath = '%INSTALL_DIR%\geo-fix.bat'; $s.Arguments = '%SHORTCUT_ARG%'; $s.WorkingDirectory = '%INSTALL_DIR%'; $s.Description = 'geo-fix: %SHORTCUT_NAME%'; $s.WindowStyle = 7; $s.Save()}" 2>nul
exit /b 0
