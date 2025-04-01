from airflow import DAG
from airflow.operators.python import PythonOperator, BranchPythonOperator
from airflow.operators.dummy import DummyOperator
from airflow.models import Variable
import smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta
import pandas as pd
from evidently.report import Report
from evidently.metric_preset import DataDriftPreset
from evidently.ui.workspace.cloud import CloudWorkspace
import logging
import requests
import boto3
import json
import os
import time

# Import drift configuration
from drift_config import get_drift_config

# Configuration du logging
logging.basicConfig(level=logging.DEBUG)

# Variables Airflow
EVIDENTLY_CLOUD_TOKEN = Variable.get("EVIDENTLY_CLOUD_TOKEN") 
EVIDENTLY_CLOUD_PROJECT_ID = Variable.get("EVIDENTLY_CLOUD_PROJECT_ID")
S3_BUCKET = Variable.get("S3_BUCKET")

# Get the drift configuration
DRIFT_CONFIG = get_drift_config()
COLUMNS_TO_ANALYZE = DRIFT_CONFIG["COLUMNS_TO_ANALYZE"]
FOREST_COVER_TYPES = DRIFT_CONFIG["FOREST_COVER_TYPES"]

# accès S3
REFERENCE_FILE = 'covertype/reference/covtype_80.csv'
# NEW_DATA_FILE = 'covertype/new_data/covtype_20.csv'
# fichier à utiliser pour test drift
NEW_DATA_FILE = 'covertype/new_data/covtype.csv'

# fonction de détection de fichier
def detect_file(**context):
    """Vérifier si le fichier existe et a été modifié depuis la dernière analyse en utilisant l'ETag"""
    try:
        s3 = boto3.client('s3')
        
        # Vérifier si le fichier existe
        logging.info(f"Checking file in S3: {S3_BUCKET}/{NEW_DATA_FILE}")
        response = s3.head_object(Bucket=S3_BUCKET, Key=NEW_DATA_FILE)
        current_etag = response['ETag'].strip('"')  
        
        logging.info(f"File found: {NEW_DATA_FILE}, ETag: {current_etag}")
        
        # Stocker l'ETag actuel dans XCom pour l'utiliser plus tard
        context["task_instance"].xcom_push(key="file_etag", value=current_etag)
        
        # Lister les fichiers de logs récents
        run_logs_prefix = 'covertype/run_logs/'
        try:
            # Récupérer la liste des fichiers de logs
            logs_response = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=run_logs_prefix)
            
            if 'Contents' in logs_response:
                log_files = sorted([obj['Key'] for obj in logs_response['Contents']], reverse=True)
                
                # Vérifier les logs récents pour voir si ce fichier a déjà été traité
                for log_file in log_files[:10]:  
                    try:
                        log_obj = s3.get_object(Bucket=S3_BUCKET, Key=log_file)
                        log_content = log_obj['Body'].read().decode('utf-8')
                        try:
                            log_data = json.loads(log_content)
                            
                            # Vérifier si le fichier a déjà été traité
                            if log_data.get('file_processed') == NEW_DATA_FILE and log_data.get('execution_status') != 'no_new_data':
                                # Vérifier si l'ETag du fichier traité est stocké dans le log
                                if 'file_etag' in log_data:
                                    last_processed_etag = log_data['file_etag']
                                    logging.info(f"ETag of last processed file: {last_processed_etag}")
                                    
                                    # Si l'ETag n'a pas changé, le fichier n'a pas été modifié
                                    if current_etag == last_processed_etag:
                                        logging.info(f"File already processed with the same ETag: {NEW_DATA_FILE}")
                                        return "no_file_found_task"
                                    else:
                                        logging.info(f"File has been modified since last processing (ETag changed)")
                                        break
                                else:
                                    # Si l'ETag n'est pas stocké dans le log, on  cherche un log plus ancien
                                    logging.info("ETag not found in the log, checking next log...")
                                    continue
                        except json.JSONDecodeError:
                            logging.warning(f"Invalid JSON format in log {log_file}")
                            continue
                    except Exception as e:
                        logging.warning(f"Error reading log file {log_file}: {str(e)}")
                        continue
        
        except Exception as e:
            logging.warning(f"Error checking run logs: {str(e)}")
        
        
        logging.info(f"New or modified file found in S3: {NEW_DATA_FILE}")
        return "trigger_jenkins_test_task"
        
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
    
