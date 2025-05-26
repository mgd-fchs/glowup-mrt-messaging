FROM public.ecr.aws/lambda/python:3.9

WORKDIR /var/task

COPY requirements.txt .
RUN pip uninstall jwt PyJWT
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD "jitai_logic_applehealth.lambda_handler"
