@echo off
rem -------------------------------------------------------------
rem  Coin recommender - refresh dashboard from Google Sheets.
rem  Messages are ASCII on purpose: cmd.exe reads batch files with
rem  the console code page, and Cyrillic text breaks parsing.
rem  The Excel dashboard itself contains full Russian instructions.
rem -------------------------------------------------------------
chcp 65001 >nul 2>nul
set PYTHONUTF8=1
cd /d "%~dp0"
title Coin recommender - dashboard update

echo ============================================================
echo  UPDATING DASHBOARD  (source: Google Sheets)
echo ============================================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m recommender --google --dashboard
) else (
    python -m recommender --google --dashboard
)

echo.
if exist "reports\*.xlsx" (
    echo DONE. Open the workbook in the "reports" folder -
    echo first sheet = dashboard, last sheet = instructions.
) else (
    echo FAILED. Make sure Python is installed and run:
    echo    pip install -r requirements.txt
)
echo.
pause
