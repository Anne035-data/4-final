import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
import os
import sys
import json
from dotenv import load_dotenv
from pathlib import Path
import mlflow
import requests
import boto3
import time

# Configuration initiale
st.set_page_config(layout="wide")

# Chargement des variables d'environnement
parent_dir = Path(__file__).parent.parent
load_dotenv(parent_dir / '.env')
load_dotenv(parent_dir / '.secrets')
S3_BUCKET = os.environ.get('S3_BUCKET')

# Import de la configuration
sys.path.append(str(parent_dir / 'dags'))
from drift_config import get_drift_config
DRIFT_CONFIG = get_drift_config()
FOREST_COVER_TYPES = DRIFT_CONFIG["FOREST_COVER_TYPES"]

# Fonctions de chargement S3
def load_s3_json(bucket, key):
    """Charger un fichier JSON depuis S3"""
    try:
        # Vérifier si le chemin est un dossier
        if key.endswith('/'):
            return None
        s3 = boto3.client(
            's3',
            aws_access_key_id=os.environ['AWS_ACCESS_KEY_ID'],
            aws_secret_access_key=os.environ['AWS_SECRET_ACCESS_KEY']
        )
        obj = s3.get_object(Bucket=bucket, Key=key)
        return json.loads(obj['Body'].read().decode('utf-8'))
    except Exception as e:
        st.error(f"Erreur lors du chargement du fichier JSON {key}: {str(e)}")
        return None

def list_s3_files(bucket, prefix):
    """Lister les fichiers dans un dossier S3"""
    try:
        s3 = boto3.client(
            's3',
            aws_access_key_id=os.environ['AWS_ACCESS_KEY_ID'],
            aws_secret_access_key=os.environ['AWS_SECRET_ACCESS_KEY']
        )
        response = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
        if 'Contents' not in response:
            return []
        return sorted([obj['Key'] for obj in response['Contents']])
    except Exception as e:
        st.error(f"Erreur lors de la liste des fichiers S3 {prefix}: {str(e)}")
        return []
    
def load_test_results(file_key):
    """Charge les résultats des tests pour un fichier spécifique"""
    try:
        # Chercher le rapport de test le plus récent pour ce fichier
        s3 = boto3.client(
            's3',
            aws_access_key_id=os.environ['AWS_ACCESS_KEY_ID'],
            aws_secret_access_key=os.environ['AWS_SECRET_ACCESS_KEY']
        )
        test_reports_prefix = 'covertype/test_reports/'
        
        # Lister tous les rapports de test
        response = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=test_reports_prefix)
        
        if 'Contents' not in response:
            return {
                'missing_values': None,
                'statistical_distribution': None,
                'column_structure': None
            }
            
        # Trier par date (du plus récent au plus ancien)
        reports = sorted([obj['Key'] for obj in response['Contents']], reverse=True)
        
        # Essayer de charger le rapport le plus récent
        for report_key in reports[:5]:  # Vérifier les 5 derniers rapports
            try:
                report_obj = s3.get_object(Bucket=S3_BUCKET, Key=report_key)
                df_report = pd.read_csv(report_obj['Body'])
                
                # Extraire les résultats spécifiques
                results = {}
                for _, row in df_report.iterrows():
                    test_name = row['test_name']
                    status = row['status']
                    description = row.get('description', '')
                    results[test_name] = {'status': status, 'description': description}
                
                return results
            except Exception as e:
                continue
                
        return {
            'missing_values': None,
            'statistical_distribution': None,
            'column_structure': None
        }
        
    except Exception as e:
        st.warning(f"Erreur lors du chargement des résultats de test: {str(e)}")
        return {
            'missing_values': None,
            'statistical_distribution': None,
            'column_structure': None
        }

def load_run_logs(max_logs=30):
    """Charger les logs des runs récents (analyses principales et secondaires)"""
    try:
        # Charger les logs des analyses principales (nouveau chemin)
        run_files = list_s3_files(S3_BUCKET, 'covertype/model_columns_logs/')
        run_files = sorted(run_files, reverse=True)[:max_logs]
        
        # Charger les logs des analyses secondaires
        secondary_files = list_s3_files(S3_BUCKET, 'covertype/secondary_columns_logs/')
        secondary_files = sorted(secondary_files, reverse=True)[:max_logs]
        
        # Combiner les deux types de logs
        all_files = run_files + secondary_files
        
        run_logs = []
        for file in all_files:
            log_data = load_s3_json(S3_BUCKET, file)
            if log_data:
                log_data['filename'] = file
                log_data['timestamp'] = datetime.fromisoformat(log_data['timestamp'])
                
                # Marquer les logs selon leur origine
                if 'secondary_columns_logs' in file:
                    log_data['analysis_type'] = 'secondary_columns'
                elif 'model_columns_logs' in file:
                    log_data['analysis_type'] = 'model_columns'
                
                run_logs.append(log_data)
        
        # Trier tous les logs par date, du plus récent au plus ancien
        run_logs_df = pd.DataFrame(run_logs)
        if not run_logs_df.empty:
            run_logs_df = run_logs_df.sort_values(by='timestamp', ascending=False)
        
        return run_logs_df
        
    except Exception as e:
        st.error(f"Erreur lors du chargement des logs de run : {str(e)}")
        return pd.DataFrame()

def trigger_airflow_dag():
    airflow_url = "http://airflow-webserver:8080/api/v1/dags/detect_data_drift_notify_retrain/dagRuns"
    
    # Paramètres d'authentification Airflow
    auth = (os.environ.get("AIRFLOW_USERNAME"), os.environ.get("AIRFLOW_PASSWORD"))
    
    # Corps de la requête pour déclencher le DAG avec les nouveaux chemins
    payload = {
        "conf": {
            "reports_folder": "covertype/model_columns_reports/",
            "logs_folder": "covertype/model_columns_logs/"
        }
    }
    
    try:
        response = requests.post(airflow_url, auth=auth, json=payload)
        if response.status_code == 200:
            st.success("DAG lancé avec succès!")
            return True
        else:
            st.error(f"Erreur lors du lancement du DAG: {response.status_code}")
            st.write(response.text)
            return False
    except Exception as e:
        st.error(f"Exception: {str(e)}")
        return False

