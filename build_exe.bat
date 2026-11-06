@echo off
REM ============================================================
REM  一键把 YOLO 标注工具打包成单文件 exe
REM  依赖:  pip install pyinstaller pillow
REM  产物:  dist\YOLOAnnotator.exe  (可复制到桌面双击运行)
REM ============================================================
cd /d "%~dp0"

echo [1/2] 正在打包 (首次约 1-2 分钟)...
python -m PyInstaller --onefile --console --clean --noconfirm ^
    --icon app_icon.ico --name YOLOAnnotator annotation_server.py
if errorlevel 1 (
    echo.
    echo 打包失败, 请确认已安装依赖:  python -m pip install pyinstaller
    pause
    exit /b 1
)

echo.
echo [2/2] 打包完成!
echo   产物: %cd%\dist\YOLOAnnotator.exe
echo   把它复制到桌面, 双击即可运行 (会自动打开浏览器).
echo.
pause
