import os
import io
import time
import boto3
import pandas as pd
import random
from typing import Optional
from datetime import datetime
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Chargement des variables d'environnement
load_dotenv('.env')
load_dotenv('.secrets')

class GenerationOptions(BaseModel):
    sample_size: int = Field(500, description="Nombre d'échantillons à générer")
    random_seed: Optional[int] = Field(None, description="Graine aléatoire pour la reproductibilité")

# Initialisation de l'application FastAPI
app = FastAPI(
    title="🌲 Forest Cover Type Data Generator",
    description="API pour générer des échantillons aléatoires de données de couverture forestière",
    version="1.0.0"
)

# Client S3
s3_client = boto3.client(
    's3',
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1")
)

@app.get("/", response_class=HTMLResponse)
async def root():
    """Page d'accueil de l'API"""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Forest Cover Type Data Generator</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                max-width: 800px;
                margin: 0 auto;
                padding: 20px;
                color: #333;
            }
            h1 {
                color: #2c732f;
                border-bottom: 2px solid #eee;
            }
            .card {
                background: #f9f9f9;
                border-radius: 8px;
                padding: 20px;
                margin-bottom: 20px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }
            .tab {
                padding: 10px 20px;
                cursor: pointer;
                border: 1px solid #ddd;
                margin-right: 5px;
                border-radius: 4px 4px 0 0;
                background: #f5f5f5;
            }
            .tab.active {
                background: #e9f5e9;
                border-bottom: 2px solid #2c732f;
            }
            .tab-content {
                display: none;
                padding: 20px;
                border: 1px solid #ddd;
                border-radius: 0 0 4px 4px;
            }
            .tab-content.active {
                display: block;
            }
            button {
                background: #2c732f;
                color: white;
                border: none;
                padding: 10px 20px;
                border-radius: 4px;
                cursor: pointer;
                margin-top: 10px;
            }
            button:hover {
                background: #225f25;
            }
            input[type="number"] {
                width: 100px;
                padding: 5px;
                border: 1px solid #ddd;
                border-radius: 4px;
            }
            label {
                display: block;
                margin: 10px 0 5px;
                font-weight: bold;
            }
            .result {
                background: #f0f0f0;
                padding: 15px;
                border-radius: 4px;
                margin-top: 15px;
                border-left: 4px solid #666;
            }
            table {
                width: 100%;
                border-collapse: collapse;
                margin-top: 15px;
            }
            th, td {
                padding: 8px;
                border: 1px solid #ddd;
                text-align: left;
            }
            th {
                background-color: #f2f2f2;
            }
            .restore-button {
                background-color: #4a6741;
                padding: 5px 10px;
                font-size: 12px;
            }
        </style>
        <script>
            function showTab(tabId) {
                // Cacher tous les contenus d'onglets
                const tabContents = document.querySelectorAll('.tab-content');
                tabContents.forEach(content => {
                    content.classList.remove('active');
                });
                
                // Désactiver tous les onglets
                const tabs = document.querySelectorAll('.tab');
                tabs.forEach(tab => {
                    tab.classList.remove('active');
                });
                
                // Activer l'onglet sélectionné
                document.getElementById(tabId).classList.add('active');
                document.querySelector(`[data-tab="${tabId}"]`).classList.add('active');
                
                // Si l'onglet de restauration est sélectionné, charger la liste des fichiers
                if (tabId === 'restore-tab') {
                    listHistoricalFiles();
                }
            }
            
            async function generateData(withDrift) {
                const form = withDrift ? document.getElementById('driftForm') : document.getElementById('normalForm');
                const resultDiv = withDrift ? document.getElementById('driftResult') : document.getElementById('normalResult');
                
                resultDiv.innerHTML = 'Génération en cours...';
                
                try {
                    let url;
                    let data;
                    
                    if (withDrift) {
                        const sampleSize = form.querySelector('input[name="sample_size"]').value;
                        const numColumns = form.querySelector('input[name="num_columns"]').value;
                        url = `/generate_drift_data?sample_size=${sampleSize}&num_columns=${numColumns}`;
                        data = {};
                    } else {
                        const sampleSize = form.querySelector('input[name="sample_size"]').value;
                        url = '/generate';
                        data = { 
                            sample_size: parseInt(sampleSize)
                        };
                        
                        const randomSeed = form.querySelector('input[name="random_seed"]').value;
                        if (randomSeed) {
                            data.random_seed = parseInt(randomSeed);
                        }
                    }
                    
                    const response = await fetch(url, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: withDrift ? null : JSON.stringify(data)
                    });
                    
                    const result = await response.json();
                    
                    resultDiv.innerHTML = `
                        <h4>Résultat:</h4>
                        <p>${result.message}</p>
                    `;
                } catch (error) {
                    resultDiv.innerHTML = `<p style="color: red;">Erreur: ${error.message}</p>`;
                }
            }
            
            // Fonction pour lister les fichiers historiques
            async function listHistoricalFiles() {
                const filesContainer = document.getElementById('files-container');
                const filesList = document.getElementById('files-list');
                const restoreResult = document.getElementById('restoreResult');
                
                restoreResult.innerHTML = 'Chargement des fichiers...';
                
                try {
                    const response = await fetch('/list_historical_files');
                    const result = await response.json();
                    
                    if (result.files && result.files.length > 0) {
                        // Vider la liste existante
                        filesList.innerHTML = '';
                        
                        // Afficher la table
                        filesContainer.style.display = 'block';
                        
                        // Ajouter chaque fichier à la liste
                        result.files.forEach(file => {
                            const row = document.createElement('tr');
                            
                            // Formater la date pour l'affichage
                            const fileDate = new Date(file.last_modified);
                            const formattedDate = fileDate.toLocaleString('fr-FR', {
                                year: 'numeric',
                                month: '2-digit',
                                day: '2-digit',
                                hour: '2-digit',
                                minute: '2-digit'
                            });
                            
                            // Convertir la taille en KB
                            const fileSizeKB = Math.round(file.size_bytes / 1024 * 10) / 10;
                            
                            row.innerHTML = `
                                <td>${file.filename}</td>
                                <td>${formattedDate}</td>
                                <td style="text-align: right;">${fileSizeKB} KB</td>
                                <td style="text-align: center;">
                                    <button 
                                        onclick="restoreFile('${file.filename}')" 
                                        class="restore-button"
                                    >
                                        Restaurer
                                    </button>
                                </td>
                            `;
                            
                            filesList.appendChild(row);
                        });
                        
                        restoreResult.innerHTML = `${result.files.length} fichiers disponibles. Cliquez sur "Restaurer" pour utiliser un fichier comme fichier courant.`;
                    } else {
                        filesContainer.style.display = 'none';
                        restoreResult.innerHTML = 'Aucun fichier historique disponible.';
                    }
                } catch (error) {
                    restoreResult.innerHTML = `<p style="color: red;">Erreur: ${error.message}</p>`;
                }
            }
            
            // Fonction pour restaurer un fichier
            async function restoreFile(filename) {
                const restoreResult = document.getElementById('restoreResult');
                restoreResult.innerHTML = `Restauration du fichier ${filename} en cours...`;
                
                try {
                    const response = await fetch('/restore_file?filename=' + encodeURIComponent(filename), {
                        method: 'POST'
                    });
                    
                    const result = await response.json();
                    
                    if (response.ok) {
                        restoreResult.innerHTML = `
                            <h4>Résultat:</h4>
                            <p style="color: green;">${result.message}</p>
                        `;
                    } else {
                        restoreResult.innerHTML = `
                            <h4>Erreur:</h4>
                            <p style="color: red;">${result.detail || 'Échec de la restauration'}</p>
                        `;
                    }
                } catch (error) {
                    restoreResult.innerHTML = `<p style="color: red;">Erreur: ${error.message}</p>`;
                }
            }
        </script>
    </head>
    <body>
        <h1>🌲 Forest Cover Type Data Generator</h1>
        
        <div class="card">
             <h2>À propos</h2>
            <p>Cette API permet de générer des échantillons aléatoires depuis le jeu de données de couverture forestière pour tester le pipeline MLOps.</p>
            <p>Elle fait partie d'un système de détection de drift et de retraining automatique.</p>
    
            <h3>Simulation de drift</h3>
            <p>Le drift est simulé en appliquant un facteur multiplicatif aléatoire (entre 0.8 et 1.5) aux colonnes numériques sélectionnées. Ce facteur peut augmenter ou diminuer les valeurs d'origine, ce qui modifie la distribution des données.</p>
            <p><strong>Note importante :</strong> Même un échantillon de données "normales" peut présenter des caractéristiques de drift, surtout si l'échantillon est petit. De même, le nombre de colonnes effectivement affectées par un drift perceptible peut être légèrement différent du nombre demandé, car certaines modifications peuvent être masquées par la variabilité statistique naturelle des données.</p>
        </div>
        
        <div class="card">
            <h2>Générer des données</h2>
            
            <div class="tabs">
                <div class="tab active" data-tab="normal-tab" onclick="showTab('normal-tab')">Données normales</div>
                <div class="tab" data-tab="drift-tab" onclick="showTab('drift-tab')">Données avec drift</div>
                <div class="tab" data-tab="restore-tab" onclick="showTab('restore-tab')">Restaurer fichier</div>
            </div>
            
            <div id="normal-tab" class="tab-content active">
                <h3>Générer un échantillon aléatoire</h3>
                <p>Génère un échantillon aléatoire et le sauvegarde sur S3.</p>
                
                <form id="normalForm">
                    <label for="normal-sample-size">Taille d'échantillon:</label>
                    <input type="number" id="normal-sample-size" name="sample_size" value="500" min="1" max="10000">
                    
                    <label for="random-seed">Graine aléatoire (optionnel):</label>
                    <input type="number" id="random-seed" name="random_seed">
                    
                    <button type="button" onclick="generateData(false)">Générer des données</button>
                </form>
                
                <div id="normalResult" class="result">
                    Les résultats s'afficheront ici.
                </div>
            </div>
            
            <div id="drift-tab" class="tab-content">
                <h3>Générer des données avec drift</h3>
                <p>Génère des données avec drift pour tester la détection.</p>
                
                <form id="driftForm">
                    <label for="drift-sample-size">Taille d'échantillon:</label>
                    <input type="number" id="drift-sample-size" name="sample_size" value="500" min="1" max="10000">
                    
                    <label for="num-columns">Nombre de colonnes à modifier:</label>
                    <input type="number" id="num-columns" name="num_columns" value="12" min="1" max="40">
                    
                    <button type="button" onclick="generateData(true)">Générer des données avec drift</button>
                </form>
                
                <div id="driftResult" class="result">
                    Les résultats s'afficheront ici.
                </div>
            </div>
            
            <div id="restore-tab" class="tab-content">
                <h3>Restaurer un fichier historique</h3>
                <p>Permet de réutiliser un fichier historique comme fichier courant pour un nouveau test.</p>
                
                <button type="button" id="list-files-btn" onclick="listHistoricalFiles()">
                    Rafraîchir la liste des fichiers
                </button>
                
                <div id="files-container" style="margin-top: 15px; display: none;">
                    <table>
                        <thead>
                            <tr>
                                <th>Nom du fichier</th>
                                <th>Date de modification</th>
                                <th style="text-align: right;">Taille</th>
                                <th style="text-align: center;">Action</th>
                            </tr>
                        </thead>
                        <tbody id="files-list">
                            <!-- Les fichiers seront ajoutés ici dynamiquement -->
                        </tbody>
                    </table>
                </div>
                
                <div id="restoreResult" class="result">
                    Chargement des fichiers disponibles...
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.post("/generate")
async def generate_data(options: Optional[GenerationOptions] = None):
    start_time = time.time()
    
    # Utiliser les valeurs par défaut si aucune option n'est fournie
    if options is None:
        options = GenerationOptions()
    
    try:
        # Définir le bucket S3
        bucket = os.getenv("S3_BUCKET")
        
        # Chargement des données
        data_obj = s3_client.get_object(
            Bucket=bucket,
            Key='covertype/new_data/covtype_20.csv'
        )
        
        # Lecture des données
        df = pd.read_csv(data_obj['Body'])
        
        # Sélection des échantillons aléatoires sans remplacement
        random_samples = df.sample(
            n=options.sample_size, 
            random_state=options.random_seed,
            replace=False
        )
        
        # Sauvegarde sur S3
        csv_buffer = io.StringIO()
        random_samples.to_csv(csv_buffer, index=False)

        # Générer un nom de fichier unique avec timestamp pour l'archivage
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        archive_key = f'covertype/new_data/covtype_{timestamp}.csv'
        
        # Destination standard pour le traitement par DAG
        destination_key = 'covertype/new_data/covtype.csv'
        
        # Sauvegarde du fichier principal (sans date)
        s3_client.put_object(
            Bucket=bucket,
            Key=destination_key,
            Body=csv_buffer.getvalue()
        )
        
        # Sauvegarde de la copie archivée (avec date)
        s3_client.put_object(
            Bucket=bucket,
            Key=archive_key,
            Body=csv_buffer.getvalue()
        )
        
        return {
            "message": f"{options.sample_size} échantillons générés et sauvegardés dans covtype.csv et {archive_key}"
        }
        
    except Exception as e:
        return {"detail": f"Une erreur s'est produite: {str(e)}"}

