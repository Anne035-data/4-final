import os
import sys
import pandas as pd
import numpy as np
import mlflow
from mlflow.tracking import MlflowClient
from sklearn.metrics import accuracy_score, f1_score
from sklearn.ensemble import RandomForestClassifier
import boto3
import logging

# Configuration du logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def load_data_from_s3(bucket, reference_key, new_data_key):
    """Charge les données de référence et les nouvelles données depuis S3"""
    try:
        s3 = boto3.client('s3')
        
        # Charger les données de référence
        logging.info(f"Chargement des données de référence depuis s3://{bucket}/{reference_key}")
        ref_obj = s3.get_object(Bucket=bucket, Key=reference_key)
        reference_data = pd.read_csv(ref_obj['Body'])
        
        # Charger les nouvelles données
        logging.info(f"Chargement des nouvelles données depuis s3://{bucket}/{new_data_key}")
        new_obj = s3.get_object(Bucket=bucket, Key=new_data_key)
        new_data = pd.read_csv(new_obj['Body'])
        
        # Conversion des colonnes numériques en float
        numeric_columns = reference_data.select_dtypes(include=[np.number]).columns
        reference_data[numeric_columns] = reference_data[numeric_columns].astype(float)
        
        numeric_columns = new_data.select_dtypes(include=[np.number]).columns
        new_data[numeric_columns] = new_data[numeric_columns].astype(float)
        
        return reference_data, new_data
    except Exception as e:
        logging.error(f"Erreur lors du chargement des données depuis S3: {str(e)}")
        raise

def get_model_version(client, model_name, version=1):
    """Récupère une version spécifique du modèle dans MLflow"""
    try:
        model_version = client.get_model_version(model_name, version)
        return model_version
    except Exception as e:
        logging.error(f"Erreur lors de la récupération de la version {version} du modèle: {str(e)}")
        return None
    
def get_latest_model_version(client, model_name):
    """Récupère la dernière version du modèle dans MLflow"""
    try:
        latest_version = 1
        versions = client.get_latest_versions(model_name)
        if versions:
            # Trouver la version la plus récente
            latest_version = max([int(v.version) for v in versions])
        return latest_version
    except Exception as e:
        logging.error(f"Erreur lors de la récupération de la version du modèle: {str(e)}")
        return 1  # Par défaut, retourner la version 1

