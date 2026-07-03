@echo off
cd /d "C:\Users\Vaibhav Choudhary\Downloads\Karan Bday\BMS Clone"
python build_website.py
git add -A
git commit -m "Daily rebuild: remove past events [%date%]"
git push