# Fonction pour sauvegarder le rapport de dérive dans S3
def save_drift_report_to_s3(drift_results, drift_summary, **context):
    """Enregistrer le rapport de dérive au format JSON dans S3"""
    try:
        s3 = boto3.client('s3')
        
        # Récupérer le dossier des rapports des paramètres de l'exécution
        dag_run = context.get('dag_run')
        reports_folder = 'covertype/model_columns_reports/'  
        
        if dag_run and dag_run.conf:
            reports_folder = dag_run.conf.get('reports_folder', reports_folder)
        
        # Générer un nom de fichier unique
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        filename = f'{reports_folder}drift_report_{timestamp}.json'
        
        # Déterminer si un drift a été détecté en fonction du seuil configuré
        drift_threshold = DRIFT_CONFIG["THRESHOLDS"]["dataset_drift"]
        drift_detected = drift_summary >= drift_threshold
        
        # Ajouter un résumé
        drift_results['drift_summary'] = { 
            'total_drifted_columns': drift_summary,
            'drift_threshold': drift_threshold,
            'drift_detected': drift_detected,
            'file_processed': NEW_DATA_FILE,
            'timestamp': datetime.now().isoformat()
        }
        
        # Convertir en JSON
        json_report = json.dumps(drift_results, indent=4)
        
        # Envoyer à S3
        s3.put_object(
            Bucket=S3_BUCKET, 
            Key=filename, 
            Body=json_report.encode('utf-8'), 
            ContentType='application/json'
        )
        
        logging.info(f"Rapport de dérive enregistré dans S3: {filename}")
        logging.info(f"Résumé du drift: {drift_detected=}, {drift_summary=} colonnes, seuil={drift_threshold}")
        
        return filename
    except Exception as e:
        logging.error(f"Erreur lors de l'enregistrement du rapport de dérive : {str(e)}")
        return None
    

# Fonction pour vérifier les différences de structure des colonnes
def check_column_structure(reference_df, new_data_df):
    """Vérifier les différences de structure des colonnes entre les données de référence et les nouvelles données"""
    reference_columns = set(reference_df.columns)
    new_data_columns = set(new_data_df.columns)
    
    missing_columns = [col for col in COLUMNS_TO_ANALYZE if col not in new_data_columns]
    new_columns = list(new_data_columns - reference_columns)
    
    return {
        "missing_columns": missing_columns,
        "new_columns": new_columns,
        "is_valid_structure": len(missing_columns) == 0  
    }

