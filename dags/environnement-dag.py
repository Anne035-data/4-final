from airflow import DAG
from airflow.operators.python import PythonOperator, BranchPythonOperator
from airflow.operators.dummy import DummyOperator
from airflow.models import Variable
from datetime import datetime, timedelta
import logging
import requests
import json
import os

# Configuration du logging
logging.basicConfig(level=logging.INFO)

# Variables Airflow
JENKINS_URL = "http://jenkins:8080"
PIPELINE_NAME = "environnement"  

# Fonction pour déclencher le pipeline Jenkins "environnement"
def trigger_jenkins_pipeline(**context):
    """Déclenche uniquement le pipeline Jenkins 'environnement'"""
    
    logging.info(f"Déclenchement du pipeline Jenkins: {PIPELINE_NAME}")
    
    # Utilisation des identifiants stockés dans les variables Airflow
    jenkins_user = Variable.get("JENKINS_USER", default_var=None)
    jenkins_token = Variable.get("JENKINS_TOKEN", default_var=None)
    
    if not jenkins_user or not jenkins_token:
        logging.error("Identifiants Jenkins non définis")
        context['task_instance'].xcom_push(key='jenkins_status', value='error')
        return "failure_task"
    
    # URL de construction du job
    job_url = f"{JENKINS_URL}/job/{PIPELINE_NAME}/build"
    
    try:
        # Appel de l'API Jenkins
        response = requests.post(
            job_url,
            auth=(jenkins_user, jenkins_token)
        )
        
        if response.status_code in [200, 201, 302]:
            logging.info(f"Pipeline {PIPELINE_NAME} déclenché avec succès")
            context['task_instance'].xcom_push(key='jenkins_status', value='success')
            return "success_task"
        else:
            logging.error(f"Échec du déclenchement du pipeline {PIPELINE_NAME}: {response.status_code}")
            context['task_instance'].xcom_push(key='jenkins_status', value='failed')
            return "failure_task"
            
    except Exception as e:
        logging.error(f"Exception lors du déclenchement: {str(e)}")
        context['task_instance'].xcom_push(key='jenkins_status', value='error')
        return "failure_task"

# Fonction pour enregistrer les résultats
def log_jenkins_call(**context):
    """Enregistre les informations sur l'appel à Jenkins"""
    
    ti = context['ti']
    
    # Récupération des données
    status = ti.xcom_pull(key='jenkins_status', task_ids='trigger_jenkins_task', default='unknown')
    
    # Préparation des métadonnées
    metadata = {
        'timestamp': datetime.now().isoformat(),
        'pipeline_name': PIPELINE_NAME,
        'status': status,
        'dag_id': context['dag'].dag_id,
        'run_id': context['run_id']
    }
    
    # Journalisation
    logging.info(f"Résultat de l'appel Jenkins: {json.dumps(metadata)}")
    
    return metadata

# Arguments par défaut pour le DAG
default_args = {
    'owner': 'RL',
    'start_date': datetime(2024, 12, 12),
    'email_on_failure': True,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=2),
}

# Définition du DAG
dag = DAG(
    'jenkins_pipeline_trigger',
    default_args=default_args,
    description='Déclenche le pipeline Jenkins environnement et enregistre le résultat',
    schedule_interval=None,
    catchup=False,
)

# Tâche pour déclencher le pipeline Jenkins
trigger_jenkins_task = BranchPythonOperator(
    task_id='trigger_jenkins_task',
    python_callable=trigger_jenkins_pipeline,
    provide_context=True,
    dag=dag,
)

# Tâches pour les résultats (succès/échec)
success_task = DummyOperator(
    task_id='success_task',
    dag=dag,
)

failure_task = DummyOperator(
    task_id='failure_task',
    dag=dag,
)

# Tâche pour enregistrer les résultats
log_task = PythonOperator(
    task_id='log_task',
    python_callable=log_jenkins_call,
    provide_context=True,
    trigger_rule='one_success',
    dag=dag,
)

# Définition du flux
trigger_jenkins_task >> [success_task, failure_task] >> log_task