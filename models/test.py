import pytest
import boto3
import pandas as pd
import numpy as np
import os
from datetime import datetime
from sklearn.preprocessing import StandardScaler

test_results = []  # Pour stocker les résultats des tests

# Fixtures
@pytest.fixture(autouse=True, scope="session")
def handle_test_results(request):
    yield
    print("\nSauvegarde des résultats...")
    save_test_results()

# Fonction pour récupérer les données de référence et les nouvelles données
@pytest.fixture
def reference_data():
    s3 = boto3.client('s3')
    bucket = os.environ['S3_BUCKET']
    data_obj = s3.get_object(Bucket=bucket, Key='covertype/reference/covtype_80.csv')
    return pd.read_csv(data_obj['Body'])

@pytest.fixture
def new_data():
    s3 = boto3.client('s3')
    bucket = os.environ['S3_BUCKET']
    data_obj = s3.get_object(Bucket=bucket, Key='covertype/new_data/covtype_20.csv')
    return pd.read_csv(data_obj['Body'])

# Fonction pour sauvegarder les résultats des tests
def save_test_results():
    """Sauvegarde les résultats dans un CSV sur S3"""
    try:
        print("Création du DataFrame des résultats de test...")
        df_results = pd.DataFrame(test_results)
        
        print("Configuration du client S3...")
        s3 = boto3.client('s3')
        bucket = os.environ['S3_BUCKET']
        key = f'covertype/test_reports/test_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
        
        print(f"Tentative de sauvegarde sur S3: {bucket}/{key}")
        csv_buffer = df_results.to_csv(index=False).encode()
        
        # Test des permissions S3
        print("Test des permissions S3...")
        try:
            s3.list_objects(Bucket=bucket, Prefix='covertype/test_reports/')
            print("Accès en lecture OK")
        except Exception as e:
            print(f"Erreur lors du test de lecture S3: {str(e)}")
        
        # Tentative de sauvegarde
        print("Tentative de sauvegarde du fichier...")
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=csv_buffer
        )
        print(f"Rapport sauvegardé avec succès: {key}")
        
    except Exception as e:
        print(f"Erreur lors de la sauvegarde du rapport: {str(e)}")
        print(f"Variables d'environnement disponibles: {list(os.environ.keys())}")

# Tests
# Fonction pour tester la cohérence du schéma
def test_schema_consistency(reference_data, new_data):
    """Vérifie que les deux datasets ont les mêmes colonnes"""
    try:
        assert list(reference_data.columns) == list(new_data.columns)
        test_results.append({
            'test_name': 'schema_consistency',
            'status': 'PASSED',
            'description': 'Colonnes identiques',
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
    except AssertionError:
        test_results.append({
            'test_name': 'schema_consistency',
            'status': 'FAILED',
            'description': 'Colonnes différentes détectées',
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
        raise

# Fonction pour tester les types de données
def test_data_types(reference_data, new_data):
    """Vérifie que les types de données sont cohérents"""
    try:
        for col in reference_data.columns:
            assert reference_data[col].dtype == new_data[col].dtype
        test_results.append({
            'test_name': 'data_types',
            'status': 'PASSED',
            'description': 'Types de données cohérents',
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
    except AssertionError:
        test_results.append({
            'test_name': 'data_types',
            'status': 'FAILED',
            'description': f'Type incohérent pour la colonne {col}',
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
        raise

# Fonction pour tester les différences de valeurs min/max
def test_value_ranges(reference_data, new_data):
    """Vérifie que les nouvelles données sont dans les plages acceptables"""
    try:
        for col in reference_data.columns:
            if pd.api.types.is_numeric_dtype(reference_data[col]):
                ref_min, ref_max = reference_data[col].min(), reference_data[col].max()
                new_min, new_max = new_data[col].min(), new_data[col].max()
                assert new_min >= ref_min * 0.8, f"Valeurs trop basses dans {col}"
                assert new_max <= ref_max * 1.2, f"Valeurs trop hautes dans {col}"
        test_results.append({
            'test_name': 'value_ranges',
            'status': 'PASSED',
            'description': 'Valeurs dans les plages acceptables',
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
    except AssertionError as e:
        test_results.append({
            'test_name': 'value_ranges',
            'status': 'FAILED',
            'description': str(e),
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
        raise

# Fonction pour tester les valeurs manquantes
def test_missing_values(reference_data, new_data):
    """Vérifie le pourcentage de valeurs manquantes"""
    max_missing_pct = 0.1
    try:
        for col in new_data.columns:
            missing_pct = new_data[col].isnull().mean()
            assert missing_pct <= max_missing_pct
        test_results.append({
            'test_name': 'missing_values',
            'status': 'PASSED',
            'description': 'Pas de valeurs manquantes excessives',
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
    except AssertionError:
        test_results.append({
            'test_name': 'missing_values',
            'status': 'FAILED',
            'description': f'Trop de valeurs manquantes dans {col}',
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
        raise

# Fonction pour tester les distributions statistiques
def test_statistical_distribution(reference_data, new_data):
    """Compare les distributions statistiques de base"""
    try:
        numerical_columns = reference_data.select_dtypes(include=[np.number]).columns
        scaler = StandardScaler()
        for col in numerical_columns:
            ref_scaled = scaler.fit_transform(reference_data[col].values.reshape(-1, 1))
            new_scaled = scaler.transform(new_data[col].values.reshape(-1, 1))
            ref_mean = np.mean(ref_scaled)
            new_mean = np.mean(new_scaled)
            assert abs(ref_mean - new_mean) < 0.5
        test_results.append({
            'test_name': 'statistical_distribution',
            'status': 'PASSED',
            'description': 'Distributions statistiques similaires',
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
    except AssertionError:
        test_results.append({
            'test_name': 'statistical_distribution',
            'status': 'FAILED',
            'description': f'Distribution trop différente pour {col}',
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
        raise

# Fonction de résumé
def pytest_terminal_summary(terminalreporter, exitstatus, config):
    print("\n============================================")
    print("DÉBUT DE LA CRÉATION DU RAPPORT")
    print(f"Nombre de résultats capturés : {len(test_results)}")
    save_test_results()
    print("FIN DE LA CRÉATION DU RAPPORT")
    print("============================================\n")