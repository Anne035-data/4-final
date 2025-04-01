
# Configuration pour la détection de dérive de données dans le dataset Forest Cover Type.


# Dictionnaire principal de configuration pour la détection de dérive
DRIFT_CONFIG = {
    # Colonnes à analyser pour la dérive
    "COLUMNS_TO_ANALYZE": [
        # Zones de nature sauvage (toutes)
        "Wilderness_Area1", "Wilderness_Area2", 
        "Wilderness_Area3", "Wilderness_Area4",
        
        # Types de sol sélectionnés (basés sur leur importance)
        "Soil_Type4", "Soil_Type7", 
        "Soil_Type10", "Soil_Type20", "Soil_Type22",
        
        # Colonnes topographiques
        "Elevation", "Aspect", "Slope", 
        "Horizontal_Distance_To_Hydrology", 
        "Vertical_Distance_To_Hydrology", 
        "Horizontal_Distance_To_Roadways",
        "Hillshade_9am", "Hillshade_Noon", "Hillshade_3pm",
        "Horizontal_Distance_To_Fire_Points",
        
        # Colonne cible
        "Cover_Type"
    ],
    
    # Seuils pour différentes métriques
    "THRESHOLDS": {
        # Seuil global de dérive pour l'ensemble du dataset 
        "dataset_drift": 1, 
        
        # Seuils spécifiques par colonne 
        "feature_drift": {
            "Elevation": 0.08, 
            "Cover_Type": 0.03,  
        },
        
        # Seuils par défaut pour les tests statistiques
        "default": {
            "feature_drift": 0.1,  
            "stattest_threshold": 0.07  
        }
    },
    
    # Mapping des types de couverture forestière (pour les rapports)
    "FOREST_COVER_TYPES": {
        1: "Épicéa / Sapin",
        2: "Pin tordu", 
        3: "Pin ponderosa",
        4: "Peuplier / Saule",
        5: "Tremble",
        6: "Sapin de Douglas",
        7: "Krummholz"
    }
}

# Fonction pour obtenir la configuration
def get_drift_config():
    """Renvoie la configuration actuelle de détection de dérive"""
    return DRIFT_CONFIG