import pytest
import boto3
import pandas as pd
import numpy as np
import os
from datetime import datetime

test_results = []  # Pour stocker les résultats des tests

@pytest.fixture(autouse=True, scope="session")
def handle_test_results(request):
    yield
    print("\nSauvegarde des résultats...")
    save_test_results()

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
    data_obj = s3.get_object(Bucket=bucket, Key='covertype/new_data/covtype.csv')
    return pd.read_csv(data_obj['Body'])

def save_test_results():
    """Sauvegarde les résultats dans un CSV sur S3"""
    try:
        df_results = pd.DataFrame(test_results)
        s3 = boto3.client('s3')
        bucket = os.environ['S3_BUCKET']
        key = f'covertype/test_reports/test_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
        
        csv_buffer = df_results.to_csv(index=False).encode()
        s3.put_object(Bucket=bucket, Key=key, Body=csv_buffer, ContentType='text/csv')
        print(f"Rapport sauvegardé : {key}")
    except Exception as e:
        print(f"Erreur lors de la sauvegarde du rapport: {str(e)}")

def test_column_structure(reference_data, new_data):
    """Vérifie la structure des colonnes"""
    try:
        # Liste complète des colonnes
        expected_columns = [
            # Zones de nature sauvage
            "Wilderness_Area1", "Wilderness_Area2", 
            "Wilderness_Area3", "Wilderness_Area4",
            
            # Colonnes topographiques
            "Elevation", "Aspect", "Slope", 
            "Horizontal_Distance_To_Hydrology", 
            "Vertical_Distance_To_Hydrology", 
            "Horizontal_Distance_To_Roadways",
            "Hillshade_9am", "Hillshade_Noon", "Hillshade_3pm",
            "Horizontal_Distance_To_Fire_Points",
            
            # Tous les types de sol
            "Soil_Type1", "Soil_Type2", "Soil_Type3", "Soil_Type4", 
            "Soil_Type5", "Soil_Type6", "Soil_Type7", "Soil_Type8", 
            "Soil_Type9", "Soil_Type10", "Soil_Type11", "Soil_Type12", 
            "Soil_Type13", "Soil_Type14", "Soil_Type15", "Soil_Type16", 
            "Soil_Type17", "Soil_Type18", "Soil_Type19", "Soil_Type20", 
            "Soil_Type21", "Soil_Type22", "Soil_Type23", "Soil_Type24", 
            "Soil_Type25", "Soil_Type26", "Soil_Type27", "Soil_Type28", 
            "Soil_Type29", "Soil_Type30", "Soil_Type31", "Soil_Type32", 
            "Soil_Type33", "Soil_Type34", "Soil_Type35", "Soil_Type36", 
            "Soil_Type37", "Soil_Type38", "Soil_Type39", "Soil_Type40",
            
            # Colonne cible
            "Cover_Type"
        ]

        # Vérifier que toutes les colonnes attendues sont présentes
        missing_columns = [col for col in expected_columns if col not in new_data.columns]
        extra_columns = [col for col in new_data.columns if col not in expected_columns]

        assert len(missing_columns) == 0, f"Colonnes manquantes : {missing_columns}"
        assert len(extra_columns) == 0, f"Colonnes supplémentaires non attendues : {extra_columns}"

        test_results.append({
            'test_name': 'column_structure',
            'status': 'PASSED',
            'description': 'Structure des colonnes correcte',
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
    except AssertionError as e:
        test_results.append({
            'test_name': 'column_structure',
            'status': 'FAILED',
            'description': str(e),
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
        raise

def test_missing_values(new_data):
    """Vérifie l'absence de valeurs manquantes"""
    try:
        # Vérifier l'absence de valeurs manquantes
        missing_values = new_data.isnull().sum()
        total_missing = missing_values.sum()

        assert total_missing == 0, f"Valeurs manquantes détectées : \n{missing_values}"

        test_results.append({
            'test_name': 'missing_values',
            'status': 'PASSED',
            'description': 'Aucune valeur manquante',
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
    except AssertionError as e:
        test_results.append({
            'test_name': 'missing_values',
            'status': 'FAILED',
            'description': str(e),
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
        raise

def test_statistical_distribution(reference_data, new_data):
    """Vérifie l'absence de valeurs aberrantes"""
    try:
        numerical_columns = reference_data.select_dtypes(include=[np.number]).columns
        
        for col in numerical_columns:
            # Calcul des statistiques descriptives
            ref_mean = reference_data[col].mean()
            ref_std = reference_data[col].std()
            
            new_mean = new_data[col].mean()
            new_std = new_data[col].std()
            
            # Calcul des bornes pour les valeurs aberrantes (3 écarts-types)
            lower_bound = ref_mean - 3 * ref_std
            upper_bound = ref_mean + 3 * ref_std
            
            # Vérifier les valeurs de la nouvelle donnée
            new_data_outliers = new_data[
                (new_data[col] < lower_bound) | (new_data[col] > upper_bound)
            ]
            
            assert len(new_data_outliers) == 0, \
                f"Valeurs aberrantes détectées pour {col}: " \
                f"Bornes [{lower_bound}, {upper_bound}]\n" \
                f"Valeurs aberrantes:\n{new_data_outliers[col]}"
        
        test_results.append({
            'test_name': 'statistical_distribution',
            'status': 'PASSED',
            'description': 'Aucune valeur aberrante détectée',
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
    except AssertionError as e:
        test_results.append({
            'test_name': 'statistical_distribution',
            'status': 'FAILED',
            'description': str(e),
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
        raise