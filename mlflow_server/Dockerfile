FROM continuumio/miniconda3

WORKDIR /home/app

RUN apt-get update && apt-get install -y postgresql-client nano unzip curl

RUN curl -fsSL https://get.deta.dev/cli.sh | sh
RUN curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip" && \
    unzip awscliv2.zip && \
    ./aws/install

COPY requirements.txt /dependencies/requirements.txt
RUN pip install -r /dependencies/requirements.txt
RUN pip install mlflow==2.15.0 --force-reinstall

ENV AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID
ENV AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY
ENV DB_STORE_URI=$DB_STORE_URI
ENV ARTIFACT_STORE_URI=$ARTIFACT_STORE_URI

CMD mlflow server -p $PORT --host 0.0.0.0 --backend-store-uri $DB_STORE_URI --default-artifact-root $ARTIFACT_STORE_URI

