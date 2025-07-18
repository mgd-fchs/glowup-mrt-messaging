# Messaging Logic for a Micro-Randomized Trials / JITAIs

The micro-randomized trial explores the effect of different messaging types of participant adherence to a digital biomarker study protocol.

This repository contains the logic for randomizing participants at three decision points per day into one of three experimental conditions. According to their previous data provided through MyDataHelps' mobile app, messages are customized and chosen from the respective message bank to send to users.

The code reuses and adapts elements of MyDataHelps' Python API Quickstart (`https://github.com/CareEvolution/mydatahelps-rest-api-python-quickstart`) and the public JITAI Case Study (`https://developer.mydatahelps.org/casestudy/jitai.html`).

## Structure
```
mrt-messaging/
│
├── lambda_handler.py               # Entry point (decision logic Lambda)
├── dispatch_handler.py             # (Optional) Separate dispatch Lambda
├── config.yaml                     # User config: API keys, base URL, context variables, etc.
│
├── context/                        # 🔌 Custom context logic lives here
│   ├── __init__.py
│   ├── apple_health.py             # e.g. implements get_steps(), get_sleep()
│   ├── fitbit.py
│   ├── google_fit.py
│   └── custom_api.py               # For user-defined sources like “snores_per_minute”
│
├── context_registry.py            # Dynamically loads functions from context/ based on config
│
├── scheduler/
│   ├── notification_scheduler.py   # Logic to store scheduled messages (e.g., in S3/DynamoDB)
│   └── sync_reminder_scheduler.py  # Optional: schedules sync reminder messages
│
├── notifications/
│   ├── sender.py                   # Calls notification API (e.g., MyDataHelps)
│   └── message_templates.py        # Optional templating / text generation
│
├── utils/
│   ├── api_utils.py                # Common utilities (e.g., get access token)
│   ├── jitai_utils.py              # Decision logic helpers, randomization, eligibility
│   └── logger.py                   # Logging / audit trail
│
└── README.md
```

## Quick Start

1. Populate environment variables in `src/.env`
2. `Run jitai_logic.py`