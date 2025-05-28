FROM public.ecr.aws/lambda/python:3.9

ARG BASE_URL
ARG RKS_PRIVATE_KEY
ARG RKS_PROJECT_ID
ARG RKS_SERVICE_ACCOUNT

ENV BASE_URL=$BASE_URL
ENV RKS_PRIVATE_KEY=$RKS_PRIVATE_KEY
ENV RKS_PROJECT_ID=$RKS_PROJECT_ID
ENV RKS_SERVICE_ACCOUNT=$RKS_SERVICE_ACCOUNT

WORKDIR /var/task

COPY requirements.txt .
RUN pip uninstall jwt PyJWT
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD "jitai_logic.lambda_handler"
