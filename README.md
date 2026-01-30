# Messaging Logic for a Micro-Randomized Trial

The micro-randomized trial explores the effect of different messaging types of participant adherence to a digital biomarker study protocol.

This repository contains the logic for randomizing participants at three decision points per day into one of three experimental conditions. According to their previous data provided through MyDataHelps' mobile app, messages are customized and chosen from the respective message bank to send to users.

The code reuses and adapts elements of MyDataHelps' Python API Quickstart (`https://github.com/CareEvolution/mydatahelps-rest-api-python-quickstart`) and the public JITAI Case Study (`https://developer.mydatahelps.org/casestudy/jitai.html`).

## Structure

Lambda functions:
- `jitai_logic.py` checks the adherence of the study participants to food logging thus far and schedules messages (runs every 15 min).
- `notifier_logic.py` dispatches the messages scheduled by jitai_logic (runs every few minutes).
- `inactivity_alerts.py` sends emails to the study team if a participant is missing Ultrahuman data (runs every 3h).
- `weekly_report_and_backup.py` sends an email to the study team regarding all participants' current adherence to food logging and backs UH data up to AWS S3 (runs every Wednesday at 06:00).