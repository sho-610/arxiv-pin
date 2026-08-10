@echo off
cd /d "%~dp0"

if "%~1"=="" (
  echo.
  echo   HTML ファイルをこの run.bat にドラッグ ^& ドロップしてください。
  echo.
  pause
  exit /b
)

:loop
if "%~1"=="" goto done
echo.
echo === %~nx1 ===
uv run arxiv-pin "%~1"
shift
goto loop

:done
echo.
pause
