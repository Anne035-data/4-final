import boto3
import psycopg2
import os
import sys
import json
import requests
import time

def check_required_env_vars():
    """Vérifier la présence des variables d'environnement critiques"""
    print("🔍 Vérification des variables d'environnement critiques")
    
    critical_vars = ['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'NEON_DATABASE_URL', 'S3_BUCKET']
    missing_vars = [var for var in critical_vars if not os.environ.get(var)]
    
    if missing_vars:
        print('⚠️ Variables manquantes:', ', '.join(missing_vars))
        return False
    else:
        print('✅ Toutes les variables critiques sont définies')
        return True

def check_s3_connection():
    """Tester la connexion au bucket S3"""
    print('🔍 Test de connexion au bucket S3')
    
    bucket_name = os.environ['S3_BUCKET']
    
    try:
        s3 = boto3.client(
            's3',
            region_name=os.environ.get('AWS_DEFAULT_REGION', 'eu-west-3')
        )
        # Test d'accès au bucket
        response = s3.list_objects_v2(Bucket=bucket_name, MaxKeys=1)
        print('✅ Connexion S3 établie avec succès')
        
        # Vérifier les dossiers importants
        folders_to_check = [
            'covertype/reference/',
            'covertype/models/',
            'covertype/new_data/',
            'covertype/model_columns_logs/',
            'covertype/model_columns_reports/',
            'covertype/secondary_columns_logs/',
            'covertype/secondary_columns_reports/',
            'covertype/test_reports/'
        ]
        all_ok = True
        
        for folder in folders_to_check:
            try:
                folder_check = s3.list_objects_v2(Bucket=bucket_name, Prefix=folder, MaxKeys=1)
                if 'Contents' in folder_check:
                    print(f'✅ Dossier {folder} accessible')
                else:
                    print(f'⚠️ Dossier {folder} existe mais semble vide')
            except Exception as e:
                print(f'⚠️ Erreur lors de l\'accès au dossier {folder}: {str(e)}')
                all_ok = False
                
        return all_ok
    except Exception as e:
        print(f'❌ Échec de connexion S3: {str(e)}')
        return False

def check_neondb_connection():
    """Tester la connexion à NeonDB"""
    print('🔍 Test de connexion à NeonDB...')
    
    connection_timeout = 10  # secondes
    
    try:
        start_time = time.time()
        
        # Extraire l'URL sans paramètres additionnels
        db_url = os.environ['NEON_DATABASE_URL']
        
        # Tentative de connexion
        conn = psycopg2.connect(db_url, connect_timeout=connection_timeout)
        
        print(f'✅ Connexion à NeonDB établie en {time.time() - start_time:.2f} secondes')
        
        # Vérifier si les tables MLflow existent
        cursor = conn.cursor()
        cursor.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'experiments'
            );
        """)
        
        mlflow_tables_exist = cursor.fetchone()[0]
        if mlflow_tables_exist:
            print('✅ Tables MLflow détectées dans la base de données')
        else:
            print('⚠️ Tables MLflow non détectées - MLflow pourrait ne pas être correctement configuré')
        
        conn.close()
        return True
    except Exception as e:
        print(f'❌ Échec de connexion à NeonDB: {str(e)}')
        return False

def check_data_flow():
    """Vérifier que le flux de données complet est en place"""
    print('🔍 Vérification du flux de données complet...')
    
    success = True
    errors = []
    
    # Vérifier le fichier de référence
    try:
        s3 = boto3.client('s3')
        ref_file = s3.head_object(
            Bucket=os.environ['S3_BUCKET'], 
            Key='covertype/reference/covtype_80.csv'
        )
        print('✅ Fichier de référence trouvé dans S3')
    except Exception as e:
        print(f'❌ Fichier de référence non trouvé: {str(e)}')
        success = False
        errors.append(f'Fichier de référence: {str(e)}')
    
    # Vérifier le modèle sauvegardé
    try:
        model_objects = s3.list_objects_v2(
            Bucket=os.environ['S3_BUCKET'], 
            Prefix='covertype/models/',
            MaxKeys=5
        )
        
        if 'Contents' in model_objects and len(model_objects['Contents']) > 0:
            print(f'✅ {len(model_objects["Contents"])} fichiers modèle trouvés dans S3')
        else:
            print('⚠️ Aucun modèle trouvé dans S3')
            success = False
            errors.append('Aucun modèle trouvé')
    except Exception as e:
        print(f'❌ Erreur lors de la vérification des modèles: {str(e)}')
        success = False
        errors.append(f'Vérification modèle: {str(e)}')
    
    if not success:
        print(f'❌ Le flux de données présente des erreurs: {errors}')
    else:
        print('✅ Flux de données complet vérifié avec succès')
    
    return success

def main():
    """Fonction principale exécutant tous les contrôles"""
    print("🚀 Démarrage des contrôles d'environnement...")
    
    all_checks = [
        check_required_env_vars(),
        check_s3_connection(),
        check_neondb_connection(),
        check_data_flow()
    ]
    
    if all(all_checks):
        print("✅ Tous les contrôles ont réussi!")
        sys.exit(0)
    else:
        print("❌ Certains contrôles ont échoué, veuillez vérifier les logs.")
        sys.exit(1)

if __name__ == "__main__":
    main()