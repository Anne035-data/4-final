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
from drift_config import get_drift_config

# Configuration du logging
logging.basicConfig(level=logging.DEBUG)

# Variables Airflow
EVIDENTLY_CLOUD_TOKEN = Variable.get("EVIDENTLY_CLOUD_TOKEN")
EVIDENTLY_CLOUD_PROJECT_ID = Variable.get("EVIDENTLY_CLOUD_PROJECT_ID")
S3_BUCKET = Variable.get("S3_BUCKET")

# Configuration des colonnes secondaires
SECONDARY_COLUMNS = [
    # Liste des autres types de sol
    "Soil_Type1", "Soil_Type2", "Soil_Type3", "Soil_Type5", "Soil_Type6",
    "Soil_Type8", "Soil_Type9", "Soil_Type11", "Soil_Type12", "Soil_Type13",
    "Soil_Type14", "Soil_Type15", "Soil_Type16", "Soil_Type17", "Soil_Type18",
    "Soil_Type19", "Soil_Type21", "Soil_Type23", "Soil_Type24", "Soil_Type25",
    "Soil_Type26", "Soil_Type27", "Soil_Type28", "Soil_Type29", "Soil_Type30",
    "Soil_Type31", "Soil_Type32", "Soil_Type33", "Soil_Type34", "Soil_Type35",
    "Soil_Type36", "Soil_Type37", "Soil_Type38", "Soil_Type39", "Soil_Type40"
]

# # Obtenir la configuration du drift
DRIFT_CONFIG = get_drift_config()
FOREST_COVER_TYPES = DRIFT_CONFIG["FOREST_COVER_TYPES"]

# Configuration des seuils
drift_threshold = DRIFT_CONFIG["THRESHOLDS"]["dataset_drift"]

# Accès S3
REFERENCE_FILE = 'covertype/reference/covtype_80.csv'
NEW_DATA_FILE = 'covertype/new_data/covtype.csv'

# Fonction pour détecter si un nouveau fichier a été ajouté ou modifié
def detect_file(**context):
    """Vérifier si le fichier existe et a été modifié depuis la dernière analyse"""
    try:
        # Vérifier si c'est un déclenchement manuel forcé
        dag_run = context.get('dag_run')
        force_run = False
        
        if dag_run and dag_run.conf:
            force_run = dag_run.conf.get('force_run', False)
            logging.info(f"Force run: {force_run}")
            
            # Si c'est un lancement forcé, ignorer la vérification d'ETag
            if force_run:
                logging.info("Lancement forcé depuis Streamlit: ignorer la vérification ETag")
                context["task_instance"].xcom_push(key="force_run", value=True)
                return "trigger_jenkins_test_task"
        
        s3 = boto3.client('s3')
        
        # Vérifier si le fichier existe
        logging.info(f"Checking file in S3: {S3_BUCKET}/{NEW_DATA_FILE}")
        response = s3.head_object(Bucket=S3_BUCKET, Key=NEW_DATA_FILE)
        current_etag = response['ETag'].strip('"')  # Enlever les guillemets
        
        logging.info(f"File found: {NEW_DATA_FILE}, ETag: {current_etag}")
        context["task_instance"].xcom_push(key="file_etag", value=current_etag)
        
        # Vérifier les logs précédents pour ce DAG spécifique
        run_logs_prefix = 'covertype/secondary_columns_logs/'
        try:
            logs_response = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=run_logs_prefix)
            
            if 'Contents' in logs_response:
                log_files = sorted([obj['Key'] for obj in logs_response['Contents']], reverse=True)
                
                # Vérifier les logs récents 
                for log_file in log_files[:10]:
                    try:
                        log_obj = s3.get_object(Bucket=S3_BUCKET, Key=log_file)
                        log_content = log_obj['Body'].read().decode('utf-8')
                        log_data = json.loads(log_content)
                        
                        # Vérifier si ce fichier a été traité avec le même ETag
                        if (log_data.get('file_processed') == NEW_DATA_FILE and 
                            log_data.get('file_etag') == current_etag and
                            log_data.get('execution_status') != 'no_new_data'):
                            
                            logging.info(f"File already processed with same ETag: {NEW_DATA_FILE}")
                            return "no_file_found_task"
                            
                    except Exception as e:
                        logging.warning(f"Error checking log file {log_file}: {str(e)}")
                        continue
        except Exception as e:
            logging.warning(f"Error checking run logs: {str(e)}")
        
        # le fichier n'a pas été traité ou a été modifié
        logging.info(f"New or modified file found in S3: {NEW_DATA_FILE}")
        return "trigger_jenkins_test_task"
        
    except Exception as e:
        logging.error(f"Error checking S3: {str(e)}")
        return "no_file_found_task"