@app.post("/generate_drift_data")
async def generate_drift_data(sample_size: int = 500, num_columns: int = 12):
    
    try:
        # Définir le bucket S3
        bucket = os.getenv("S3_BUCKET")
        
        # Chargement des données
        data_obj = s3_client.get_object(
            Bucket=bucket,
            Key='covertype/new_data/covtype_20.csv'
        )
        
        # Lecture des données
        df = pd.read_csv(data_obj['Body'])
        
        # Sélectionner un sous-ensemble sans remplacement
        drift_df = df.sample(n=sample_size, random_state=42, replace=False)
        
        # Identifier les colonnes numériques (en excluant Cover_Type)
        numeric_cols = df.select_dtypes(include=['int64', 'float64']).columns.tolist()
        if 'Cover_Type' in numeric_cols:
            numeric_cols.remove('Cover_Type')
        
        # S'assurer que num_columns ne dépasse pas le nombre de colonnes disponibles
        num_columns = min(num_columns, len(numeric_cols))
        
        # Sélectionner les colonnes à modifier
        columns_to_modify = numeric_cols[:num_columns]
        
        # Modifier les colonnes numériques pour simuler un drift
        for col in columns_to_modify:
            if col in drift_df.columns:
                # Générer un facteur de drift aléatoire entre 0.8 et 1.5
                random_factor = random.uniform(0.8, 1.5)
                direction = random.choice(["augmentation", "diminution"])
                
                if direction == "diminution":
                    actual_factor = 1 / random_factor  # Pour diminuer la valeur
                else:
                    actual_factor = random_factor  # Pour augmenter la valeur
                
                # Appliquer le facteur de drift
                drift_df[col] = drift_df[col] * actual_factor
        
        # Sauvegarde sur S3
        csv_buffer = io.StringIO()
        drift_df.to_csv(csv_buffer, index=False)
        
        # Générer un nom de fichier unique avec timestamp pour l'archivage
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        archive_key = f'covertype/new_data/covtype_drift_{timestamp}.csv'
        
        # Destination standard pour le traitement par DAG
        destination_key = 'covertype/new_data/covtype.csv'
        
        # Sauvegarde du fichier principal (sans date)
        s3_client.put_object(
            Bucket=bucket,
            Key=destination_key,
            Body=csv_buffer.getvalue()
        )
        
        # Sauvegarde de la copie archivée (avec date)
        s3_client.put_object(
            Bucket=bucket,
            Key=archive_key,
            Body=csv_buffer.getvalue()
        )
        
        return {
            "message": f"{sample_size} échantillons avec drift générés et sauvegardés dans covtype.csv et {archive_key}"
        }
        
    except Exception as e:
        return {"detail": f"Une erreur s'est produite: {str(e)}"}