def main():
    # Récupérer les variables d'environnement
    mlflow_tracking_uri = os.environ.get('MLFLOW_TRACKING_URI', 'https://anneformation-mlflow-final-project.hf.space')
    s3_bucket = os.environ.get('S3_BUCKET', '4-final-project')
    
    # Chemins S3
    reference_file = 'covertype/reference/covtype_80.csv'
    new_data_file = 'covertype/new_data/covtype.csv'

    try:
        # Connexion à MLflow
        logging.info(f"Connexion à MLflow: {mlflow_tracking_uri}")
        mlflow.set_tracking_uri(mlflow_tracking_uri)
        client = MlflowClient()
        
        # Configuration de l'expérience MLflow
        experiment_name = "forest_cover_type"
        experiment = client.get_experiment_by_name(experiment_name)
        if not experiment:
            experiment_id = client.create_experiment(experiment_name)
        else:
            experiment_id = experiment.experiment_id
        
        # Configurer le run MLflow
        with mlflow.start_run(experiment_id=experiment_id) as run:
            run_id = run.info.run_id
            logging.info(f"Run MLflow démarré: {run_id}")
            
            # Chargement des données
            reference_data, new_data = load_data_from_s3(s3_bucket, reference_file, new_data_file)
            logging.info(f"Données chargées - Référence: {reference_data.shape}, Nouvelles: {new_data.shape}")
            
            # Séparer les caractéristiques et la cible
            X_ref = reference_data.drop('Cover_Type', axis=1)
            y_ref = reference_data['Cover_Type']
            
            X_new = new_data.drop('Cover_Type', axis=1)
            y_new = new_data['Cover_Type']
            
            # Option 1: Charger le modèle existant depuis MLflow et le réentraîner
            model_name = "forest_cover_type_model"
            logging.info(f"Recherche du modèle enregistré: {model_name}")

            try:
                # Récupérer la dernière version du modèle
                latest_version = get_latest_model_version(client, model_name)
                logging.info(f"Dernière version du modèle trouvée: {latest_version}")
                
                # Tenter de charger la dernière version du modèle
                model_version = get_model_version(client, model_name, version=latest_version)
                
                if model_version:
                    logging.info(f"Chargement du modèle {model_name}, version {model_version.version}")
                    
                    # Charger le modèle
                    model_uri = f"models:/{model_name}/{model_version.version}"
                    model = mlflow.sklearn.load_model(model_uri)
                    logging.info(f"Modèle chargé avec succès depuis {model_uri}")
                    
                    # Réentraîner le modèle avec les nouvelles données
                    logging.info("Réentraînement du modèle avec les nouvelles données")
                    model.fit(X_new, y_new)
                else:
                    raise Exception(f"Version {latest_version} du modèle {model_name} non trouvée dans MLflow")
            except Exception as e:
                logging.warning(f"Modèle non trouvé ou erreur de chargement: {str(e)}")
                logging.info("Création d'un nouveau modèle RandomForest")

                # Créer un nouveau modèle si le chargement échoue
                model = RandomForestClassifier(
                    n_estimators=100,
                    max_depth=10,
                    random_state=42,
                    n_jobs=-1
                )
                model.fit(X_ref, y_ref)
            
            # Évaluer le modèle
            logging.info("Évaluation du modèle")
            y_pred = model.predict(X_new)
            accuracy = accuracy_score(y_new, y_pred)
            f1 = f1_score(y_new, y_pred, average='weighted')
            
            logging.info(f"Métriques - Accuracy: {accuracy:.4f}, F1 Score: {f1:.4f}")
            
            # Enregistrer les métriques dans MLflow
            mlflow.log_metric("accuracy", accuracy)
            mlflow.log_metric("f1_score", f1)
            logging.info(f"Métriques enregistrées dans MLflow - Accuracy: {accuracy:.4f}, F1 Score: {f1:.4f}")
            
            # Enregistrer les paramètres du modèle
            if isinstance(model, RandomForestClassifier):
                mlflow.log_param("n_estimators", model.n_estimators)
                mlflow.log_param("max_depth", model.max_depth)
            
            # Enregistrer le modèle dans MLflow
            logging.info(f"Enregistrement du modèle dans MLflow")
            mlflow.sklearn.log_model(model, "forest_cover_model")
            
            # Enregistrer le modèle dans le registre MLflow
            registered_model = mlflow.register_model(
                f"runs:/{run_id}/forest_cover_model",
                model_name
            )
            logging.info(f"Modèle enregistré: {model_name}, version: {registered_model.version}")
            
            # Vérifier les métriques enregistrées
            try:
                current_run = client.get_run(run_id)
                logged_accuracy = current_run.data.metrics.get("accuracy", None)
                logged_f1 = current_run.data.metrics.get("f1_score", None)
                logging.info(f"Métriques vérifiées: Accuracy={logged_accuracy}, F1 Score={logged_f1}")
            except Exception as e:
                logging.warning(f"Impossible de vérifier les métriques enregistrées: {str(e)}")
            
            return {
                "status": "success",
                "metrics": {
                    "accuracy": accuracy,
                    "f1_score": f1
                },
                "model_info": {
                    "name": model_name,
                    "version": registered_model.version
                },
                "run_id": run_id
            }

    except Exception as e:
        logging.error(f"Erreur dans l'exécution du script: {str(e)}")
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    result = main()
    logging.info(f"Résultat final: {result}")
    if result["status"] == "error":
        sys.exit(1)
    sys.exit(0)