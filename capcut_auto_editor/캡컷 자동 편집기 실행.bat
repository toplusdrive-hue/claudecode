@echo off
rem ===================================================================
rem  캡컷 자동 편집기 실행
rem
rem  [주의] 이 파일은 CP949(ANSI)로 저장해야 합니다.
rem  한국어 윈도우 cmd는 배치 파일을 OEM 코드페이지(949)로 읽습니다.
rem  UTF-8로 저장한 뒤 파일 안에서 코드페이지를 65001로 바꾸면 cmd가 읽던 위치를
rem  잃고 아무 메시지 없이 즉시 종료됩니다. (창이 깜빡하고 사라짐)
rem  그래서 이 파일에는 코드페이지 변경 명령이 없습니다. 편집기에서 저장할 때
rem  인코딩을 ANSI / CP949 로 유지하고, 코드페이지 변경 명령을 넣지 마세요.
rem ===================================================================

setlocal
cd /d "%~dp0"

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

echo.
echo  캡컷 자동 편집기를 시작합니다.
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    py -3.11 run.py
    if errorlevel 1 (
        echo.
        echo  [알림] 파이썬 3.11 로 실행하지 못했습니다. 기본 파이썬으로 다시 시도합니다.
        echo.
        py run.py
    )
    goto done
)

where python >nul 2>nul
if %errorlevel%==0 (
    python run.py
    goto done
)

echo  [중단] 파이썬을 찾을 수 없습니다.
echo         python.org 에서 3.11 을 설치하고, 설치 화면에서
echo         "Add Python to PATH" 를 체크해 주세요.

:done
echo.
echo  창을 닫으려면 아무 키나 누르세요.
pause >nul
endlocal
