@echo off
SETLOCAL EnableDelayedExpansion

REM Define colors for console output
SET GREEN=[32m
SET BLUE=[34m
SET YELLOW=[33m
SET RED=[31m
SET NC=[0m

ECHO %GREEN%Starting Bunq Hackathon Challenge Servers...%NC%

REM Store current directory paths
SET "PROJECT_ROOT=%CD%"
SET "EXTENSION_DIR=%PROJECT_ROOT%\chrome-extension"
SET "STREAMING_DIR=%PROJECT_ROOT%\streamig-server"
SET "STREAM_SERVER=%STREAMING_DIR%\src\main.py"

REM Check if Python is installed
python --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
  ECHO %RED%Python is not installed or not in PATH. Please install Python and try again.%NC%
  EXIT /B 1
)

REM Setup streaming server virtual environment if it doesn't exist
IF NOT EXIST "%STREAMING_DIR%\venv\" (
  ECHO %YELLOW%Setting up virtual environment for streaming server...%NC%
  CD "%STREAMING_DIR%"
  python -m venv venv
  CALL venv\Scripts\activate.bat
  ECHO %YELLOW%Installing streaming server dependencies...%NC%
  pip install -r requirements.txt
  CALL venv\Scripts\deactivate.bat
  CD "%PROJECT_ROOT%"
) ELSE (
  ECHO %BLUE%Using existing virtual environment for streaming server.%NC%
)

REM Setup an extension server virtual environment if it doesn't exist
IF NOT EXIST "%PROJECT_ROOT%\venv\" (
  ECHO %YELLOW%Setting up virtual environment for HTTP server...%NC%
  CD "%PROJECT_ROOT%"
  python -m venv venv
  CD "%PROJECT_ROOT%"
) ELSE (
  ECHO %BLUE%Using existing virtual environment for HTTP server.%NC%
)

REM Create a temporary batch file to run both servers
ECHO @echo off > "%TEMP%\run_http_server.bat"
ECHO CD "%EXTENSION_DIR%" >> "%TEMP%\run_http_server.bat"
ECHO CALL "%PROJECT_ROOT%\venv\Scripts\activate.bat" >> "%TEMP%\run_http_server.bat"
ECHO python -m http.server 8080 >> "%TEMP%\run_http_server.bat"

ECHO @echo off > "%TEMP%\run_streaming_server.bat"
ECHO CD "%STREAMING_DIR%" >> "%TEMP%\run_streaming_server.bat"
ECHO CALL "%STREAMING_DIR%\venv\Scripts\activate.bat" >> "%TEMP%\run_streaming_server.bat"
ECHO python src\main.py >> "%TEMP%\run_streaming_server.bat"

REM Start both servers in separate windows
ECHO %BLUE%Starting HTTP server for Chrome extension on port 8080...%NC%
START "Bunq Chrome Extension HTTP Server" CMD /K "%TEMP%\run_http_server.bat"

ECHO %BLUE%Starting Gemini Streaming server on port 8000...%NC%
START "Bunq Gemini Streaming Server" CMD /K "%TEMP%\run_streaming_server.bat"

ECHO %GREEN%All servers started in separate windows.%NC%
ECHO %BLUE%Chrome extension server running at: %NC%http://localhost:8080
ECHO %BLUE%Streaming server running at: %NC%http://localhost:8000
ECHO.
ECHO %YELLOW%To stop the servers, close their respective command windows.%NC%

PAUSE