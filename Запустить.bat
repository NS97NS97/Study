@echo off
rem ------------------------------------------------------------------
rem  One-click launch for the end user (no terminal knowledge needed).
rem
rem  This batch file is ASCII on purpose: cmd.exe reads it using the
rem  console code page, so Cyrillic text here would break parsing.
rem  All user-facing messages are printed by Python itself in UTF-8.
rem
rem  What it does: fresh export from Google Sheets -> calculation ->
rem  "Recommendations for pasting" file -> Excel dashboard -> both
rem  files are opened with a short "what to do next" summary.
rem ------------------------------------------------------------------
chcp 65001 >nul 2>nul
set PYTHONUTF8=1
cd /d "%~dp0"
title Coin recommender - recommendations and dashboard

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m recommender --google --dashboard --open
) else (
    python -m recommender --google --dashboard --open
)
set LAUNCH=%errorlevel%

echo.
if not "%LAUNCH%"=="0" (
    echo FAILED with code %LAUNCH%.
    echo Make sure Python is installed and dependencies are present:
    echo    pip install -r requirements.txt
)
pause
