@echo off
REM ============================================================
REM  Pipeline Call Center — Programador de Tareas Windows
REM  Apuntar este .bat en Task Scheduler como acción a ejecutar
REM ============================================================

SET DIR_PROYECTO=%~dp0
SET LOG_DIR=%DIR_PROYECTO%\logs
FOR /F "tokens=*" %%i IN ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') DO SET FECHA=%%i
SET LOG_FILE=%LOG_DIR%\pipeline_%FECHA%.log
SET PYTHON=python
SET PYTHONIOENCODING=utf-8

REM Crear carpeta de logs si no existe
IF NOT EXIST "%LOG_DIR%" MKDIR "%LOG_DIR%"

echo [%DATE% %TIME%] Iniciando pipeline... >> "%LOG_FILE%"

REM ── Montar unidad de red Y:\ ──────────────────────────────────────────────────
net use Y: /delete /yes > nul 2>&1
net use Y: \\TU_SERVIDOR\tu_carpeta "TU_CONTRASENA" /user:DOMINIO\usuario /persistent:no >> "%LOG_FILE%" 2>&1

IF %ERRORLEVEL% NEQ 0 (
    echo [%DATE% %TIME%] ERROR: No se pudo conectar a Y:\. Verifique red o credenciales. >> "%LOG_FILE%"
    exit /b 1
)
echo [%DATE% %TIME%] Unidad Y:\ montada correctamente. >> "%LOG_FILE%"

REM ── Ejecutar pipeline (el .py maneja errores y envia correo si falla) ─────────
chcp 65001 > nul
"%PYTHON%" "%DIR_PROYECTO%\pipeline_call_center.py" >> "%LOG_FILE%" 2>&1
SET CODIGO_ERROR=%ERRORLEVEL%

IF %CODIGO_ERROR% EQU 0 (
    echo [%DATE% %TIME%] Pipeline finalizado correctamente. >> "%LOG_FILE%"
) ELSE (
    echo [%DATE% %TIME%] ERROR: el pipeline terminó con código %CODIGO_ERROR%. >> "%LOG_FILE%"
)

REM ── Desmontar unidad de red ───────────────────────────────────────────────────
net use Y: /delete /yes > nul 2>&1

exit /b %CODIGO_ERROR%
