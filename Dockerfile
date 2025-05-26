# ---- Dockerfile for AWS Lambda Python ----
FROM public.ecr.aws/lambda/python:3.9

WORKDIR /var/task

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY src/ .

CMD ["./src/jitai_logic_applehealth.lambda_handler"]
