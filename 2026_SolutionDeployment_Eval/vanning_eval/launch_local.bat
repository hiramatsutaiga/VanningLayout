@echo off
REM vanning-eval WebUI launcher (LOCAL FS mode for testing)
REM
REM scoreboard_client が GitHub API でなくローカル ../scoreboard/ を読み書きする。
REM history.json / submissions/ への投稿は git commit/push しない限りチームには見えない。
REM 本番投稿には launch.bat を使うこと。
chcp 65001 >nul

cd /d "%~dp0"
set VANNING_LOCAL_SCOREBOARD=1

call launch.bat
