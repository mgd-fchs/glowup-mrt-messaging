# Messaging Logic for a Micro-Randomized Trial

The micro-randomized trial explores the effect of different messaging types of participant adherence to a digital biomarker study protocol.

This repository contains the logic for randomizing participants at three decision points per day into one of three experimental conditions. According to their previous data provided through MyDataHelps' mobile app, messages are customized and chosen from the respective message bank to send to users.

The code reuses and adapts elements of MyDataHelps' Python API Quickstart (`https://github.com/CareEvolution/mydatahelps-rest-api-python-quickstart`) and the public JITAI Case Study (`https://developer.mydatahelps.org/casestudy/jitai.html`).

## Structure


## Quick Start

1. Populate environment variables in `src/.env`
2. `Run jitai_logic.py`