# Fonction pour détecter la dérive des données
def detect_data_drift(**context):
    """Produire un rapport de dérive des données avec Evidently Cloud"""
    try:
        # Chargement des données depuis S3
        logging.info("Loading files from S3...")
        reference, new_data = _load_files()
        logging.info(f"Reference data shape: {reference.shape}")
        logging.info(f"New data shape: {new_data.shape}")

        # Vérifier la structure des colonnes
        columns_check = check_column_structure(reference, new_data)
        logging.info(f"Structure des colonnes: {columns_check}")
        
        # Enregistrer la structure des colonnes dans XCom pour le récupérer plus tard
        context["task_instance"].xcom_push(key="columns_check", value=columns_check)
        
        # Vérifier si des colonnes essentielles sont manquantes
        if not columns_check["is_valid_structure"]:
            missing_cols = columns_check["missing_columns"]
            logging.error(f"Colonnes essentielles manquantes: {missing_cols}")
            context["task_instance"].xcom_push(key="drift_detected", value=False)
            context["task_instance"].xcom_push(key="drift_summary", value=0)
            return "no_drift_detected_task"
        
        # Initialiser la connexion au workspace Evidently Cloud
        ws = CloudWorkspace(
            token=EVIDENTLY_CLOUD_TOKEN,
            url="https://app.evidently.cloud"
        )

        project = ws.get_project(EVIDENTLY_CLOUD_PROJECT_ID)

        # Filtrer les données pour n'utiliser que les colonnes existantes à analyser
        analysis_columns = [col for col in COLUMNS_TO_ANALYZE if col in new_data.columns]
        
        reference_filtered = reference[analysis_columns]
        new_data_filtered = new_data[analysis_columns]

        # Create drift report with thresholds from config
        data_drift_report = Report(metrics=[
            DataDriftPreset(
            stattest_threshold=DRIFT_CONFIG["THRESHOLDS"]["default"]["stattest_threshold"]
        )
        ])
        
        logging.debug("Rapport de dérive créé.")

        data_drift_report.run(current_data=new_data_filtered, reference_data=reference_filtered)
        logging.debug("Rapport de dérive exécuté avec succès.")

        # Convertir le rapport en dictionnaire
        drift_results = data_drift_report.as_dict()

        # Envoyer le rapport à Evidently Cloud
        ws.add_report(project.id, data_drift_report, include_data=True)
        logging.info("Rapport envoyé à Evidently Cloud.")

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

        # Sauvegarder le rapport dans S3
        save_drift_report_to_s3(drift_results, data_drift_summary, **context)

        # Analyse de la distribution des types de forêts
        reference_forest_types = reference['Cover_Type'].value_counts(normalize=True)
        new_data_forest_types = new_data['Cover_Type'].value_counts(normalize=True)
        
        logging.info("Distribution des types de forêts :")
        logging.info("Référence : " + 
            str({FOREST_COVER_TYPES.get(k, k): f"{v*100:.2f}%" for k, v in reference_forest_types.items()})
        )
        logging.info("Nouvelles données : " + 
            str({FOREST_COVER_TYPES.get(k, k): f"{v*100:.2f}%" for k, v in new_data_forest_types.items()})
        )

        # Décision basée sur la dérive détectée et le seuil de la configuration
        drift_threshold = DRIFT_CONFIG["THRESHOLDS"]["dataset_drift"]
        if data_drift_summary >= drift_threshold:
            logging.info(f"Dérive détectée dans {data_drift_summary} colonnes, seuil de déclenchement: {drift_threshold}.")
            context["task_instance"].xcom_push(key="drift_detected", value=True)
            return "trigger_jenkins_retrain_task"
        else:
            logging.info(f"Dérive détectée dans {data_drift_summary} colonnes, sous le seuil de {drift_threshold}.")
            context["task_instance"].xcom_push(key="drift_detected", value=False)
            return "no_drift_detected_task"
    except Exception as e:
        logging.error(f"Erreur dans detect_data_drift: {str(e)}")
        raise


