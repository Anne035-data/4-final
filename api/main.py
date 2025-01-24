import os
import boto3
from fastapi import FastAPI
from dotenv import load_dotenv
import pandas as pd
import io

# Chargement des variables d'environnement
load_dotenv('.env')
load_dotenv('.secrets')

app = FastAPI()

# Client S3
s3_client = boto3.client(
    's3',
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY")
)

@app.post("/generate")
async def generate_data():
    # Chargement des données
    data_obj = s3_client.get_object(
        Bucket=os.getenv("S3_BUCKET"),
        Key='covertype/new_data/covtype_20.csv'
    )
    df = pd.read_csv(data_obj['Body'])
    
    # Sélection de 500 échantillons aléatoires
    random_samples = df.sample(n=500)
    
    # Sauvegarde sur S3
    csv_buffer = io.StringIO()
    random_samples.to_csv(csv_buffer, index=False)
    
    s3_client.put_object(
        Bucket=os.getenv("S3_BUCKET"),
        Key='covertype/new_data/covtype_20_random.csv',
        Body=csv_buffer.getvalue()
    )
    
    return {"message": "500 échantillons générés et sauvegardés"}