def check_new_file_and_trigger_dag(force=False):
    """Vérifier si le fichier covtype.csv a été modifié depuis la dernière analyse en utilisant l'ETag"""
    import logging
    
    s3 = boto3.client(
        's3',
        aws_access_key_id=os.environ['AWS_ACCESS_KEY_ID'],
        aws_secret_access_key=os.environ['AWS_SECRET_ACCESS_KEY']
    )
    
    # Chemin du fichier à vérifier
    file_key = 'covertype/new_data/covtype.csv'
    
    try:
        # Vérifier si le fichier existe
        response = s3.head_object(Bucket=S3_BUCKET, Key=file_key)
        current_etag = response['ETag'].strip('"')
        
        # Si on force l'analyse, traiter comme un nouveau fichier
        if force:
            return trigger_airflow_dag()
            
        # Vérifier les logs récents (nouveau chemin)
        file_needs_processing = True
        run_logs_prefix = 'covertype/model_columns_logs/'  # Chemin modifié
        
        try:
            # Récupérer la liste des fichiers de logs
            logs_response = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=run_logs_prefix)
            
            if 'Contents' in logs_response:
                log_files = sorted([obj['Key'] for obj in logs_response['Contents']], reverse=True)
                
                # Parcourir les 10 derniers logs (pour la performance)
                for log_file in log_files[:10]:
                    try:
                        log_obj = s3.get_object(Bucket=S3_BUCKET, Key=log_file)
                        log_content = log_obj['Body'].read().decode('utf-8')
                        try:
                            log_data = json.loads(log_content)
                            
                            # Vérifier si le fichier a déjà été traité
                            if log_data.get('file_processed') == file_key and log_data.get('execution_status') != 'no_new_data':
                                # Vérifier si l'ETag du fichier traité est stocké dans le log
                                if 'file_etag' in log_data:
                                    last_processed_etag = log_data['file_etag']
                                    
                                    # Si l'ETag n'a pas changé, le fichier n'a pas été modifié
                                    if current_etag == last_processed_etag:
                                        st.warning("⚠️ Fichier déjà traité et non modifié depuis la dernière analyse")
                                        file_needs_processing = False
                                        return False
                                    else:
                                        st.success("Le fichier a été modifié depuis le dernier traitement")
                                        break
                                else:
                                    continue
                        except json.JSONDecodeError:
                            continue
                    except Exception:
                        continue
                        
        except Exception:
            pass
        
        # Si le fichier a besoin d'être traité
        if file_needs_processing:
            return trigger_airflow_dag()
        
        return False
    
    except Exception as e:
        st.warning(f"⚠️ Fichier non trouvé: {str(e)}")
        return False

def check_new_file_and_trigger_secondary_dag(force=False):
    """Vérifier si le fichier covtype.csv a été modifié depuis la dernière analyse secondaire"""
    
    s3 = boto3.client(
        's3',
        aws_access_key_id=os.environ['AWS_ACCESS_KEY_ID'],
        aws_secret_access_key=os.environ['AWS_SECRET_ACCESS_KEY']
    )
    
    # Chemin du fichier à vérifier (même fichier que pour l'analyse principale)
    file_key = 'covertype/new_data/covtype.csv'
    
    try:
        # Vérifier si le fichier existe
        response = s3.head_object(Bucket=S3_BUCKET, Key=file_key)
        current_etag = response['ETag'].strip('"')
        
        # Si on force l'analyse, ignorer la vérification d'ETag
        if force:
            airflow_url = "http://airflow-webserver:8080/api/v1/dags/secondary_columns_drift_analysis/dagRuns"
            auth = (os.environ.get("AIRFLOW_USERNAME"), os.environ.get("AIRFLOW_PASSWORD"))
            payload = {"conf": {"force_run": True}}
            
            response = requests.post(airflow_url, auth=auth, json=payload)
            if response.status_code == 200:
                st.success("✅ Analyse des données secondaires lancée avec succès!")
                time.sleep(1)
                return True
            else:
                st.error(f"Erreur lors du lancement de l'analyse secondaire: {response.status_code}")
                return False
            
        # Vérifier les logs récents
        file_needs_processing = True
        run_logs_prefix = 'covertype/secondary_columns_logs/'
        
        try:
            # Récupérer la liste des fichiers de logs
            logs_response = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=run_logs_prefix)
            
            if 'Contents' in logs_response:
                log_files = sorted([obj['Key'] for obj in logs_response['Contents']], reverse=True)
                
                # Parcourir les 10 derniers logs
                for log_file in log_files[:10]:
                    try:
                        log_obj = s3.get_object(Bucket=S3_BUCKET, Key=log_file)
                        log_content = log_obj['Body'].read().decode('utf-8')
                        try:
                            log_data = json.loads(log_content)
                            
                            # Vérifier si le fichier a déjà été traité
                            if log_data.get('file_processed') == file_key and log_data.get('execution_status') != 'no_new_data':
                                # Vérifier si l'ETag du fichier traité est stocké dans le log
                                if 'file_etag' in log_data:
                                    last_processed_etag = log_data['file_etag']
                                    
                                    # Si l'ETag n'a pas changé, le fichier n'a pas été modifié
                                    if current_etag == last_processed_etag:
                                        st.warning("⚠️ Fichier déjà traité par l'analyse secondaire et non modifié depuis")
                                        file_needs_processing = False
                                        return False
                                    else:
                                        st.success("Le fichier a été modifié depuis la dernière analyse secondaire")
                                        break
                                else:
                                    continue
                        except json.JSONDecodeError:
                            continue
                    except Exception:
                        continue
                        
        except Exception:
            pass
        
        # Si le fichier a besoin d'être traité
        if file_needs_processing:
            airflow_url = "http://airflow-webserver:8080/api/v1/dags/secondary_columns_drift_analysis/dagRuns"
            auth = (os.environ.get("AIRFLOW_USERNAME"), os.environ.get("AIRFLOW_PASSWORD"))
            payload = {"conf": {}}
            
            response = requests.post(airflow_url, auth=auth, json=payload)
            if response.status_code == 200:
                st.success("✅ Analyse des données secondaires lancée avec succès!")
                time.sleep(1)
                return True
            else:
                st.error(f"Erreur lors du lancement de l'analyse secondaire: {response.status_code}")
                return False
        
        return False
    
    except Exception as e:
        st.warning(f"⚠️ Fichier non trouvé pour analyse secondaire: {str(e)}")
        return False

# Fonction utilitaire pour formater le statut des colonnes
def _format_column_status(row):
    """Formate le statut des colonnes pour un affichage simplifié avec indication du type de problème"""
    if row['file_processed'] == "Aucun fichier":
        return "N/A"
    
    # Vérifier si on est passé par le test Jenkins
    test_result = row.get('test_result', None)
    
    # Vérifier la structure des colonnes
    column_status = row.get('column_status', None)
    
    # Si test Jenkins en échec, déterminer le type d'échec
    if test_result == 'FAILURE':
        # Examiner les détails pour identifier la cause
        if isinstance(column_status, dict):
            missing_columns = column_status.get('missing_columns', [])
            if missing_columns:
                return "❌ Colonnes manquantes"
            else:
                return "❌ Valeurs aberrantes"
        else:
            return "❌ Échec de test"
    
    # Vérifier si la structure des colonnes indique un problème
    if isinstance(column_status, dict):
        missing_columns = column_status.get('missing_columns', [])
        new_columns = column_status.get('new_columns', [])
        
        if missing_columns:
            return "⚠️ Colonnes manquantes"
        elif new_columns:
            return "ℹ️ Nouvelles colonnes"
    
    # Aucun problème détecté
    return "✅ Structure OK"

