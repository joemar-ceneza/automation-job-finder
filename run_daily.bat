@echo off
rem Daily scheduled run for the Automation Job Finder (Windows Task Scheduler).
rem Edit the keywords or flags below to change what the scheduled run searches.
rem Console output goes to logs\last_scheduled_run.log (overwritten each run);
rem the full history is in logs\automation.log as always.

cd /d "c:\Users\joemar.can\Downloads\Development Projects\Python Development\automation-job-finder"
if not exist logs mkdir logs

"c:\Users\joemar.can\Downloads\Development Projects\Python Development\.venv\Scripts\python.exe" main.py "resume.pdf" "python developer, web developer" --pages 2 --min-score 8 --prune-days 30 --email > "logs\last_scheduled_run.log" 2>&1
