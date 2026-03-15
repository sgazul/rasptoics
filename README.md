Parsing of a lecturer’s schedule from SPbSEU (rasp.unecon.ru)
and generation of a .ics file for import into Google Calendar or any other calendar.

To run the script, I recommend doing the following:
1) Upload main.py to the script directory.
2) Create a virtual environment: python3 -m venv rasptoics
3) Activate the virtual environment: rasptoics\Scripts\activate
4) Install the required libraries (run this from the same directory since the venv is active): pip install pytz requests bs4 icalendar lxml
Then try running the script.

You can run the script with following parameters:
python unecon_to_ics.py --prepod 7998 --week 29
python unecon_to_ics.py --url "https://rasp.unecon.ru/raspisanie_prepod.php?p=7998&w=29"