# Fonction pour formater le statut des tests de valeurs manquantes
def _format_missing_values_status(test_results):
    """Formate le statut du test de valeurs manquantes"""
    if not test_results or 'missing_values' not in test_results:
        return "⚠️ Non testé"
        
    missing_values = test_results.get('missing_values', {})
    if not missing_values:
        return "⚠️ Non testé"
        
    status = missing_values.get('status')
    
    if status == 'PASSED':
        return "✅ Données complètes"
    elif status == 'FAILED':
        return "❌ Données manquantes"
    else:
        return "⚠️ Statut inconnu"

# Fonction pour formater le statut des tests de valeurs aberrantes
def _format_statistical_distribution_status(test_results):
    """Formate le statut du test de distribution statistique"""
    if not test_results or 'statistical_distribution' not in test_results:
        return "⚠️ Non testé"
        
    stat_dist = test_results.get('statistical_distribution', {})
    if not stat_dist:
        return "⚠️ Non testé"
        
    status = stat_dist.get('status')
    description = stat_dist.get('description', '')
    
    if status == 'PASSED':
        return "✅ Pas de données aberrantes"
    elif status == 'FAILED':
        # Extraire les informations sur les colonnes avec des valeurs aberrantes
        columns_with_outliers = []
        if 'Valeurs aberrantes détectées pour' in description:
            try:
                # Extraction des noms de colonnes contenant des valeurs aberrantes
                import re
                columns = re.findall(r'Valeurs aberrantes détectées pour (\w+):', description)
                if columns:
                    columns_with_outliers = columns
            except:
                pass
                
        if columns_with_outliers:
            cols_str = ", ".join(columns_with_outliers)
            return f"❌ Données aberrantes ({cols_str})"
        else:
            return "❌ Données aberrantes"
    else:
        return "⚠️ Statut inconnu"
    
def format_column_status_with_color(row):
    """Formate le statut des colonnes pour un affichage avec point de couleur"""
    if row['file_processed'] == "Aucun fichier":
        return "N/A"
    
    # Vérifier si on est passé par le test Jenkins
    test_result = row.get('test_result', None)
    
    # Vérifier la structure des colonnes
    column_status = row.get('column_status', None)
    
    # Si test Jenkins en échec, déterminer le type d'échec
    if test_result == 'FAILURE':
        # Examiner les détails pour identifier la cause
        if isinstance(column_status, dict):
            missing_columns = column_status.get('missing_columns', [])
            if missing_columns:
                return "🔴 Non"
            else:
                return "🔴 Non (valeurs aberrantes)"
        else:
            return "🔴 Non (échec test)"
    
    # Vérifier si la structure des colonnes indique un problème
    if isinstance(column_status, dict):
        missing_columns = column_status.get('missing_columns', [])
        new_columns = column_status.get('new_columns', [])
        
        if missing_columns:
            return "🟠 Partiellement"
        elif new_columns:
            return "🟢 Oui (+ nouvelles)"
    
    # Aucun problème détecté
    return "🟢 Oui"

