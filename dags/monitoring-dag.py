from airflow import DAG
from airflow.operators.python import PythonOperator, BranchPythonOperator
from airflow.operators.email import EmailOperator
from airflow.operators.dummy import DummyOperator
from airflow.models import Variable
import smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta
import os
import glob
import pandas as pd
from evidently.report import Report
from evidently.metric_preset import DataDriftPreset
from evidently.ui.workspace.cloud import CloudWorkspace
import logging
import requests
import boto3

# Configuration du logging
logging.basicConfig(level=logging.DEBUG)

# Variables Airflow
EVIDENTLY_CLOUD_TOKEN = Variable.get("EVIDENTLY_CLOUD_TOKEN") 
EVIDENTLY_CLOUD_PROJECT_ID = Variable.get("EVIDENTLY_CLOUD_PROJECT_ID")
S3_BUCKET = Variable.get("S3_BUCKET")

# accès S3
REFERENCE_FILE = 'covertype/reference/covtype_80.csv'
# NEW_DATA_FILE = 'covertype/new_data/covtype_20.csv'
# fichier à utiliser pour test drift
NEW_DATA_FILE = 'covertype/new_data/covtype_sample_drift.csv'

# Colonnes à analyser (gardez les mêmes que dans le notebook / entrainement du model)
COLUMNS_TO_ANALYZE = [
    "Elevation", "Aspect", "Slope", "Horizontal_Distance_To_Hydrology",
    "Vertical_Distance_To_Hydrology", "Horizontal_Distance_To_Roadways",
    "Hillshade_9am", "Hillshade_Noon", "Hillshade_3pm",
    "Horizontal_Distance_To_Fire_Points"
]

# fonction de détection de fichier
def detect_file(**context):
    """Vérifier si le fichier existe sur S3"""
    try:
        s3 = boto3.client('s3')
        logging.info(f"Checking file in S3: {S3_BUCKET}/{NEW_DATA_FILE}")
        s3.head_object(Bucket=S3_BUCKET, Key=NEW_DATA_FILE)
        logging.info("File found in S3")
        return "detect_data_drift_task"
    except Exception as e:
        logging.error(f"Error checking S3: {str(e)}")
        return "no_file_found_task"

# Fonction pour charger les fichiers depuis S3
def _load_files():
    """Charger les fichiers depuis S3"""
    try:
        logging.info("Starting to load files from S3...")
        s3 = boto3.client('s3')

        # Charger le fichier de référence
        logging.info(f"Loading reference file: {S3_BUCKET}/{REFERENCE_FILE}")
        ref_obj = s3.get_object(Bucket=S3_BUCKET, Key=REFERENCE_FILE)
        reference = pd.read_csv(ref_obj['Body'])
        logging.info(f"Reference file loaded, shape: {reference.shape}")

        # Charger le nouveau fichier
        logging.info(f"Loading new data file: {S3_BUCKET}/{NEW_DATA_FILE}")
        new_obj = s3.get_object(Bucket=S3_BUCKET, Key=NEW_DATA_FILE)
        new_data = pd.read_csv(new_obj['Body'])
        logging.info(f"New data file loaded, shape: {new_data.shape}")

        return reference, new_data
    except Exception as e:
        logging.error(f"Error loading files from S3: {str(e)}")
        raise

