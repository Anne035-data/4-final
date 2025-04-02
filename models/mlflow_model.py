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
import json

# Configuration du logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def load_data_from_s3(bucket, reference_key, new_data_key):
    """Charge les données de référence et les nouvelles données depuis S3"""
    try:
        s3 = boto3.client('s3')
        
        # Charger les données de référence
        logging.info(f"Chargement des données de référence depuis s3://{bucket}/{reference_key}")
        ref_obj = s3.get_object(Bucket=bucket, Key=reference_file)
        reference_data = pd.read_csv(ref_obj['Body'])
        
        # Charger les nouvelles données
        logging.info(f"Chargement des nouvelles données depuis s3://{bucket}/{new_data_key}")
        new_obj = s3.get_object(Bucket=bucket, Key=new_data_file)
        new_data = pd.read_csv(new_obj['Body'])
        
        # Analyser les données chargées
        logging.info(f"Taille des données de référence: {reference_data.shape}")
        logging.info(f"Taille des nouvelles données: {new_data.shape}")
        
        # Vérifier si Cover_Type est bien présent
        if 'Cover_Type' not in reference_data.columns or 'Cover_Type' not in new_data.columns:
            logging.error(f"Colonne Cover_Type manquante dans les données!")
            if 'Cover_Type' not in reference_data.columns:
                logging.error(f"Colonnes dans reference_data: {reference_data.columns.tolist()}")
            if 'Cover_Type' not in new_data.columns:
                logging.error(f"Colonnes dans new_data: {new_data.columns.tolist()}")
            raise ValueError("Colonne Cover_Type manquante dans les données")
        
        # Ajouter un résumé des classes de Cover_Type pour vérifier la distribution
        logging.info(f"Distribution des classes dans reference_data:\n{reference_data['Cover_Type'].value_counts()}")
        logging.info(f"Distribution des classes dans new_data:\n{new_data['Cover_Type'].value_counts()}")
        
        # Conversion des colonnes numériques en float
        numeric_columns = reference_data.select_dtypes(include=[np.number]).columns
        reference_data[numeric_columns] = reference_data[numeric_columns].astype(float)
        
        numeric_columns = new_data.select_dtypes(include=[np.number]).columns
        new_data[numeric_columns] = new_data[numeric_columns].astype(float)
        
        # Ajouter un identifiant unique aux données
        reference_data['_source'] = 'reference'
        new_data['_source'] = 'new_data'
        
        return reference_data, new_data
    except Exception as e:
        logging.error(f"Erreur lors du chargement des données depuis S3: {str(e)}")
        raise

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
        
        # Configurer le run MLflow avec un tag unique pour suivre l'exécution
        run_tag = f"run_{int(time.time())}"
        with mlflow.start_run(experiment_id=experiment_id, tags={"run_tag": run_tag}) as run:
            run_id = run.info.run_id
            logging.info(f"Run MLflow démarré: {run_id} avec tag: {run_tag}")
            
            # Chargement des données
            reference_data, new_data = load_data_from_s3(s3_bucket, reference_file, new_data_file)
            
            # Log des informations sur les données pour vérifier qu'elles sont bien distinctes
            mlflow.log_param("reference_data_shape", str(reference_data.shape))
            mlflow.log_param("new_data_shape", str(new_data.shape))
            mlflow.log_param("reference_data_hash", str(hash(tuple(reference_data.head(5).values.flatten()))))
            mlflow.log_param("new_data_hash", str(hash(tuple(new_data.head(5).values.flatten()))))
            
            # Séparer les caractéristiques et la cible
            X_ref = reference_data.drop(['Cover_Type', '_source'], axis=1)
            y_ref = reference_data['Cover_Type']
            
            X_new = new_data.drop(['Cover_Type', '_source'], axis=1)
            y_new = new_data['Cover_Type']
            
            # Vérifier si les données X_new et X_ref sont différentes
            is_same_data = X_new.equals(X_ref.iloc[:len(X_new)])
            logging.info(f"Les données X_new et X_ref sont-elles identiques? {is_same_data}")
            mlflow.log_param("is_same_data", str(is_same_data))
            
            # Tentative de chargement du modèle depuis MLflow
            model_name = "forest_cover_type_model"
            logging.info(f"Recherche du modèle enregistré: {model_name}")

            # Variable pour suivre si on utilise un modèle de secours ou MLflow
            using_fallback = False
            
            try:
                # Récupérer les versions du modèle
                versions = client.get_latest_versions(model_name)
                if not versions:
                    raise Exception(f"Aucune version du modèle {model_name} trouvée")
                
                # Sélectionner la version la plus récente
                latest_version = max(versions, key=lambda v: int(v.version))
                logging.info(f"Dernière version du modèle : {latest_version.version}")
                
                # Tester si le modèle existe vraiment
                try:
                    model_uri = f"models:/{model_name}/{latest_version.version}"
                    logging.info(f"Tentative de chargement du modèle depuis: {model_uri}")
                    model = mlflow.sklearn.load_model(model_uri)
                    
                    # Vérifier si le modèle peut prédire sur les données avant réentraînement
                    sample_preds_before = model.predict(X_new.iloc[:5])
                    logging.info(f"Prédictions sur échantillon avant réentraînement: {sample_preds_before}")
                    
                    logging.info(f"Modèle chargé avec succès depuis {model_uri}")
                    
                    # Identifier le type de modèle
                    model_type = type(model).__name__
                    logging.info(f"Type de modèle chargé: {model_type}")
                    mlflow.log_param("loaded_model_type", model_type)
                    
                    # Enregistrer les paramètres actuels du modèle avant réentraînement
                    if hasattr(model, 'get_params'):
                        model_params = model.get_params()
                        logging.info(f"Paramètres du modèle avant réentraînement: {json.dumps(model_params)}")
                        mlflow.log_param("model_params_before", json.dumps(model_params))
                    
                    # Réentraîner avec les nouvelles données
                    logging.info(f"Réentraînement du modèle avec les nouvelles données: {X_new.shape}")
                    model_before = model
                    model.fit(X_new, y_new)
                    
                    # Vérifier si le modèle a été modifié par le réentraînement
                    if model is model_before:
                        logging.info("Référence du modèle identique après fit")
                    else:
                        logging.info("Nouvelle référence de modèle après fit")
                    
                    # Vérifier si les paramètres ont changé
                    if hasattr(model, 'get_params'):
                        new_params = model.get_params()
                        params_changed = new_params != model_params if 'model_params' in locals() else "Inconnu"
                        logging.info(f"Les paramètres ont-ils changé? {params_changed}")
                        mlflow.log_param("params_changed", str(params_changed))
                        
                except Exception as e:
                    logging.error(f"Échec du chargement du modèle: {str(e)}")
                    raise
                
            except Exception as e:
                logging.warning(f"Modèle non trouvé ou erreur de chargement: {str(e)}")
                logging.info("Création d'un nouveau modèle RandomForest")
                using_fallback = True
                
                # Créer un nouveau modèle si le chargement échoue
                model = RandomForestClassifier(
                    n_estimators=100,
                    max_depth=10,
                    random_state=42,
                    n_jobs=-1
                )
                
                # Entraîner sur les nouvelles données comme vous l'avez modifié
                logging.info(f"Entraînement du modèle de secours sur les nouvelles données: {X_new.shape}")
                model.fit(X_new, y_new)
            
            # Log du flag indiquant si on utilise le modèle de secours
            mlflow.log_param("using_fallback_model", str(using_fallback))
            
            # Évaluer le modèle
            logging.info("Évaluation du modèle")
            y_pred = model.predict(X_new)
            accuracy = accuracy_score(y_new, y_pred)
            f1 = f1_score(y_new, y_pred, average='weighted')
            
            logging.info(f"Métriques - Accuracy: {accuracy:.4f}, F1 Score: {f1:.4f}")
            
            # Si on a des échantillons dans sample_preds_before, comparer avec après réentraînement
            if 'sample_preds_before' in locals():
                sample_preds_after = model.predict(X_new.iloc[:5])
                logging.info(f"Prédictions sur échantillon après réentraînement: {sample_preds_after}")
                predictions_changed = not np.array_equal(sample_preds_before, sample_preds_after)
                logging.info(f"Les prédictions ont-elles changé? {predictions_changed}")
                mlflow.log_param("predictions_changed", str(predictions_changed))
            
            # Évaluer sur les données de référence aussi pour voir la différence
            y_pred_ref = model.predict(X_ref)
            accuracy_ref = accuracy_score(y_ref, y_pred_ref)
            f1_ref = f1_score(y_ref, y_pred_ref, average='weighted')
            
            logging.info(f"Métriques sur données de référence - Accuracy: {accuracy_ref:.4f}, F1 Score: {f1_ref:.4f}")
            
            # Enregistrer les métriques dans MLflow
            mlflow.log_metric("accuracy", accuracy)
            mlflow.log_metric("f1_score", f1)
            mlflow.log_metric("accuracy_on_reference", accuracy_ref)
            mlflow.log_metric("f1_score_on_reference", f1_ref)
            
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
            
            return {
                "status": "success",
                "metrics": {
                    "accuracy": accuracy,
                    "f1_score": f1,
                    "accuracy_on_reference": accuracy_ref,
                    "f1_score_on_reference": f1_ref
                },
                "model_info": {
                    "name": model_name,
                    "version": registered_model.version,
                    "using_fallback": using_fallback
                },
                "run_id": run_id,
                "run_tag": run_tag
            }

    except Exception as e:
        logging.error(f"Erreur dans l'exécution du script: {str(e)}")
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    import time
    result = main()
    logging.info(f"Résultat final: {result}")
    if result["status"] == "error":
        sys.exit(1)
    sys.exit(0)