# Fonction pour détecter la dérive des données
def trigger_jenkins_test(**context):
    """Déclenche le pipeline de test Jenkins et détermine la prochaine tâche"""
    jenkins_url = "http://jenkins:8080"
    job_name = "test"
    
    try:
        # Déclencher le job Jenkins
        response = requests.post(
            f"{jenkins_url}/job/{job_name}/build",
            auth=(Variable.get("JENKINS_USER"), Variable.get("JENKINS_TOKEN"))
        )
        
        if response.status_code == 201:
            logging.info("Pipeline de test Jenkins déclenché avec succès")
            
            # Attendre que le job commence
            time.sleep(10)
            
            # Récupérer le numéro du build
            status_response = requests.get(
                f"{jenkins_url}/job/{job_name}/lastBuild/api/json",
                auth=(Variable.get("JENKINS_USER"), Variable.get("JENKINS_TOKEN"))
            )
            
            if status_response.status_code == 200:
                build_info = status_response.json()
                build_number = build_info.get("number")
                logging.info(f"Build #{build_number} démarré")
                
                # Attendre la fin du build avec timeout
                max_tries = 60  
                for attempt in range(max_tries):
                    try:
                        status_response = requests.get(
                            f"{jenkins_url}/job/{job_name}/{build_number}/api/json",
                            auth=(Variable.get("JENKINS_USER"), Variable.get("JENKINS_TOKEN"))
                        )
                        
                        if status_response.status_code == 200:
                            build_info = status_response.json()
                            
                            if build_info.get("building", True):
                                logging.info(f"Build #{build_number} en cours... (tentative {attempt+1})")
                                time.sleep(10)
                                continue
                            
                            result = build_info.get("result")
                            logging.info(f"Build #{build_number} terminé avec résultat: {result}")
                            
                            if result == "SUCCESS":
                                logging.info("Les tests ont réussi, passage à l'analyse de drift")
                                return "detect_data_drift_task"
                            else:
                                logging.error(f"Tests échoués avec statut: {result}, passage à l'analyse de drift quand même")
                                context['ti'].xcom_push(key='test_result', value='FAILURE')
                                return "detect_data_drift_task"  
                    except Exception as e:
                        logging.warning(f"Erreur lors de la vérification du build #{build_number}: {str(e)}")
                        time.sleep(10)
                
                # Si timeout
                logging.error(f"Timeout en attendant les résultats du build #{build_number}, passage à l'analyse de drift")
                return "detect_data_drift_task"
            else:
                logging.error(f"Impossible d'obtenir le numéro du build: {status_response.status_code}")
                return "detect_data_drift_task"  
        else:
            logging.error(f"Échec du déclenchement de Jenkins: {response.status_code}")
            return "detect_data_drift_task"  
    except Exception as e:
        logging.error(f"Erreur lors du déclenchement de Jenkins: {str(e)}")
        return "detect_data_drift_task" 

# Fonction pour déclencher le retraining via Jenkins
def trigger_jenkins_retrain(**context):
    """Déclenche le retraining via Jenkins"""
    jenkins_url = "http://jenkins:8080"
    job_name = "retrain"

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

