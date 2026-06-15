@echo off
echo Installing Verilint VSCode Extension...
echo.

:: Check if node_modules exists
if not exist "node_modules" (
    echo Installing dependencies...
    npm install
    if errorlevel 1 (
        echo Failed to install dependencies!
        pause
        exit /b 1
    )
)

:: Compile
echo Compiling TypeScript...
npm run compile
if errorlevel 1 (
    echo Failed to compile!
    pause
    exit /b 1
)

:: Package extension
echo Packaging extension...
npx vsce package --no-dependencies
if errorlevel 1 (
    echo Failed to package extension!
    pause
    exit /b 1
)

echo.
echo Extension packaged successfully!
echo.
echo To install in VSCode:
echo 1. Open VSCode
echo 2. Press Ctrl+Shift+X to open Extensions
echo 3. Click "..." menu and select "Install from VSIX"
echo 4. Select the generated verilint-1.0.0.vsix file
echo.
pause