def run_dashboard():
    """Dashboard principal de suivi des nouvelles données"""
    st.header("🔄 Suivi des nouvelles données")
    
    # Bouton d'actualisation
    if st.button('↻ Actualiser les données'):
        st.experimental_rerun()
    
    # Boutons pour déclencher les analyses
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔍 Lancer l'analyse des données liées au modèle", 
                    help="Déclenche l'analyse de drift sur les colonnes principales utilisées par le modèle"):
            # Modification: déclenche le DAG principal
            success = check_new_file_and_trigger_dag(force=False)
            if success:
                st.success("✅ Analyse des données principales lancée avec succès!")
                time.sleep(1)
            
    with col2:
        if st.button("🔬 Lancer l'analyse des données secondaires", 
                    help="Déclenche l'analyse des colonnes secondaires non utilisées directement par le modèle"):
            # Utiliser la nouvelle fonction de vérification
            check_new_file_and_trigger_secondary_dag(force=False)
    
    # Séparateur
    st.divider()


    
    # Charger les logs
    run_logs = load_run_logs(max_logs=20)  # Réduire le nombre de logs chargés
    
    # Filtrer les logs par type d'analyse
    if not run_logs.empty:
        # Ajouter une colonne pour identifier le type d'analyse
        run_logs['analysis_type'] = run_logs.apply(
            lambda row: 'secondary' if 'analysis_type' in row and row['analysis_type'] == 'secondary_columns' 
            else 'primary', axis=1
        )
        
        # Séparer les logs par type - Réduire le nombre d'échantillons comme demandé
        main_logs = run_logs[run_logs['analysis_type'] == 'primary'].head(10)  # Réduit à 10
        secondary_logs = run_logs[run_logs['analysis_type'] == 'secondary'].head(5)  # Réduit à 5
        
        # Statistiques pour les analyses principales
        st.subheader("Résultat des 10 dernières analyses liées au modèle")  # Mis à jour
        st.caption("Colonnes concernées: Elevation, Aspect, Slope, Wilderness Areas et Types de sol principaux")
        
        col1, col2, col3 = st.columns(3)
        with col1:
            main_drift = len(main_logs[main_logs['drift_detected'] == True])
            st.metric("Analyses avec drift", main_drift)
        with col2:
            main_no_drift = len(main_logs[main_logs['drift_detected'] == False])
            st.metric("Analyses sans drift", main_no_drift)
        with col3:
            st.metric("Total analyses", len(main_logs))
        
        # Filtres simples pour les logs principaux
        main_col1, main_col2 = st.columns([1, 3])
        with main_col1:
            main_filter = st.selectbox(
                "Filtre", 
                ["Toutes les analyses", "Uniquement avec drift", "Uniquement sans drift"],
                key="main_filter"
            )
        
        # Appliquer le filtre
        filtered_main_logs = main_logs
        if main_filter == "Uniquement avec drift":
            filtered_main_logs = main_logs[main_logs['drift_detected'] == True]
        elif main_filter == "Uniquement sans drift":
            filtered_main_logs = main_logs[main_logs['drift_detected'] == False]
        
        # Visualisation des runs principaux récents
        if not filtered_main_logs.empty:
            styled_main = filtered_main_logs.copy()
    
            # Chargement des résultats de test pour chaque fichier
            styled_main['test_results'] = styled_main['file_processed'].apply(
                lambda x: load_test_results(x) if x != "Aucun fichier" else None
            )
    
            styled_main['drift_detected'] = styled_main['drift_detected'].map(
                {True: '⚠️ Drift', False: '✅ Pas de Drift'}
            )
            styled_main['timestamp'] = styled_main['timestamp'].dt.strftime('%d/%m/%Y %H:%M')


            
            # Modification: formater le statut des colonnes avec des points de couleur
            styled_main['columns_status_color'] = styled_main.apply(
                lambda row: format_column_status_with_color(row), axis=1
            )
            
            # Nouvelles colonnes pour les résultats des tests
            styled_main['exhaustivite_donnees'] = styled_main['test_results'].apply(
                lambda x: _format_missing_values_status(x)
            )
            
            styled_main['valeurs_aberrantes'] = styled_main['test_results'].apply(
                lambda x: _format_statistical_distribution_status(x)
            )
            
            styled_main['sample_size'] = styled_main.get('sample_size', 0)
            styled_main['sample_size'] = styled_main['sample_size'].fillna("N/A")
            # Convertir en entiers et formater avec des séparateurs de milliers si possible
            styled_main['sample_size'] = styled_main['sample_size'].apply(
                lambda x: f"{int(x):,}" if isinstance(x, (int, float)) else x
            )

            # Afficher seulement les colonnes importantes avec les nouvelles colonnes
            display_main_df = styled_main[['timestamp', 'sample_size', 'drift_detected', 'drift_summary',
                                        'columns_status_color', 'exhaustivite_donnees', 'valeurs_aberrantes',
                                        'file_processed']]
            
            # Renommer les colonnes pour un meilleur affichage
            display_main_df.columns = ['Date', 'Taille échantillon', 'Statut Drift', 'Colonnes en drift',
                                    'Colonnes attendues présentes', 'Exhaustivité des données', 'Valeurs aberrantes',
                                    'Fichier Traité']
            
            # Afficher le dataframe
            st.dataframe(display_main_df, use_container_width=True)   

            # Ajouter après l'affichage des tableaux principaux
            st.markdown("""
            ---
            #### Informations sur les tests réalisés

            **Valeurs aberrantes** : Une valeur est considérée comme aberrante si elle se situe à plus de 3 écarts-types de la moyenne de la distribution de référence. 
            Cette définition est utilisée pour chaque colonne numérique du jeu de données.

            > Pour des analyses plus détaillées des valeurs aberrantes, consultez les rapports Evidently complets ou effectuez une nouvelle analyse exploratoire des données (EDA).
            """)
            
            
        else:
            st.info("Aucune analyse principale correspondant au filtre sélectionné")
        
        # Statistiques pour les analyses secondaires
        st.divider()
        st.subheader("Résultat des 5 dernières analyses liées aux colonnes secondaires")  # Mis à jour
        st.caption("Colonnes concernées: Types de sol secondaires et autres caractéristiques non utilisées directement par le modèle")
        
        col1, col2, col3 = st.columns(3)
        with col1:
            secondary_drift = len(secondary_logs[secondary_logs['drift_detected'] == True])
            st.metric("Analyses avec drift", secondary_drift)
        with col2:
            secondary_no_drift = len(secondary_logs[secondary_logs['drift_detected'] == False])
            st.metric("Analyses sans drift", secondary_no_drift)
        with col3:
            st.metric("Total analyses", len(secondary_logs))
        
        # Filtres pour les logs secondaires
        sec_col1, sec_col2 = st.columns([1, 3])
        with sec_col1:
            sec_filter = st.selectbox(
                "Filtre", 
                ["Toutes les analyses", "Uniquement avec drift", "Uniquement sans drift"],
                key="sec_filter"
            )
        
        # Appliquer le filtre
        filtered_sec_logs = secondary_logs
        if sec_filter == "Uniquement avec drift":
            filtered_sec_logs = secondary_logs[secondary_logs['drift_detected'] == True]
        elif sec_filter == "Uniquement sans drift":
            filtered_sec_logs = secondary_logs[secondary_logs['drift_detected'] == False]
        
        # Visualisation des runs secondaires récents
        # Visualisation des runs secondaires récents
        if not filtered_sec_logs.empty:
            styled_sec = filtered_sec_logs.copy()
            
            # Chargement des résultats de test pour chaque fichier
            styled_sec['test_results'] = styled_sec['file_processed'].apply(
                lambda x: load_test_results(x) if x != "Aucun fichier" else None
            )
            
            styled_sec['drift_detected'] = styled_sec['drift_detected'].map(
                {True: '⚠️ Drift', False: '✅ Pas de Drift'}
            )
            styled_sec['timestamp'] = styled_sec['timestamp'].dt.strftime('%d/%m/%Y %H:%M')
            
            # Ajouter la colonne sur le statut des colonnes
            styled_sec['columns_status_color'] = styled_sec.apply(
                lambda row: format_column_status_with_color(row), axis=1
            )
            
            # Nouvelles colonnes pour les résultats des tests
            styled_sec['exhaustivite_donnees'] = styled_sec['test_results'].apply(
                lambda x: _format_missing_values_status(x)
            )
            
            styled_sec['valeurs_aberrantes'] = styled_sec['test_results'].apply(
                lambda x: _format_statistical_distribution_status(x)
            )
            
            styled_sec['sample_size'] = styled_sec.get('sample_size', 0)
            styled_sec['sample_size'] = styled_sec['sample_size'].fillna("N/A")
            # Convertir en entiers et formater avec des séparateurs
            styled_sec['sample_size'] = styled_sec['sample_size'].apply(
                lambda x: f"{int(x):,}" if isinstance(x, (int, float)) else x
            )

            # Afficher seulement les colonnes importantes avec les nouvelles colonnes ajoutées
            display_sec_df = styled_sec[['timestamp', 'sample_size', 'drift_detected', 'drift_summary', 
                                    'columns_status_color', 'exhaustivite_donnees', 'valeurs_aberrantes',
                                    'file_processed']]
            
            # Renommer les colonnes pour un meilleur affichage
            display_sec_df.columns = ['Date', 'Taille échantillon', 'Statut Drift', 'Colonnes en drift',
                                    'Colonnes attendues présentes', 'Exhaustivité des données', 'Valeurs aberrantes',
                                    'Fichier Traité']
            
            # Afficher le dataframe
            st.dataframe(display_sec_df, use_container_width=True)
            
        else:
            st.info("Aucune analyse secondaire correspondant au filtre sélectionné")
    else:
        st.warning("Aucun log d'analyse trouvé")

