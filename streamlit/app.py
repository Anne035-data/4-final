import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
import os
from dotenv import load_dotenv
from pathlib import Path
import mlflow
import boto3
from evidently.report import Report
from evidently.metric_preset import DataDriftPreset
from evidently.metrics import *

# format de la page
st.set_page_config(layout="wide")

# Chargement des variables d'environnement
parent_dir = Path(__file__).parent.parent
load_dotenv(parent_dir / '.env')
load_dotenv(parent_dir / '.secrets')

st.title("🌲 Forest Cover Type - MLOps Monitor")

# Ajout tabs
tabs = st.tabs(["📊 Dashboard"])

with tabs[0]:

    # Test results
    st.header("🧪 Test Results")

    try:
        # Configuration S3 client
        s3 = boto3.client(
            's3',
            aws_access_key_id=os.environ['AWS_ACCESS_KEY_ID'],
            aws_secret_access_key=os.environ['AWS_SECRET_ACCESS_KEY']
        )

        # Récupération dernier rapport de test sur S3
        response = s3.list_objects_v2(
            Bucket='rom1',
            Prefix='covertype/test_reports/'
        )
        latest_file = sorted([obj['Key'] for obj in response['Contents']])[-1]
        obj = s3.get_object(Bucket='rom1', Key=latest_file)
        test_df = pd.read_csv(obj['Body'])

        # mise en forme
        st.subheader("Test Summary")
        col1, col2 = st.columns([1, 2])  # ajustement des colonnes
        
        with col1:
            test_status = test_df['status'].value_counts()
            fig = px.pie(
                values=test_status.values, 
                names=test_status.index,
                title="Test Results Distribution",
                color_discrete_map={'PASSED': '#2ecc71', 'FAILED': '#e74c3c'}
            )
            st.plotly_chart(fig, use_container_width=True)
        
        with col2:
            st.subheader("Test Details")
            # Affichage des résultats des tests
            display_df = test_df[['test_name', 'status', 'description']].copy()
            display_df = display_df.style.apply(lambda x: ['background-color: #2ecc71' if v == 'PASSED' 
                                                         else 'background-color: #e74c3c' 
                                                         for v in x], subset=['status'])
            st.dataframe(display_df, use_container_width=True)

    except Exception as e:
        st.error(f"Error loading test results: {str(e)}")