# Fonction pour déclencher le pipeline de test Jenkins
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
                max_tries = 60  # 10 minutes
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
def save_drift_report_to_s3(drift_results, drift_summary):
    """Enregistrer le rapport de dérive au format JSON dans S3"""
    try:
        s3 = boto3.client('s3')
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        filename = f'covertype/secondary_columns_reports/drift_report_{timestamp}.json'
        
        # Déterminer si un drift a été détecté
        drift_threshold = DRIFT_CONFIG["THRESHOLDS"]["dataset_drift"]
        drift_detected = drift_summary >= drift_threshold
        
        # Ajouter un résumé
        drift_results['drift_summary'] = { 
            'total_drifted_columns': drift_summary,
            'drift_threshold': drift_threshold,
            'drift_detected': drift_detected,
            'file_processed': NEW_DATA_FILE,
            'columns_analyzed': 'secondary',
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

# Fonction pour vérifier la structure des colonnes
def check_column_structure(reference_df, new_data_df):
    """Vérifier les différences de structure des colonnes"""
    reference_columns = set(reference_df.columns)
    new_data_columns = set(new_data_df.columns)
    
    missing_columns = [col for col in SECONDARY_COLUMNS if col not in new_data_columns]
    new_columns = list(new_data_columns - reference_columns)
    
    return {
        "missing_columns": missing_columns,
        "new_columns": new_columns,
        "is_valid_structure": len(missing_columns) == 0
    }

# Fonction pour détecter la dérive des données secondaires
def detect_data_drift(**context):
    """Produire un rapport de dérive des données secondaires avec Evidently Cloud"""
    try:
        # Chargement des données
        logging.info("Loading files from S3...")
        reference, new_data = _load_files()
        logging.info(f"Reference data shape: {reference.shape}")
        logging.info(f"New data shape: {new_data.shape}")

        # Vérifier la structure des colonnes
        columns_check = check_column_structure(reference, new_data)
        logging.info(f"Structure des colonnes: {columns_check}")
        context["task_instance"].xcom_push(key="columns_check", value=columns_check)
        
        # Vérifier si des colonnes essentielles sont manquantes
        if not columns_check["is_valid_structure"]:
            missing_cols = columns_check["missing_columns"]
            logging.error(f"Colonnes secondaires manquantes: {missing_cols}")
            context["task_instance"].xcom_push(key="drift_detected", value=False)
            context["task_instance"].xcom_push(key="drift_summary", value=0)
            return "no_drift_detected_task"
        
        # Initialiser la connexion au workspace Evidently Cloud
        ws = CloudWorkspace(
            token=EVIDENTLY_CLOUD_TOKEN,
            url="https://app.evidently.cloud"
        )
        project = ws.get_project(EVIDENTLY_CLOUD_PROJECT_ID)

        # Filtrer les données pour n'utiliser que les colonnes secondaires existantes
        analysis_columns = [col for col in SECONDARY_COLUMNS if col in new_data.columns]
        
        if "Cover_Type" not in analysis_columns:
            analysis_columns.append("Cover_Type")  # Ajouter la variable cible
        
        reference_filtered = reference[analysis_columns]
        new_data_filtered = new_data[analysis_columns]

        # Création du rapport de dérive
        data_drift_report = Report(metrics=[
            DataDriftPreset(
                stattest_threshold=DRIFT_CONFIG["THRESHOLDS"]["default"]["stattest_threshold"]
            )
        ])
        
        data_drift_report.run(current_data=new_data_filtered, reference_data=reference_filtered)
        drift_results = data_drift_report.as_dict()

        # Envoyer le rapport à Evidently Cloud avec le tag "secondary_columns"
        ws.add_report(project.id, data_drift_report, include_data=True)
        logging.info("Rapport envoyé à Evidently Cloud.")

        # Extraire le nombre de colonnes dérivées
        dataset_drift_metric = next(
            (metric["result"] for metric in drift_results["metrics"] if metric["metric"] == "DatasetDriftMetric"),
            None
        )
        if not dataset_drift_metric:
            raise ValueError("Métrique 'DatasetDriftMetric' introuvable dans le rapport.")

        data_drift_summary = dataset_drift_metric.get("number_of_drifted_columns", 0)
        context["task_instance"].xcom_push(key="drift_summary", value=data_drift_summary)
        logging.info(f"Nombre de colonnes secondaires dérivées: {data_drift_summary}")

        # Sauvegarder le rapport dans S3
        save_drift_report_to_s3(drift_results, data_drift_summary)

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

        # Décision basée sur la dérive détectée
        drift_threshold = DRIFT_CONFIG["THRESHOLDS"]["dataset_drift"]
        if data_drift_summary >= drift_threshold:
            logging.info(f"Dérive détectée dans {data_drift_summary} colonnes secondaires, seuil: {drift_threshold}")
            context["task_instance"].xcom_push(key="drift_detected", value=True)
            return "prepare_email_drift_task"
        else:
            logging.info(f"Dérive limitée: {data_drift_summary} colonnes secondaires, sous le seuil: {drift_threshold}")
            context["task_instance"].xcom_push(key="drift_detected", value=False)
            return "no_drift_detected_task"
    except Exception as e:
        logging.error(f"Erreur dans detect_data_drift: {str(e)}")
        raise

# Fonction pour logger les métadonnées du run
def log_run_metadata(**context):
    """Enregistrer les métadonnées du run d'analyse des colonnes secondaires"""
    try:
        ti = context['ti']
        
        # Vérifier si c'est un lancement forcé
        force_run = ti.xcom_pull(key='force_run', task_ids='detect_file_task', default=False)
        if isinstance(force_run, (list, tuple)) and len(force_run) > 0:
            force_run = force_run[0]  # Convertir LazyXComSelectSequence en valeur simple
        
        # Déterminer le statut de l'exécution
        execution_status = None
        sample_size = 0
        file_etag = None  # Initialiser à None par défaut
        
        # Vérifier la branche exécutée
        no_file_task_state = ti.xcom_pull(key='return_value', task_ids='detect_file_task')
        if isinstance(no_file_task_state, (list, tuple)) and len(no_file_task_state) > 0:
            no_file_task_state = no_file_task_state[0]  # Convertir LazyXComSelectSequence
            
        if no_file_task_state == "no_file_found_task":
            execution_status = "no_new_data"
            drift_detected = False
            drift_summary = 0
            file_processed = "Aucun fichier"
            column_status = None
        else:
            # Si une analyse a été effectuée, alors récupérer l'ETag
            file_etag = ti.xcom_pull(key='file_etag', task_ids='detect_file_task')
            if isinstance(file_etag, (list, tuple)) and len(file_etag) > 0:
                file_etag = file_etag[0]  # Convertir LazyXComSelectSequence
            
            # Vérifier le résultat du test Jenkins
            test_result = ti.xcom_pull(key='test_result', task_ids=['trigger_jenkins_test_task'])
            if isinstance(test_result, (list, tuple)) and len(test_result) > 0:
                test_result = test_result[0]  # Convertir LazyXComSelectSequence
            
            # Vérifier le statut du drift
            drift_data_task_state = ti.xcom_pull(key='return_value', task_ids='detect_data_drift_task')
            if isinstance(drift_data_task_state, (list, tuple)) and len(drift_data_task_state) > 0:
                drift_data_task_state = drift_data_task_state[0]  # Convertir LazyXComSelectSequence
                
            if drift_data_task_state == "no_drift_detected_task":
                execution_status = "no_drift_detected"
                drift_detected = False
            else:
                execution_status = "drift_detected"
                drift_detected = True
            
            # Récupérer les détails
            drift_summary = ti.xcom_pull(key='drift_summary', task_ids='detect_data_drift_task', default=0)
            if isinstance(drift_summary, (list, tuple)) and len(drift_summary) > 0:
                drift_summary = drift_summary[0]  # Convertir LazyXComSelectSequence
                
            file_processed = NEW_DATA_FILE
            
            column_status = ti.xcom_pull(key='columns_check', task_ids='detect_data_drift_task', default=None)
            if isinstance(column_status, (list, tuple)) and len(column_status) > 0:
                column_status = column_status[0]  # Convertir LazyXComSelectSequence
            
            # Récupérer la taille de l'échantillon uniquement si un fichier a été traité
            try:
                _, new_data = _load_files()
                sample_size = len(new_data)
                logging.info(f"Taille de l'échantillon: {sample_size} lignes")
            except Exception as e:
                logging.warning(f"Impossible de récupérer la taille de l'échantillon: {str(e)}")

        # Préparer métadonnées
        run_metadata = {
            'timestamp': datetime.now().isoformat(),
            'execution_status': execution_status,
            'drift_detected': drift_detected,
            'drift_summary': drift_summary,
            'file_processed': file_processed,
            'drift_threshold': DRIFT_CONFIG["THRESHOLDS"]["dataset_drift"],
            'sample_size': sample_size,
            'analysis_type': 'secondary_columns',
            'force_run': force_run,
            'trigger_source': 'streamlit' if force_run else 'scheduler'
        }
        
        # Ajouter les résultats du test Jenkins s'ils existent
        if test_result:
            run_metadata['test_result'] = test_result
        
        # N'ajouter l'ETag que s'il existe
        if file_etag:
            run_metadata['file_etag'] = file_etag
            
        # La clé column_status peut contenir des objets complexes
        if column_status:
            try:
                
                if isinstance(column_status, dict):
                    simplified_status = {}
                    for k, v in column_status.items():
                        if isinstance(v, (list, tuple)):
                            simplified_status[k] = list(v)  # Convertir en liste simple
                        else:
                            simplified_status[k] = v
                    run_metadata['column_status'] = simplified_status
                else:
                    logging.warning(f"Format de column_status non pris en charge, omis de la sérialisation")
            except Exception as e:
                logging.warning(f"Erreur lors de la simplification de column_status: {str(e)}")
                run_metadata['column_status'] = str(column_status)  # Fallback en string
        
        # Vérifier que tout est sérialisable avant le log
        try:
            json_metadata = json.dumps(run_metadata, indent=2)
            logging.info(f"Métadonnées préparées: {json_metadata}")
        except TypeError as e:
            logging.warning(f"Erreur de sérialisation JSON: {str(e)}, nettoyage supplémentaire nécessaire")
            clean_metadata = {}
            for k, v in run_metadata.items():
                try:
                    # Tester chaque valeur individuellement
                    json.dumps({k: v})
                    clean_metadata[k] = v
                except TypeError:
                    clean_metadata[k] = str(v) 
            run_metadata = clean_metadata
            logging.info(f"Métadonnées nettoyées: {json.dumps(run_metadata, indent=2)}")
        
        # Enregistrer dans S3
        s3 = boto3.client('s3')
        filename = f'covertype/secondary_columns_logs/run_{datetime.now().strftime("%Y%m%d%H%M%S")}.json'
        
        s3.put_object(
            Bucket=S3_BUCKET, 
            Key=filename, 
            Body=json.dumps(run_metadata, indent=4).encode('utf-8'),
            ContentType='application/json'
        )
        
        logging.info(f"Métadonnées enregistrées: {filename}")
        
    except Exception as e:
        logging.error(f"Erreur dans log_run_metadata: {str(e)}")
        import traceback
        logging.error(f"Traceback: {traceback.format_exc()}")
        raise

# Fonctions pour les emails
def prepare_email_drift_content(**context):
    drift_summary = context['ti'].xcom_pull(key='drift_summary', task_ids='detect_data_drift_task')
    drift_threshold = DRIFT_CONFIG["THRESHOLDS"]["dataset_drift"]
    
    # Vérifier le résultat du test Jenkins
    test_result = context['ti'].xcom_pull(key='test_result', task_ids=['trigger_jenkins_test_task'])
    
    subject = "Drift détecté dans les colonnes secondaires"
    body = f"""
    Un drift a été détecté dans {drift_summary} colonne(s) secondaire(s).
    Le seuil de déclenchement configuré est de {drift_threshold} colonnes.
    
    Ces colonnes secondaires ne sont pas utilisées directement par le modèle principal, 
    mais cette dérive pourrait indiquer un changement dans la distribution des données source.
    
    Une analyse plus approfondie est recommandée pour évaluer l'impact potentiel sur le modèle.
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

def prepare_email_no_drift_content(**context):
    drift_summary = context['ti'].xcom_pull(key='drift_summary', task_ids='detect_data_drift_task')
    drift_threshold = DRIFT_CONFIG["THRESHOLDS"]["dataset_drift"]
    
    # Vérifier le résultat du test Jenkins
    test_result = context['ti'].xcom_pull(key='test_result', task_ids=['trigger_jenkins_test_task'])
    
    subject = "Pas de drift significatif dans les colonnes secondaires"
    body = f"""
    Un drift limité a été détecté dans {drift_summary} colonnes secondaires des données Forest Cover Type.
    Ce niveau est sous le seuil de déclenchement configuré de {drift_threshold} colonnes.
    
    La distribution des colonnes secondaires reste relativement stable.
    
    Aucune action n'est requise pour le moment.
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

# Fonction pour envoyer l'email
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
        return f"Email envoyé à {to_email} avec succès!"
    except Exception as e:
        error_message = f"Erreur lors de l'envoi de l'e-mail: {str(e)}"
        logging.error(error_message)
        raise Exception(error_message)

def send_email_drift(**context):
    ti = context['ti']
    subject = ti.xcom_pull(key='email_subject', task_ids='prepare_email_drift_task')
    body = ti.xcom_pull(key='email_body', task_ids='prepare_email_drift_task')
    context['ti'].xcom_push(key='email_subject', value=subject)
    context['ti'].xcom_push(key='email_body', value=body)
    send_email_with_smtp(**context)

# Paramètres par défaut du DAG
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
    'secondary_columns_drift_analysis',
    default_args=default_args,
    description='Analyse de dérive des colonnes secondaires du dataset Forest Cover Type',
    schedule_interval='0 0 1 * *',  # Tous les 1er du mois à minuit (mensuel)
    catchup=False,
)