def display_drift_reports(drift_files):
    """Fonction réutilisable pour afficher les rapports de drift"""
    # Séparer les 5 derniers rapports en boutons cliquables et les rapports plus anciens
    latest_reports = drift_files[:5]
    older_reports = drift_files[5:] if len(drift_files) > 5 else []
    
    # Initialiser avec le rapport le plus récent par défaut
    selected_drift_file = drift_files[0] if drift_files else None
    
    st.markdown("### Derniers rapports disponibles:")

    for i, file in enumerate(latest_reports):
        try:
            timestamp_str = file.split('_')[-1].replace('.json', '')
            report_datetime = datetime.strptime(timestamp_str, "%Y%m%d%H%M%S")
            formatted_date = report_datetime.strftime("%d/%m/%Y à %H:%M")
            
            # Créer un bouton pour chaque rapport avec une clé qui inclut le hash du fichier pour être unique
            button_key = f"{hash(file)}_{i}"
            if st.button(f"{i+1}. Rapport du {formatted_date}", key=button_key):
                selected_drift_file = file
        except:
            # En cas d'erreur de formatage, afficher simplement le nom du fichier
            if st.button(f"{i+1}. {file}", key=f"{file}_btn"):
                selected_drift_file = file
    
    # Menu déroulant pour les anciens
    if not selected_drift_file and older_reports:
        st.divider()
        st.markdown("### Rapports plus anciens:")
        
        # Préparer les options du menu déroulant avec des dates formatées
        dropdown_options = []
        for file in older_reports:
            try:
                timestamp_str = file.split('_')[-1].replace('.json', '')
                report_datetime = datetime.strptime(timestamp_str, "%Y%m%d%H%M%S")
                formatted_date = report_datetime.strftime("%d/%m/%Y à %H:%M")
                dropdown_options.append((file, f"Rapport du {formatted_date}"))
            except:
                dropdown_options.append((file, file))
        
        # Créer la liste des libellés pour le menu déroulant
        dropdown_labels = [option[1] for option in dropdown_options]
        
        # Créer une clé plus unique en utilisant le hash du premier fichier
        dropdown_key = f"old_dropdown_{hash(drift_files[0])}"
        # Afficher le menu déroulant avec clé unique
        selected_label = st.selectbox("Sélectionner un rapport plus ancien", 
                                     options=dropdown_labels, 
                                     key=dropdown_key)
        
        # Retrouver le fichier correspondant au libellé sélectionné
        for file, label in dropdown_options:
            if label == selected_label:
                selected_drift_file = file
                break
    
    # Si un bouton a été cliqué, il remplacera la sélection par défaut
    # Si aucun bouton n'a été cliqué, on garde le rapport le plus récent sélectionné automatiquement
    # Le menu déroulant ne sera proposé que si aucun rapport n'est sélectionné (cas improbable)
    if not selected_drift_file and latest_reports:
        # Préparation des options pour le menu déroulant
        dropdown_options = []
        for file in latest_reports:
            try:
                timestamp_str = file.split('_')[-1].replace('.json', '')
                report_datetime = datetime.strptime(timestamp_str, "%Y%m%d%H%M%S")
                formatted_date = report_datetime.strftime("%d/%m/%Y à %H:%M")
                dropdown_options.append((file, f"Rapport du {formatted_date}"))
            except:
                dropdown_options.append((file, file))
        
        # Créer la liste des libellés pour le menu déroulant
        dropdown_labels = [option[1] for option in dropdown_options]
        
        # Créer une clé plus unique en utilisant le hash du premier fichier et un préfixe différent
        dropdown_key = f"recent_dropdown_{hash(drift_files[0])}"
        # Afficher le menu déroulant avec clé unique
        selected_label = st.selectbox("Sélectionner un rapport de drift", 
                                     options=dropdown_labels,
                                     key=dropdown_key)
        
        # Retrouver le fichier correspondant au libellé sélectionné
        for file, label in dropdown_options:
            if label == selected_label:
                selected_drift_file = file
                break
                
    # Analyser le rapport sélectionné
    if selected_drift_file:
        drift_report = load_s3_json(S3_BUCKET, selected_drift_file)
        
        if drift_report:
            # Extraire la date du rapport à partir du nom du fichier
            report_date = "Date inconnue"
            try:
                timestamp_str = selected_drift_file.split('_')[-1].replace('.json', '')
                report_datetime = datetime.strptime(timestamp_str, "%Y%m%d%H%M%S")
                report_date = report_datetime.strftime("%d/%m/%Y à %H:%M")
            except:
                pass
            
            # Statut global du drift avec la date du rapport
            st.subheader(f"Statut Global du Drift - {report_date}")
            col1, col2 = st.columns(2)
            
            with col1:
                dataset_drift = next(
                    (metric['result'] for metric in drift_report['metrics'] 
                     if metric['metric'] == 'DatasetDriftMetric'),
                    None
                )
                
                if dataset_drift:
                    drift_detected = dataset_drift.get('drift_detected', False)
                    drifted_columns = dataset_drift.get('number_of_drifted_columns', 0)
                    total_columns = dataset_drift.get('number_of_columns', 0)
    
                    # Modification: vérifier aussi le nombre de colonnes en dérive
                    if drift_detected or drifted_columns > 0:
                        st.error(f"⚠️ Drift détecté ({drifted_columns} colonnes)")
                    else:
                        st.success("✅ Pas de drift détecté")
                        
                    # Notification discrète pour indiquer que c'est le rapport le plus récent
                    if selected_drift_file == drift_files[0]:
                        st.caption("📌 Ceci est le rapport le plus récent")
            
            with col2:
                st.metric("Colonnes dérivées", f"{drifted_columns} / {total_columns}")
            
            # Détail des colonnes dérivées
            st.subheader("Colonnes en Dérive")
            
            # Chercher la table de dérive des données qui contient les détails par colonne
            data_drift_table = next(
                (metric["result"] for metric in drift_report["metrics"] 
                 if metric["metric"] == "DataDriftTable"),
                None
            )
            
            if data_drift_table and "drift_by_columns" in data_drift_table:
                # Extraire les informations des colonnes, mais uniquement celles nécessaires
                column_drift_data = []
                
                for col_name, col_data in data_drift_table["drift_by_columns"].items():
                    drift_detected = col_data.get('drift_detected', False)
                    
                    column_drift_data.append({
                        'Colonne': col_name,
                        'Score': round(col_data.get('drift_score', 0), 4),
                        'Seuil': col_data.get('stattest_threshold', 0.05),
                        'Drift': '⚠️ Dérive' if drift_detected else '✅ Stable'
                    })
                
                # Créer un DataFrame simplifié
                drift_details_df = pd.DataFrame(column_drift_data)
                
                # Tri par défaut: Dérive en premier, puis par score
                drift_details_df = drift_details_df.sort_values(
                    by=['Drift', 'Score'], 
                    ascending=[True, False]  # Dérive (⚠️) avant Stable (✅)
                )
                
                # Commencer l'index à 1
                drift_details_df.index = range(1, len(drift_details_df) + 1)
                
                # Afficher le DataFrame simplifié
                st.dataframe(drift_details_df, use_container_width=True)
                
            else:
                st.info("Pas de détails sur les colonnes disponibles dans ce rapport.")
                
