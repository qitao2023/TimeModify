@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

:: 检查管理员权限
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo 请求管理员权限...
    echo Set UAC = CreateObject^("Shell.Application"^) > "%temp%\getadmin.vbs"
    echo UAC.ShellExecute "%~s0", "", "", "runas", 1 >> "%temp%\getadmin.vbs"
    "%temp%\getadmin.vbs"
    del "%temp%\getadmin.vbs"
    exit /b
)


:: 直接使用PowerShell命令设置时间，避免格式问题
date 2024/11/20

echo 等待60秒后恢复系统时间...
echo.

:: 保存原始提示信息
set "original_msg=已经改为2024/11/20，1分钟后恢复时间...
等待60秒后恢复系统时间..."

:: 倒计时显示
set /a count=60

:countdown
:: 使用临时文件保存原始信息
echo !original_msg! > "%temp%\temp_msg.txt"
echo. >> "%temp%\temp_msg.txt"
echo 剩余时间: !count! 秒 >> "%temp%\temp_msg.txt"

:: 显示内容
cls
type "%temp%\temp_msg.txt"

if !count! GTR 0 (
    timeout /t 1 /nobreak >nul
    set /a count-=1
    goto :countdown
)

:: 清理临时文件
del "%temp%\temp_msg.txt" >nul 2>&1

:: 直接使用PowerShell命令设置时间，避免格式问题
powershell -command "net time \\192.168.0.7 /set /y"
echo 系统时间已恢复!
