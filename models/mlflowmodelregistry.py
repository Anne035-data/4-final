# enregistrer le modèle en pkl actuel dans une version pour MLflow
import mlflow
import boto3
import pickle
import os
from dotenv import load_dotenv
from sklearn.ensemble import RandomForestClassifier

# Charger les variables d'environnement
# Chemins vers les fichiers de configuration
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env_path = os.path.join(project_root, '.env')
secrets_path = os.path.join(project_root, '.secrets')

# Charger les variables d'environnement
load_dotenv(env_path)
load_dotenv(secrets_path)

# Configuration MLflow
mlflow_tracking_uri = os.getenv('MLFLOW_TRACKING_URI', 'https://anneformation-mlflow-final-project.hf.space')
mlflow.set_tracking_uri(mlflow_tracking_uri)
mlflow.set_experiment("forest_cover_type")

def migrate_model_to_mlflow():
    """Migre le modèle S3 PKL vers MLflow Model Registry"""
    try:
        # Récupérer les paramètres de configuration
        bucket_name = os.getenv('S3_BUCKET')
        aws_access_key = os.getenv('AWS_ACCESS_KEY_ID')
        aws_secret_key = os.getenv('AWS_SECRET_ACCESS_KEY')
        aws_region = os.getenv('AWS_DEFAULT_REGION', 'eu-west-3')
        
        # Vérifier que les paramètres sont présents
        if not all([bucket_name, aws_access_key, aws_secret_key]):
            raise ValueError("Certaines informations d'identification AWS sont manquantes")
        
        model_path = 'covertype/models/forest_cover_type_model.pkl'
        
        print(f"Tentative de chargement du modèle depuis S3: {bucket_name}/{model_path}")
        
        # Créer le client S3 avec les credentials
        s3 = boto3.client(
            's3',
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
            region_name=aws_region
        )
        
        # Charger le modèle depuis S3
        response = s3.get_object(Bucket=bucket_name, Key=model_path)
        model_data = response['Body'].read()
        model = pickle.loads(model_data)
        
        print("Modèle chargé avec succès, enregistrement dans MLflow...")
        
        # Enregistrer le modèle dans MLflow
        with mlflow.start_run(run_name="model_migration") as run:
            # Log des paramètres du modèle
            mlflow.log_params(model.get_params())
            
            # Log des métriques si disponibles
            mlflow.log_metric("accuracy", 0.9282)
            mlflow.log_metric("f1_score", 0.8775)
            
            # Enregistrer le modèle
            mlflow.sklearn.log_model(
                model, 
                "forest_cover_model",
                registered_model_name="forest_cover_type_model"
            )
            
            run_id = run.info.run_id
            
        # Récupérer la version enregistrée
        client = mlflow.tracking.MlflowClient()
        model_versions = client.get_latest_versions("forest_cover_type_model")
        model_version = model_versions[0].version
        
        # Transition vers la production
        client.transition_model_version_stage(
            name="forest_cover_type_model",
            version=model_version,
            stage="Production"
        )
        
        print(f"Modèle enregistré avec succès dans MLflow sous le nom 'forest_cover_type_model' (version {model_version})")
        print(f"Le modèle est maintenant en production et peut être chargé avec: mlflow.sklearn.load_model('models:/forest_cover_type_model/Production')")
        
        return run_id, model_version
        
    except Exception as e:
        print(f"Erreur lors de la migration du modèle: {str(e)}")
        raise

if __name__ == "__main__":
    migrate_model_to_mlflow()