def model_tracking():
    """Suivi du modèle"""
    st.header("🤖 Suivi du Modèle")
    
    # Ajouter un lien vers MLFlow sur HuggingFace
    mlflow_huggingface_url = "https://anneformation-mlflow-final-project.hf.space"
    st.markdown(f"""
    🔗 **[Accéder à MLFlow sur HuggingFace]({mlflow_huggingface_url})** pour une analyse complète du suivi des modèles et des expériences.
    """)
    st.divider()
    
    try:
        # Connexion à MLflow
        mlflow_tracking_uri = os.environ.get('MLFLOW_TRACKING_URI', 'https://anneformation-mlflow-final-project.hf.space')
        mlflow.set_tracking_uri(mlflow_tracking_uri)
        client = mlflow.tracking.MlflowClient()
        
        # Récupérer les runs récents
        experiment = client.get_experiment_by_name("forest_cover_type")
        if experiment:
            experiment_id = experiment.experiment_id
            runs = client.search_runs(
                experiment_ids=[experiment_id],
                order_by=["attributes.start_time DESC"],
                max_results=5
            )
            
            if runs:
                st.subheader("Runs récents")
                
                # Préparer les données pour le tableau
                table_data = []
                for i, run in enumerate(runs):
                    run_id = run.info.run_id
                    start_time = datetime.fromtimestamp(run.info.start_time / 1000.0)
                    metrics = run.data.metrics
                    
                    accuracy = metrics.get("accuracy", 0) * 100
                    f1 = metrics.get("f1_score", 0) * 100
                    
                    table_data.append({
                        "Date": start_time.strftime('%d/%m/%Y %H:%M'),
                        "Précision": f"{accuracy:.8f}%",
                        "F1 Score": f"{f1:.8f}%",
                        "Run ID": run_id[:8] + "..." 
                    })
                
                # Créer un DataFrame et l'afficher
                df = pd.DataFrame(table_data)
                
                # Commencer la numérotation à 1 (au lieu de 0)
                df.index = range(1, len(df) + 1)
                
                st.dataframe(df, use_container_width=True)
                
                # graphique de tendance
                if len(runs) > 1:
                    st.subheader("Évolution des performances")
                    
                    # Créer des données pour le graphique
                    # On inverse l'ordre pour avoir les plus récents à droite
                    dates = [datetime.fromtimestamp(run.info.start_time / 1000.0).strftime('%d/%m/%Y %H:%M') for run in runs]
                    dates.reverse()  # Inverser pour avoir les plus récents à droite
                    
                    metrics_data = {
                        "Run": range(1, len(runs) + 1),  # Commencer à 1
                        "Date": dates,
                        "Précision": [run.data.metrics.get("accuracy", 0) * 100 for run in reversed(runs)],
                        "F1 Score": [run.data.metrics.get("f1_score", 0) * 100 for run in reversed(runs)]
                    }
                    metrics_df = pd.DataFrame(metrics_data)
                    
                    # Créer un graphique avec Plotly
                    fig = go.Figure()
                    
                    # ligne Précision
                    fig.add_trace(go.Scatter(
                        x=metrics_df["Run"],
                        y=metrics_df["Précision"],
                        mode='lines+markers',
                        name='Précision',
                        line=dict(color='red', dash='dash', width=4),
                        text=metrics_df["Date"],  # Ajouter les dates comme info au survol
                        hovertemplate='Run %{x}<br>Date: %{text}<br>Précision: %{y:.2f}%'
                    ))
                    
                    # ligne F1 Score
                    fig.add_trace(go.Scatter(
                        x=metrics_df["Run"],
                        y=metrics_df["F1 Score"],
                        mode='lines+markers',
                        name='F1 Score',
                        text=metrics_df["Date"],  # Ajouter les dates comme info au survol
                        hovertemplate='Run %{x}<br>Date: %{text}<br>F1 Score: %{y:.2f}%'
                    ))
                    
                    # Configuration avancée du graphique
                    fig.update_layout(
                        xaxis_title="Run",
                        yaxis_title="Score (%)",
                        xaxis=dict(
                            tickmode='array',
                            tickvals=list(range(1, len(runs) + 1)),  # Seulement les runs entiers
                            ticktext=[f"{i} ({date})" for i, date in zip(range(1, len(runs) + 1), dates)]  # Ajouter les dates aux étiquettes
                        ),
                        yaxis=dict(
                            range=[50, 100],  # Modifier ici pour commencer à 50% au lieu de 0%
                            dtick=10  # Réduire le pas à 10% pour avoir plus de graduations dans la plage visible
                        ),
                        legend=dict(
                            orientation="h",
                            yanchor="bottom",
                            y=1.02,
                            xanchor="right",
                            x=1
                        ),
                        margin=dict(l=20, r=20, t=40, b=20),
                        hovermode="x unified"
                    )
                    
                    st.plotly_chart(fig, use_container_width=True)
            else:
                st.warning("Aucun run trouvé pour l'expérience 'forest_cover_type'")
        else:
            st.error("Expérience 'forest_cover_type' non trouvée")
    
    except Exception as e:
        st.error(f"Erreur de chargement des données : {str(e)}")


def drift_details():
    """Page détaillée des dérives"""
    st.header("🕵️ Analyse des Dérives")
    
    # Afficher un lien vers Evidently Cloud pour des analyses plus détaillées
    evidently_cloud_url = "https://app.evidently.cloud"
    st.markdown(f"""
    📊 **[Accéder à Evidently Cloud]({evidently_cloud_url})** pour des analyses plus détaillées et l'historique complet des drifts.
    """)
    st.divider()
    
    # Onglets pour séparer les deux types d'analyses de drift
    drift_tab1, drift_tab2 = st.tabs(["🔍 Colonnes liées au modèle", "🔬 Colonnes secondaires"])
    
    with drift_tab1:
        # Sélection des rapports de drift pour les colonnes principales
        # Utiliser le nouveau chemin correct pour les rapports de drift des colonnes du modèle
        drift_files = list_s3_files(S3_BUCKET, 'covertype/model_columns_reports/')
        drift_files = [f for f in drift_files if 'drift_report_' in f]
        drift_files.sort(reverse=True)
        
        if not drift_files:
            st.warning("Aucun rapport de drift trouvé pour les colonnes liées au modèle")
        else:
            st.subheader("Rapports de drift pour les colonnes liées au modèle")
            display_drift_reports(drift_files)
    
    with drift_tab2:
        # Sélection des rapports de drift pour les colonnes secondaires
        secondary_drift_files = list_s3_files(S3_BUCKET, 'covertype/secondary_columns_reports/')
        secondary_drift_files = [f for f in secondary_drift_files if 'drift_report_' in f]
        secondary_drift_files.sort(reverse=True)
        
        if not secondary_drift_files:
            st.warning("Aucun rapport de drift trouvé pour les colonnes secondaires")
        else:
            st.subheader("Rapports de drift pour les colonnes secondaires")
            display_drift_reports(secondary_drift_files)