# Définition des tâches
detect_file_task = BranchPythonOperator(
    task_id='detect_file_task',
    python_callable=detect_file,
    provide_context=True,
    dag=dag,
)

trigger_jenkins_test_task = BranchPythonOperator(
    task_id='trigger_jenkins_test_task',
    python_callable=trigger_jenkins_test,
    provide_context=True,
    dag=dag,
)

detect_data_drift_task = BranchPythonOperator(
    task_id='detect_data_drift_task',
    python_callable=detect_data_drift,
    provide_context=True,
    dag=dag,
)

prepare_email_drift_task = PythonOperator(
    task_id='prepare_email_drift_task',
    python_callable=prepare_email_drift_content,
    provide_context=True,
    dag=dag,
)

send_email_drift_task = PythonOperator(
    task_id='send_email_drift_task',
    python_callable=send_email_drift,
    provide_context=True,
    dag=dag,
)

prepare_email_no_drift_task = PythonOperator(
    task_id='prepare_email_no_drift_task',
    python_callable=prepare_email_no_drift_content,
    provide_context=True,
    dag=dag,
)

send_email_no_drift_task = PythonOperator(
    task_id='send_email_no_drift_task',
    python_callable=send_email_with_smtp,
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

log_run_metadata_task = PythonOperator(
    task_id='log_run_metadata_task',
    python_callable=log_run_metadata,
    provide_context=True,
    trigger_rule='none_failed_min_one_success',
    dag=dag,
)

# Définition du flux
detect_file_task >> [trigger_jenkins_test_task, no_file_found_task]
trigger_jenkins_test_task >> detect_data_drift_task
detect_data_drift_task >> [prepare_email_drift_task, no_drift_detected_task]
prepare_email_drift_task >> send_email_drift_task
no_drift_detected_task >> prepare_email_no_drift_task >> send_email_no_drift_task

# Branches vers la tâche finale de log
send_email_drift_task >> log_run_metadata_task
send_email_no_drift_task >> log_run_metadata_task
no_file_found_task >> log_run_metadata_task