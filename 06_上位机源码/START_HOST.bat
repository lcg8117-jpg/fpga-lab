@echo off
chcp 65001 >nul
set "ROOT=%~dp0"
set "HOST_PYTHON=%ROOT%host\fd2\upstream\runtime\python.exe"
if not exist "%HOST_PYTHON%" (
  echo Missing bundled FD2 Python 3.12 runtime.
  pause
  exit /b 1
)
echo Select the firmware already programmed on J280.
echo 1. ADB1 07 CommandFlow / S1 - USB, SWM4 and gestures
echo 2. V4 USB + BLE - 5634523A, six relative positions
echo 3. G3 C1 - USB and GOWIN mapped display
echo 4. DS13 R2 - USB and live drawing
echo Close the previous Host before using port 8371.
echo Start offline; connect the matching device explicitly in the Host window.
choice /c 1234 /n /m "Select 1-4: "
set "HOST_VARIANT="
if "%ERRORLEVEL%"=="1" set "HOST_VARIANT=ADB1_07"
if "%ERRORLEVEL%"=="2" set "HOST_VARIANT=V4_USB_BLE"
if "%ERRORLEVEL%"=="3" set "HOST_VARIANT=G3_C1"
if "%ERRORLEVEL%"=="4" set "HOST_VARIANT=DS13"
if "%HOST_VARIANT%"=="V4_USB_BLE" set "HOST_PYTHON=%ROOT%host\v4_usb_ble\PC_Bluetooth\runtime\python.exe"
if "%HOST_VARIANT%"=="DS13" set "HOST_PYTHON=%ROOT%host\ds13\releases\20261103_r2\DS13_App\runtime\python.exe"
if not exist "%HOST_PYTHON%" (
  echo Missing bundled runtime for %HOST_VARIANT%.
  pause
  exit /b 1
)
pushd "%ROOT%"
"%HOST_PYTHON%" -B -X utf8 "%ROOT%launch_host.py" "%HOST_VARIANT%"
set "HOST_EXIT=%ERRORLEVEL%"
popd
if not "%HOST_EXIT%"=="0" pause
exit /b %HOST_EXIT%