# Fonction pour déclencher le DAG Airflow avec paramètres
def trigger_airflow_dag_with_params_and_wait(dag_id, params=None, max_wait_seconds=60):
    """Déclenche un DAG Airflow et attend sa fin d'exécution"""
    
    airflow_url = "http://airflow-webserver:8080/api/v1/dags"
    
    # Paramètres d'authentification Airflow
    auth = (os.environ.get("AIRFLOW_USERNAME"), os.environ.get("AIRFLOW_PASSWORD"))
    
    # Déclencher le DAG
    dag_url = f"{airflow_url}/{dag_id}/dagRuns"
    payload = {"conf": params or {}}
    
    try:
        # Lancer le DAG
        response = requests.post(dag_url, auth=auth, json=payload)
        
        if response.status_code != 200:
            st.error(f"Erreur lors du lancement du DAG: {response.status_code}")
            st.write(response.text)
            return False
            
        # Récupérer l'ID de l'exécution
        run_response = response.json()
        dag_run_id = run_response.get('dag_run_id')
        
        if not dag_run_id:
            st.error("Impossible de récupérer l'ID d'exécution du DAG")
            return False
            
        # Attendre la fin de l'exécution
        st.info(f"DAG lancé avec succès (ID: {dag_run_id}). Attente de la fin d'exécution...")
        
        # Barre de progression
        progress_bar = st.progress(0)
        
        wait_time = 0
        while wait_time < max_wait_seconds:
            # Vérifier l'état du DAG
            dag_status_url = f"{airflow_url}/{dag_id}/dagRuns/{dag_run_id}"
            status_response = requests.get(dag_status_url, auth=auth)
            
            if status_response.status_code == 200:
                status_data = status_response.json()
                state = status_data.get('state')
                
                if state in ['success', 'failed']:
                    # DAG terminé
                    progress_bar.progress(100)
                    
                    if state == 'success':
                        st.success(f"DAG {dag_id} terminé avec succès!")
                    else:
                        st.error(f"DAG {dag_id} terminé avec erreur!")
                    
                    # Attendre un peu pour s'assurer que les données sont bien enregistrées
                    time.sleep(2)
                    return True
            
            # Mise à jour de la barre de progression
            progress = min(int((wait_time / max_wait_seconds) * 100), 99)
            progress_bar.progress(progress)
            
            # Attendre avant la prochaine vérification
            time.sleep(3)
            wait_time += 3
        
        st.warning(f"Délai d'attente dépassé. Le DAG {dag_id} est peut-être toujours en cours d'exécution.")
        return True
        
    except Exception as e:
        st.error(f"Exception: {str(e)}")
        return False

def check_environment_status():
    """Fonction pour vérifier le statut de l'environnement directement depuis Jenkins"""
    # Configuration
    jenkins_url = os.environ.get("JENKINS_URL", "http://jenkins:8080")
    jenkins_user = os.environ.get("JENKINS_ADMIN_ID")
    jenkins_password = os.environ.get("JENKINS_ADMIN_PASSWORD")
    pipeline_name = "environnement"
    
    try:
        # URL pour l'API Jenkins
        api_url = f"{jenkins_url}/job/{pipeline_name}/lastBuild/api/json"
        
        # Interroger Jenkins directement
        response = requests.get(api_url, auth=(jenkins_user, jenkins_password))
        
        if response.status_code == 200:
            data = response.json()
            status = data.get("result")
            
            # Récupérer des informations supplémentaires
            build_number = data.get("number", "N/A")
            timestamp = data.get("timestamp", 0)
            build_date = datetime.fromtimestamp(timestamp / 1000.0).strftime('%d/%m/%Y %H:%M')
            
            # Récupérer les logs en cas d'échec pour identifier le contrôle spécifique qui a échoué
            failed_tests = []
            if status == "FAILURE":
                # URL pour accéder aux logs de construction
                console_url = f"{jenkins_url}/job/{pipeline_name}/{build_number}/consoleText"
                console_response = requests.get(console_url, auth=(jenkins_user, jenkins_password))
                
                if console_response.status_code == 200:
                    log_content = console_response.text
                    # Analyser les logs pour identifier les contrôles en échec
                    # Exemple de recherche de lignes spécifiques indiquant des échecs
                    if "❌ Échec de connexion S3" in log_content:
                        failed_tests.append("Connexion S3")
                    if "❌ Échec de connexion à NeonDB" in log_content:
                        failed_tests.append("Connexion NeonDB")
                    if "⚠️ Variables manquantes" in log_content:
                        failed_tests.append("Variables d'environnement")
                    if "❌ Le flux de données présente des erreurs" in log_content:
                        failed_tests.append("Flux de données")
            
            return {
                "status": status,
                "build_number": build_number,
                "date": build_date,
                "success": status == "SUCCESS",
                "failed_tests": failed_tests
            }
        else:
            return None
            
    except Exception as e:
        st.error(f"Erreur lors de la vérification: {str(e)}")
        return None