@app.post("/restore_file")
async def restore_historical_file(filename: str):
    """Restaure un fichier historique en tant que fichier covtype.csv actif"""
    try:
        # Définir le bucket S3
        bucket = os.getenv("S3_BUCKET")
        
        # Vérifier que le fichier source existe
        source_key = f'covertype/new_data/{filename}'
        
        try:
            # Vérifier l'existence du fichier source
            s3_client.head_object(Bucket=bucket, Key=source_key)
        except Exception as e:
            return {"detail": f"Le fichier source {filename} n'existe pas: {str(e)}"}
        
        # Copier le fichier historique vers covtype.csv
        s3_client.copy_object(
            CopySource={'Bucket': bucket, 'Key': source_key},
            Bucket=bucket,
            Key='covertype/new_data/covtype.csv'
        )
        
        # Également créer une copie d'archive avec timestamp pour traçabilité
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        archive_key = f'covertype/new_data/covtype_restored_{timestamp}.csv'
        
        s3_client.copy_object(
            CopySource={'Bucket': bucket, 'Key': source_key},
            Bucket=bucket, 
            Key=archive_key
        )
        
        return {
            "message": f"Le fichier {filename} a été restauré comme fichier courant covtype.csv et archivé sous {archive_key}"
        }
        
    except Exception as e:
        return {"detail": f"Une erreur s'est produite lors de la restauration: {str(e)}"}
    
