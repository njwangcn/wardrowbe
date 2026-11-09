@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo ============================================
echo   虚拟试穿 · 本地预览
echo ============================================
echo.
if not exist "tryon\.env" (
  echo [提示] 还没找到 tryon\.env
  echo        请先复制 tryon\.env.example 为 tryon\.env 并填好 4 项配置。
  echo.
)
echo 启动后请在浏览器打开 http://127.0.0.1:8787
echo 按 Ctrl+C 可停止。
echo.
python tryon\serve.py
pause