# Fonction pour logger les runs
def log_run_metadata(**context):
    """Enregistrer les métadonnées de chaque run avec débogage approfondi"""
    import traceback
    
    try:
        # Identifier quelle branche a été exécutée
        ti = context['ti']
        dag_run = context['dag_run']
        
        # Initialiser le dictionnaire run_metadata avant de l'utiliser
        run_metadata = {}
        
        # Déterminer le statut de l'exécution
        execution_status = None
        sample_size = 0  
        file_etag = None  
        drift_detected = False  
        drift_summary = 0  
        file_processed = "Aucun fichier"  
        column_status = None  
        
        # Récupérer l'ETag si disponible
        file_etag = ti.xcom_pull(key='file_etag', task_ids='detect_file_task')

        # Vérifier le résultat du test Jenkins
        test_result = ti.xcom_pull(key='test_result', task_ids=['trigger_jenkins_test_task'])
        if test_result == 'FAILURE':
            run_metadata['test_result'] = 'FAILURE'
        else:
            run_metadata['test_result'] = 'SUCCESS'
        
        # Vérifier si no_file_found_task a été exécuté
        no_file_task_state = ti.xcom_pull(key='return_value', task_ids='detect_file_task')
        if no_file_task_state == "no_file_found_task":
            execution_status = "no_new_data"
            drift_detected = False
            drift_summary = 0
            file_processed = "Aucun fichier"
            column_status = None
        else:
            # Vérifier si drift détecté ou non
            drift_data_task_state = ti.xcom_pull(key='return_value', task_ids='detect_data_drift_task')
            if drift_data_task_state == "no_drift_detected_task":
                execution_status = "no_drift_detected"
                drift_detected = False
            elif drift_data_task_state == "trigger_jenkins_retrain_task":
                execution_status = "drift_detected"
                drift_detected = True
            
            # Récupérer les détails du drift
            drift_summary = ti.xcom_pull(key='drift_summary', task_ids='detect_data_drift_task', default=0)
            file_processed = NEW_DATA_FILE
            
            # Récupérer les informations sur les colonnes
            column_status = ti.xcom_pull(key='columns_check', task_ids='detect_data_drift_task', default=None)
        
            # Récupérer la taille de l'échantillon
            try:
                _, new_data = _load_files()
                sample_size = len(new_data)
                logging.info(f"Taille de l'échantillon récupérée: {sample_size} lignes")
            except Exception as e:
                logging.warning(f"Impossible de récupérer la taille de l'échantillon: {str(e)}")

        # Récupérer le dossier des logs des paramètres de l'exécution
        logs_folder = 'covertype/run_logs/'  
        if dag_run and dag_run.conf:
            logs_folder = dag_run.conf.get('logs_folder', logs_folder)

        # Préparer un dictionnaire de métadonnées complet
        run_metadata.update({
            'timestamp': datetime.now().isoformat(),
            'execution_status': execution_status,
            'drift_detected': drift_detected,
            'drift_summary': drift_summary,
            'file_processed': file_processed,
            'drift_threshold': DRIFT_CONFIG["THRESHOLDS"]["dataset_drift"],
            'column_status': column_status,
            'sample_size': sample_size,
            'file_etag': file_etag 
        })
        
        # Configuration S3
        s3 = boto3.client('s3')
        filename = f'{logs_folder}run_{datetime.now().strftime("%Y%m%d%H%M%S")}.json'
        
        # Journalisation détaillée
        logging.info(f"Status d'exécution: {execution_status}")
        logging.info(f"ETag du fichier: {file_etag}")
        logging.info(f"Metadata du run: {json.dumps(run_metadata, indent=4)}")
        
        # Envoi du fichier
        s3.put_object(
            Bucket=S3_BUCKET, 
            Key=filename, 
            Body=json.dumps(run_metadata, indent=4).encode('utf-8'),
            ContentType='application/json'
        )
        
        logging.info(f"Run metadata successfully logged: {filename}")
        
    except Exception as e:
        logging.error(f"Unexpected error in log_run_metadata: {str(e)}")
        logging.error(f"Full traceback: {traceback.format_exc()}")
        raise
    
# Fonctions pour preparer le contenu des e-mails
def prepare_email_drift_content(**context):
    drift_summary = context['ti'].xcom_pull(key='drift_summary', task_ids='detect_data_drift_task')
    drift_threshold = DRIFT_CONFIG["THRESHOLDS"]["dataset_drift"]

    # Vérifier si c'est la première analyse pour ce fichier
    file_etag = context['ti'].xcom_pull(key='file_etag', task_ids='detect_file_task')
    test_result = context['ti'].xcom_pull(key='test_result', task_ids=['trigger_jenkins_test_task'])
    
    subject = "Drift détecté - Retraining nécessaire"
    body = f"""
    Un drift a été détecté dans {drift_summary} colonnes des données de type de couvert forestier.
    Le seuil de déclenchement configuré est de {drift_threshold} colonnes.
    
    Types de forêts détectés :
    - Un changement significatif dans la distribution des types de forêts a été observé.
    
    Action: Lancement d'un retraining du modèle via Jenkins.
    
    """
    
    if test_result:
        body += f"""
        
        RÉSULTATS DES TESTS:
        
        Si des problèmes ont été détectés:
        - Vérifiez l'intégrité des données (valeurs manquantes, aberrantes)
        - Consultez les rapports complets dans S3: covertype/test_reports/
        
        Pour une analyse détaillée, consultez le dashboard Streamlit ou Evidently Cloud.
        """
    
    body += """
    
    Rapport de drift disponible dans S3 et Evidently Cloud.
    """
    
    context['ti'].xcom_push(key='email_subject', value=subject)
    context['ti'].xcom_push(key='email_body', value=body)