@app.get("/list_historical_files")
async def list_historical_files():
    """Liste tous les fichiers historiques disponibles pour restauration"""
    try:
        # Définir le bucket S3
        bucket = os.getenv("S3_BUCKET")
        
        # Lister les fichiers du préfixe
        response = s3_client.list_objects_v2(
            Bucket=bucket,
            Prefix='covertype/new_data/covtype_'
        )
        
        # Extraire les noms des fichiers et les trier par date (du plus récent au plus ancien)
        files = []
        if 'Contents' in response:
            for obj in response['Contents']:
                key = obj['Key']
                # Ne prendre que le nom du fichier, pas le chemin complet
                filename = key.split('/')[-1]
                # Ne pas inclure covtype.csv (le fichier courant)
                if filename != 'covtype.csv':
                    last_modified = obj['LastModified']
                    size = obj['Size']
                    files.append({
                        'filename': filename,
                        'last_modified': last_modified.isoformat(),
                        'size_bytes': size
                    })
        
        # Trier par date de modification (du plus récent au plus ancien)
        files = sorted(files, key=lambda x: x['last_modified'], reverse=True)
        
        return {"files": files}
        
    except Exception as e:
        return {"detail": f"Une erreur s'est produite: {str(e)}"}

# Pour démarrer l'application en local
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)