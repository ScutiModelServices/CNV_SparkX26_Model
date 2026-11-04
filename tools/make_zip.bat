@echo off
rem Build zip_versions\SparkX26Model-<VERSION>.zip, select it in Explorer, and open GitHub's new-release page for that tag
rem so the zip can be dragged onto it. Run it after the release is tagged (deploy.bat option R).
setlocal
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
py -3 "%~dp0make_zip.py" || python "%~dp0make_zip.py" || (echo ZIP creation failed. & pause & exit /b 1)
set /p VER=<"%ROOT%\VERSION"
start "" explorer /select,"%ROOT%\zip_versions\SparkX26Model-%VER%.zip"
start "" "https://github.com/scutiii2/SparkX26Model/releases/new?tag=v%VER%"
echo.
echo Drag zip_versions\SparkX26Model-%VER%.zip onto the release page, paste the notes, and publish.
pause