def prepare_email_no_drift_content(**context):
    drift_summary = context['ti'].xcom_pull(key='drift_summary', task_ids='detect_data_drift_task')
    drift_threshold = DRIFT_CONFIG["THRESHOLDS"]["dataset_drift"]
    
    # Vérifier si c'est la première analyse pour ce fichier
    file_etag = context['ti'].xcom_pull(key='file_etag', task_ids='detect_file_task')
    test_result = context['ti'].xcom_pull(key='test_result', task_ids=['trigger_jenkins_test_task'])
    
    subject = "Aucun drift significatif détecté"
    body = f"""
    Un drift limité a été détecté dans {drift_summary} colonnes des données de type de couvert forestier.
    Ce niveau est sous le seuil de déclenchement configuré de {drift_threshold} colonnes.
    
    La distribution des types de forêts reste relativement stable.
    
    Aucune action de retraining n'est requise pour le moment.
    """
    
    # Ajouter détails des tests si disponibles
    if test_result:
        body += f"""
        
        RÉSULTATS DES TESTS:
        
        Si des problèmes ont été détectés:
        - Vérifiez l'intégrité des données (valeurs manquantes, aberrantes)
        - Consultez les rapports complets dans S3: covertype/test_reports/
        
        Pour une analyse détaillée, consultez le dashboard Streamlit ou Evidently Cloud.
        """
    
    body += """
    
    Rapport de drift disponible dans S3 et Evidently Cloud.
    """
    
    context['ti'].xcom_push(key='email_subject', value=subject)
    context['ti'].xcom_push(key='email_body', value=body)

# Fonction pour configurer l'envoi d'un email avec SMTP
def send_email_with_smtp(**context):
    ti = context['ti']

    to_email = "anneformation035@gmail.com"
    smtp_server = "smtp.gmail.com"
    smtp_port = 587
    smtp_user = "anneformation035@gmail.com"

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
        logging.error(error_message)
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
    schedule_interval='30 7 * * 1', 
    catchup=False,
)

# Définition des tâches

# Nouvelle tâche pour déclencher le pipeline de test Jenkins
trigger_jenkins_test_task = BranchPythonOperator(
    task_id='trigger_jenkins_test_task',
    python_callable=trigger_jenkins_test,
    provide_context=True,
    dag=dag,
)

#join_task = DummyOperator(
#    task_id='join_branches',
#    trigger_rule='one_success',
#    dag=dag,
# )

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

trigger_jenkins_retrain_task = PythonOperator(
    task_id='trigger_jenkins_retrain_task',
    python_callable=trigger_jenkins_retrain,
    provide_context=True,
    dag=dag,
)

log_run_metadata_task = PythonOperator(
    task_id='log_run_metadata_task',
    python_callable=log_run_metadata,
    provide_context=True,
    trigger_rule='all_done',
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



# Définition des branches et des flux
detect_file_task >> [trigger_jenkins_test_task, no_file_found_task]
trigger_jenkins_test_task >> detect_data_drift_task  # Simplification: toujours continuer vers la détection de drift
detect_data_drift_task >> [trigger_jenkins_retrain_task, no_drift_detected_task]
trigger_jenkins_retrain_task >> prepare_email_drift_task >> send_email_drift_task
no_drift_detected_task >> prepare_email_no_drift_task >> send_email_no_drift_task

# Fusion des branches pour terminer avec le log
[no_file_found_task, send_email_drift_task, send_email_no_drift_task] >> log_run_metadata_task