def environment_check():
    """Page de vérification de l'environnement"""
    st.header("🔧 Contrôle de l'Environnement")
    
    # Explication de la fonctionnalité
    st.markdown("""
    Cette page permet de vérifier que l'ensemble de l'infrastructure MLOps fonctionne correctement.
    """)
    
    # Structure en colonnes pour une meilleure mise en page
    col1, col2 = st.columns([2, 1])
    
    with col1:
        # Vérifier l'état actuel
        env_status = check_environment_status()
        
        if env_status:
            status_icon = "✅" if env_status["success"] else "❌"
            status_text = "Opérationnel" if env_status["success"] else "Problèmes détectés"
            
            st.markdown(f"""
            ### Statut actuel de l'environnement: {status_icon} {status_text}
            
            **Dernière vérification:** Build #{env_status["build_number"]} ({env_status["date"]})
            """)
            
            # Afficher les contrôles en échec s'il y en a
            if not env_status["success"] and env_status.get("failed_tests"):
                st.error("### Contrôles en échec:")
                for test in env_status["failed_tests"]:
                    st.error(f"- ❌ {test}")
        else:
            st.warning("⚠️ Impossible de récupérer le statut actuel de l'environnement")

    with col2:
        # Bouton pour lancer une nouvelle vérification - modifié comme demandé
        if st.button("🔍 Vérifier l'environnement", help="Lance une vérification complète via Jenkins"):
            with st.spinner("Lancement de la vérification..."):
                result = trigger_airflow_dag_with_params_and_wait(
                    dag_id="jenkins_pipeline_trigger", 
                    params={"pipeline_name": "environnement"},
                    max_wait_seconds=60
                )
                if result:
                    st.success("Vérification lancée avec succès!")
                    time.sleep(2)  # Attendre un peu pour que les résultats soient visibles
                    st.experimental_rerun()
                else:
                    st.error("❌ Échec du lancement de la vérification")
    
    # Séparateur visuel
    st.divider()
    
    # Tableau des résultats des dernières vérifications
    st.subheader("Historique des vérifications")
    
    # Option 1: Utiliser l'historique Jenkins directement
    try:
        jenkins_url = os.environ.get("JENKINS_URL", "http://jenkins:8080")
        jenkins_user = os.environ.get("JENKINS_ADMIN_ID")
        jenkins_password = os.environ.get("JENKINS_ADMIN_PASSWORD")
        
        api_url = f"{jenkins_url}/job/environnement/api/json?tree=builds[number,result,timestamp,duration]{{0,5}}"
        
        response = requests.get(api_url, auth=(jenkins_user, jenkins_password))
        
        if response.status_code == 200:
            data = response.json()
            
            # Préparer les données pour le tableau
            jenkins_results = []
            for build in data.get('builds', [])[:5]:  # Limiter aux 5 derniers builds
                status = build.get('result', 'En cours')
                status_icon = "✅ Succès" if status == "SUCCESS" else "❌ Échec" if status == "FAILURE" else "⏳ En cours"
                
                timestamp_ms = build.get('timestamp', 0)
                build_date = datetime.fromtimestamp(timestamp_ms / 1000.0).strftime('%d/%m/%Y %H:%M')
                
                jenkins_results.append({
                    'Date': build_date,
                    'Statut': status_icon
                })
            
            # Créer et afficher le DataFrame
            if jenkins_results:
                results_df = pd.DataFrame(jenkins_results)
                # Modifier l'index pour qu'il commence à 1 au lieu de 0
                results_df.index = range(1, len(results_df) + 1)
                st.dataframe(results_df, use_container_width=True)
            else:
                st.info("Aucun historique de vérification disponible")
                
            # Ajouter la liste des contrôles réalisés
            st.subheader("Liste des contrôles réalisés")
            
            # Liste des contrôles effectués dans le pipeline
            controls = [
                {"nom": "Variables d'environnement", "description": "Vérification des variables critiques (AWS, NeonDB, S3)"},
                {"nom": "Connexion S3", "description": "Test de connexion au bucket S3 et vérification des dossiers principaux"},
                {"nom": "Connexion NeonDB", "description": "Test de connexion à la base de données et vérification des tables MLflow"},
                {"nom": "Services interconnectés", "description": "Vérification de l'accès aux services Airflow, Jenkins, FastAPI et Streamlit"},
                {"nom": "Flux de données", "description": "Vérification du fichier de référence et des modèles sauvegardés"}
            ]
            
            # Créer une table de contrôles
            controls_df = pd.DataFrame(controls)
            controls_df.index = range(1, len(controls_df) + 1)
            st.dataframe(controls_df, use_container_width=True)
                
        else:
            # Fallback vers la méthode Airflow si l'accès direct à Jenkins échoue
            st.info("Utilisation des données Airflow comme fallback...")
            verification_results = get_airflow_dag_runs("jenkins_pipeline_trigger")
            
            if isinstance(verification_results, pd.DataFrame) and not verification_results.empty:
                display_df = verification_results[['timestamp', 'pipeline_name', 'status']]
                display_df.columns = ['Date', 'Pipeline', 'Statut']
                
                display_df['Date'] = pd.to_datetime(display_df['Date']).dt.strftime('%d/%m/%Y %H:%M')
                
                display_df['Statut'] = display_df['Statut'].apply(
                    lambda x: "✅ Succès" if x == "success" else "❌ Échec" if x == "failed" else "⚠️ Erreur"
                )
                
                # Modifier l'index pour qu'il commence à 1
                display_df.index = range(1, len(display_df) + 1)
                
                # Limiter à 5 résultats
                display_df = display_df.head(5)
                
                st.dataframe(display_df, use_container_width=True)
            else:
                st.info("Aucun résultat de vérification disponible")
                
    except Exception as e:
        # En cas d'erreur, utiliser l'approche Airflow existante
        st.warning(f"Impossible d'accéder directement à Jenkins: {str(e)}")
        verification_results = get_airflow_dag_runs("jenkins_pipeline_trigger")
        
        if isinstance(verification_results, pd.DataFrame) and not verification_results.empty:
            display_df = verification_results[['timestamp', 'status']]
            display_df.columns = ['Date', 'Statut']
            
            display_df['Date'] = pd.to_datetime(display_df['Date']).dt.strftime('%d/%m/%Y %H:%M')
            
            display_df['Statut'] = display_df['Statut'].apply(
                lambda x: "✅ Succès" if x == "success" else "❌ Échec" if x == "failed" else "⚠️ Erreur"
            )
            
            # Modifier l'index pour qu'il commence à 1
            display_df.index = range(1, len(display_df) + 1)
            
            # Limiter à 5 résultats
            display_df = display_df.head(5)
            
            st.dataframe(display_df, use_container_width=True)
        else:
            st.info("Aucun résultat de vérification disponible")

def get_airflow_dag_runs(dag_id):
    """Fonction utilitaire pour récupérer les runs d'un DAG Airflow"""
    try:
        airflow_url = "http://airflow-webserver:8080/api/v1/dags"
        
        auth = (os.environ.get("AIRFLOW_USERNAME"), os.environ.get("AIRFLOW_PASSWORD"))
        
        # Récupérer les derniers runs
        dag_runs_url = f"{airflow_url}/{dag_id}/dagRuns"
        response = requests.get(dag_runs_url, auth=auth)
        
        if response.status_code == 200:
            data = response.json()
            dag_runs = data.get('dag_runs', [])
            
            if dag_runs:
                runs_data = []
                for run in dag_runs:
                    conf = run.get('conf', {})
                    pipeline_name = conf.get('pipeline_name', 'N/A')
                    
                    runs_data.append({
                        'timestamp': run.get('execution_date'),
                        'status': run.get('state'),
                        'pipeline_name': pipeline_name
                    })
                
                return pd.DataFrame(runs_data)
            
        return pd.DataFrame()
            
    except Exception as e:
        st.error(f"Erreur lors de la récupération des runs Airflow: {str(e)}")
        return pd.DataFrame()

def main():
    st.title("🌲 Forest Cover Type - MLOps Monitor")
    
    tab1, tab2, tab3, tab4 = st.tabs(["📊 Dashboard nouvelles données", "🔄 Détail des Drifts", "🤖 Suivi du modèle", "🔧 Contrôle Environnement"])
    
    with tab1:
        run_dashboard()
    
    with tab2:
        drift_details()
    
    with tab3:
        model_tracking()
    
    with tab4:
        environment_check()

if __name__ == "__main__":
    main()
