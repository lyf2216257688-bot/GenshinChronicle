@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
if not exist ".local\runtime-py314\generation\Scripts\python.exe" (
  echo Missing Generation runtime. See docs/local-chat.md.
  pause
  exit /b 1
)
".local\runtime-py314\generation\Scripts\python.exe" -m genshin_corpus.local_chat %*
set "GC_EXIT_CODE=%errorlevel%"
if not "%GC_EXIT_CODE%"=="0" if "%~1"=="" pause
exit /b %GC_EXIT_CODE%