# Fonction pour détecter la dérive des données
def detect_data_drift(**context):
    """Produire un rapport de dérive des données avec Evidently Cloud"""
    try:
        # Chargement des données depuis S3
        logging.info("Loading files from S3...")
        reference, new_data = _load_files()
        logging.info(f"Reference data shape: {reference.shape}")
        logging.info(f"New data shape: {new_data.shape}")

        # Initialiser la connexion au workspace Evidently Cloud
        ws = CloudWorkspace(
            token=EVIDENTLY_CLOUD_TOKEN,
            url="https://app.evidently.cloud"
        )

        project = ws.get_project(EVIDENTLY_CLOUD_PROJECT_ID)

        # Validation des colonnes
        assert all(col in reference.columns for col in COLUMNS_TO_ANALYZE), "Colonnes manquantes dans les données de référence"
        assert all(col in new_data.columns for col in COLUMNS_TO_ANALYZE), "Colonnes manquantes dans les logs"

        reference_filtered = reference[COLUMNS_TO_ANALYZE]
        new_data_filtered = new_data[COLUMNS_TO_ANALYZE]

        data_drift_report = Report(metrics=[DataDriftPreset()])
        logging.debug("Rapport de dérive créé.")

        data_drift_report.run(current_data=new_data_filtered, reference_data=reference_filtered)
        logging.debug("Rapport de dérive exécuté avec succès.")

        ws.add_report(project.id, data_drift_report, include_data=True)
        logging.info("Rapport envoyé à Evidently Cloud.")

        # Log des métriques
        drift_results = data_drift_report.as_dict()
        logging.info(f"Résultats détaillés de la dérive : {drift_results}")

        # Rechercher la métrique DatasetDriftMetric
        dataset_drift_metric = next(
            (metric["result"] for metric in drift_results["metrics"] if metric["metric"] == "DatasetDriftMetric"),
            None
        )

        if not dataset_drift_metric:
            raise ValueError("Métrique 'DatasetDriftMetric' introuvable dans le rapport.")

        # Extraire le nombre de colonnes dérivées
        data_drift_summary = dataset_drift_metric.get("number_of_drifted_columns", 0)
        context["task_instance"].xcom_push(key="drift_summary", value=data_drift_summary)
        logging.info(f"Nombre de colonnes dérivées détectées : {data_drift_summary}")

        # Décision basée sur la dérive détectée
        if data_drift_summary > 0:
            logging.info(f"Dérive détectée dans {data_drift_summary} colonnes.")
            context["task_instance"].xcom_push(key="drift_detected", value=True)
            return "trigger_jenkins_task"
        else:
            logging.info("Aucune dérive détectée.")
            context["task_instance"].xcom_push(key="drift_detected", value=False)
            return "no_drift_detected_task"
    except Exception as e:
        logging.error(f"Erreur dans detect_data_drift: {str(e)}")
        raise

# Fonction pour déclencher le retraining via Jenkins
def trigger_jenkins_retrain(**context):
    """Déclenche le retraining via Jenkins"""
    jenkins_url = "http://jenkins:8080"
    job_name = "covertype_retrain"

    try:
        response = requests.post(
            f"{jenkins_url}/job/{job_name}/build",
            auth=(Variable.get("JENKINS_USER"), Variable.get("JENKINS_TOKEN"))
        )
        if response.status_code == 201:
            logging.info("Jenkins pipeline triggered successfully")
        else:
            logging.error(f"Failed to trigger Jenkins: {response.status_code}")
            raise Exception("Failed to trigger Jenkins pipeline")
    except Exception as e:
        logging.error(f"Error triggering Jenkins: {e}")
        raise

# Fonctions pour preparer le contenu des e-mails avec drift
def prepare_email_drift_content(**context):
    subject = "Drift détecté - Retraining lancé"
    body = "Un drift a été détecté dans les données et le retraining a été lancé."
    
    context['ti'].xcom_push(key='email_subject', value=subject)
    context['ti'].xcom_push(key='email_body', value=body)

# Fonctions pour preparer le contenu des e-mails avec non drift
def prepare_email_no_drift_content(**context):
    subject = "Pas de drift détecté"
    body = "Aucun drift n'a été détecté dans les données."
    
    context['ti'].xcom_push(key='email_subject', value=subject)
    context['ti'].xcom_push(key='email_body', value=body) 

