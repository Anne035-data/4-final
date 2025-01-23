import os
import boto3
import logging
import mlflow
from dotenv import load_dotenv
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, f1_score
from sqlalchemy import create_engine, text
import alembic.config
import mlflow.store.db.utils

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# configuration des chemins
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)

# chemins vers les fichiers de configuration
env_path = os.path.join(project_root, '.env')
secrets_path = os.path.join(project_root, '.secrets')

logger.info(f"Chargement des variables depuis {env_path}")
logger.info(f"Chargement des secrets depuis {secrets_path}")

# chargement des variables d'environnement
load_dotenv(env_path)
load_dotenv(secrets_path)

# vérification des variables d'env.
aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")

if not aws_access_key or not aws_secret_key:
    logger.error("AWS credentials manquants:")
    logger.error(f"AWS_ACCESS_KEY_ID: {'Présent' if aws_access_key else 'Manquant'}")
    logger.error(f"AWS_SECRET_ACCESS_KEY: {'Présent' if aws_secret_key else 'Manquant'}")
    exit(1)

# configuration du tracking MLflow avec la base de données
mlflow_tracking_uri = "postgresql+psycopg2://" + os.getenv("NEON_DATABASE_URL").replace("postgresql://", "")
mlflow.set_tracking_uri(mlflow_tracking_uri)
mlflow.set_experiment("forest_cover_type")

# fonction d'entrainement
def train():
    try:
        # Configuration MLflow
        mlflow.set_experiment("forest_cover_type")
        logging.info("MLflow configuré")
        
        # Charger les données
        s3 = boto3.client('s3')
        bucket = os.environ['S3_BUCKET']
        data_obj = s3.get_object(Bucket=bucket, Key='covertype/new_data/covtype_20.csv')
        data = pd.read_csv(data_obj['Body'])
        logging.info(f"Données chargées : {data.shape[0]} échantillons")
        
        # Vérifier et renommer la colonne cible si nécessaire
        target_column = 'target'
        if target_column not in data.columns:
            if 'Cover_Type' in data.columns:
                data = data.rename(columns={'Cover_Type': 'target'})
                
        logging.info(f"Colonnes disponibles : {data.columns.tolist()}")
        
        # Préparation des données
        X = data.drop('target', axis=1)
        y = data['target']
        
        # Entrainement
        with mlflow.start_run():
            model = RandomForestClassifier(n_estimators=50)
            model.fit(X, y)
            
            accuracy = model.score(X, y)
            f1 = f1_score(y, model.predict(X), average='weighted')  # weighted car problème multi-classe
            logging.info(f"Accuracy: {accuracy}")
            logging.info(f"F1 Score: {f1}")
            
            # Log uniquement les métriques et paramètres
            mlflow.log_params(model.get_params())
            mlflow.log_metric("accuracy", accuracy)
            mlflow.log_metric("f1_score", f1)
            
    except Exception as e:
        logging.error(f"Erreur : {str(e)}")
        raise

if __name__ == "__main__":
    train()