# Fonction pour confugurer l'envoie d'un email avec SMTP
def send_email_with_smtp(**context):
    ti = context['ti']

    to_email = "dsgattaca@gmail.com"
    smtp_server = "smtp.gmail.com"
    smtp_port = 587
    smtp_user = "dsgattaca@gmail.com"

    smtp_password = Variable.get("gmail_password", default_var=None)
    if smtp_password is None:
        raise ValueError("Le mot de passe Gmail n'est pas défini dans les variables Airflow.")

    subject = ti.xcom_pull(key='email_subject')
    body = ti.xcom_pull(key='email_body')

    msg = EmailMessage()
    msg.set_content(body)
    msg['Subject'] = subject
    msg['From'] = smtp_user
    msg['To'] = to_email

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
        return f"Email envoyé à {to_email} avec succès !"
    except Exception as e:
        error_message = f"Erreur lors de l'envoi de l'e-mail : {str(e)}"
        print(error_message)
        raise Exception(error_message)

# Fonction pour envoyer un e-mail avec drift
def send_email_drift(**context):
    ti = context['ti']
    subject = ti.xcom_pull(key='email_subject', task_ids='prepare_email_drift_task')
    body = ti.xcom_pull(key='email_body', task_ids='prepare_email_drift_task')
    context['ti'].xcom_push(key='email_subject', value=subject)
    context['ti'].xcom_push(key='email_body', value=body)
    send_email_with_smtp(**context)

# Fonction pour envoyer un e-mail sans drift
def send_email_no_drift(**context):
    ti = context['ti']
    subject = ti.xcom_pull(key='email_subject', task_ids='prepare_email_no_drift_task')
    body = ti.xcom_pull(key='email_body', task_ids='prepare_email_no_drift_task')
    context['ti'].xcom_push(key='email_subject', value=subject)
    context['ti'].xcom_push(key='email_body', value=body)
    send_email_with_smtp(**context)

# Arguments par défaut pour le DAG
default_args = {
    'owner': 'RL',
    'start_date': datetime(2024, 12, 12),
    'email_on_failure': True,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

# Définition du DAG
dag = DAG(
    'detect_data_drift_notify_retrain',
    default_args=default_args,
    description='Détecte la dérive des données, rentraine et envoie une notification par email',
    schedule_interval=None,
    catchup=False,
)

# Définition des tâches
detect_file_task = BranchPythonOperator(
    task_id='detect_file_task',
    python_callable=detect_file,
    provide_context=True,
    dag=dag,
)

detect_data_drift_task = BranchPythonOperator(
    task_id='detect_data_drift_task',
    python_callable=detect_data_drift,
    provide_context=True,
    dag=dag,
)

trigger_jenkins_task = PythonOperator(
    task_id='trigger_jenkins_task',
    python_callable=trigger_jenkins_retrain,
    provide_context=True,
    dag=dag,
)

prepare_email_drift_task = PythonOperator(
    task_id='prepare_email_drift_task',
    python_callable=prepare_email_drift_content,
    provide_context=True,
    dag=dag,
)

prepare_email_no_drift_task = PythonOperator(
    task_id='prepare_email_no_drift_task',
    python_callable=prepare_email_no_drift_content,
    provide_context=True,
    dag=dag,
)

send_email_drift_task = PythonOperator(
    task_id='send_email_drift_task',
    python_callable=send_email_drift,
    provide_context=True,
    dag=dag,
)

send_email_no_drift_task = PythonOperator(
    task_id='send_email_no_drift_task',
    python_callable=send_email_no_drift,
    provide_context=True,
    dag=dag,
)

no_file_found_task = DummyOperator(
    task_id='no_file_found_task',
    dag=dag,
)

no_drift_detected_task = DummyOperator(
    task_id='no_drift_detected_task',
    dag=dag,
)

# Définition du flux
detect_file_task >> [detect_data_drift_task, no_file_found_task]
detect_data_drift_task >> [trigger_jenkins_task, no_drift_detected_task]

# Branche avec drift
trigger_jenkins_task >> prepare_email_drift_task >> send_email_drift_task

# Branche sans drift
no_drift_detected_task >> prepare_email_no_drift_task >> send_email_no_drift_task