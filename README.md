# 🌲 Forest Cover Type MLOps Pipeline

![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)
![MLflow](https://img.shields.io/badge/MLflow-2.15.0-yellow.svg)
![Airflow](https://img.shields.io/badge/Airflow-2.10.2-red.svg)
![Docker](https://img.shields.io/badge/Docker-Latest-blue.svg)
![Jenkins](https://img.shields.io/badge/Jenkins-LTS-red.svg)
![Streamlit](https://img.shields.io/badge/Streamlit-1.22.0-red.svg)
![Evidently](https://img.shields.io/badge/Evidently-0.4.17-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.109.0-blue.svg)

## 📋 Overview

Complete MLOps platform for Forest Cover Type prediction, integrating DevOps best practices for deploying and maintaining AI models in production. The system includes a comprehensive pipeline for data drift detection, automated testing, retraining, and continuous deployment.

The dataset used comes from [UCI Machine Learning Repository - Forest Cover Type](https://archive.ics.uci.edu/ml/datasets/covertype), which contains cartographic information to predict forest cover type in the Roosevelt National Forest in Colorado.

### Project Team / Romain, Kevin and Anne


## 🏗️ Architecture

![Architecture](architecture.jpg)

The complete architecture of the project is built around the following components:

### Storage and Databases
- **AWS S3**: Storage for data, models, and reports
- **NeonDB (PostgreSQL)**: Backend database for MLflow and metadata

### Model Tracking and Management
- **MLflow**: Tracking, registry, and versioning of models
  - Deployed on HuggingFace for permanent access

### Orchestration and Automation
- **Apache Airflow**: Orchestration of drift detection and retraining workflows
  - Main DAG: Drift detection and notification
  - Secondary DAG: Analysis of secondary columns
  - Environment DAG: Infrastructure verification

### CI/CD and Testing
- **Jenkins**: Continuous integration and deployment
  - `test` pipeline: Validation of incoming data
  - `retrain` pipeline: Model retraining
  - `environment` pipeline: Infrastructure integrity verification

### Monitoring and Analytics
- **Evidently.ai**: Data drift analysis and detection
- **Streamlit**: Visualization and control dashboard
  - Data drift history
  - Model performance metrics
  - Environment status

### APIs and Interfaces
- **FastAPI**: API for data generation and testing

## 📦 Project Structure

```
s3://bucket_name/covertype/
├── model_columns_logs/           # Logs of main columns analysis
├── model_columns_reports/        # Drift reports for main columns
├── models/                       # Trained models
│   └── forest_cover_type_model.pkl  # Versioned model
├── new_data/                     # New data to analyze and Archives
│   └── covtype.csv               # Active data (generated or restored)
│   └── covtype_drift_*.csv       # Archives of data with drift
│   └── covtype_*.csv             # Archives of previously generated data
├── reference/                    # Reference data
│   └── covtype_80.csv            # Training data (80%)
├── secondary_columns_logs/       # Logs of secondary columns analysis
├── secondary_columns_reports/    # Drift reports for secondary columns
└── test_reports/                 # Jenkins test reports
    └── test_report_*.csv         # Test results
```

## 🚀 Quick Start

### Prerequisites

- Docker and Docker Compose
- AWS (S3), NeonDB, HuggingFace, Evidently.ai accounts

### Configuration

1. **Clone the repository**

```bash
git clone https://github.com/Anne035-data/4-final.git
cd 4-final
```

2. **Configure environment files**

Create a `.env` file at the root with the following variables:

```
# System configuration
AIRFLOW_UID=50000

# Service URLs
AIRFLOW_API_URL=http://airflow-webserver:8080
MLFLOW_TRACKING_URI=https://XXXXX.hf.space
JENKINS_URL=http://jenkins:8080
JENKINS_OPTS="--prefix=/jenkins"
JENKINS_HOME=/var/jenkins_home

# S3 Configuration
S3_BUCKET=4-final-project

# Default users
AIRFLOW_USERNAME=XXX
JENKINS_ADMIN_ID=XXX

# MLflow Configuration
MLFLOW_DEFAULT_ARTIFACT_ROOT=XXXX

# NeonDB Database
NEON_DATABASE_URL=postgresql://user:password@host:port/database
DB_STORE_URI=postgresql://user:password@host:port/database

# Evidently Configuration
EVIDENTLY_CLOUD_PROJECT_ID=project_id
```

Create a `.secrets` file at the root with:

```
# AWS Credentials
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
AWS_DEFAULT_REGION=eu-west-3

# S3 URI
ARTIFACT_STORE_URI=s3://4-final-project

# Email credentials
EMAIL_USER=your_email@gmail.com
EMAIL_PASSWORD=your_app_password
gmail_password=your_app_password
EMAIL_TO=destination@gmail.com

# Service passwords
AIRFLOW_PASSWORD=XXXXX
_AIRFLOW_WWW_USER_PASSWORD=XXXXX
JENKINS_ADMIN_PASSWORD=XXXXX
EVIDENTLY_CLOUD_TOKEN=your_token
```

3. **Configure credentials in Jenkins**
   - AWS_ACCESS_KEY_ID
   - AWS_SECRET_ACCESS_KEY
   - NEON_DATABASE_URL
   - S3_BUCKET
   - MLFLOW_TRACKING_URI
   - EVIDENTLY_CLOUD_TOKEN

4. **Configure variables in Airflow**
   - AWS_ACCESS_KEY_ID 
   - AWS_SECRET_ACCESS_KEY
   - S3_BUCKET
   - EVIDENTLY_CLOUD_PROJECT_ID
   - EVIDENTLY_CLOUD_TOKEN
   - JENKINS_USER
   - JENKINS_TOKEN
   - gmail_password

5. **Configure secrets in HuggingFace**
   - ARTIFACT_STORE_URI
   - AWS_ACCESS_KEY_ID
   - AWS_SECRET_ACCESS_KEY
   - DB_STORE_URI (= NEON_DATABASE_URL)
   - PORT (7860)

### Starting Services

```bash
docker-compose build
docker-compose up -d
```

### Accessing Interfaces

- **Airflow**: http://localhost:8080 (user/password)
- **Streamlit**: http://localhost:8501
- **FastAPI**: http://localhost:8000
- **Jenkins**: http://localhost:8088 (admin/password)
- **MLflow**: https://anneformation-mlflow-final-project.hf.space

## 🔄 Pipeline Workflow

### 1. Data Generation / Ingestion

- Via FastAPI (http://localhost:8000):
  - Generation of normal or drift data for testing
  - Restoration of historical files

### 2. Drift Detection

- Manual triggering via Streamlit or automatic (scheduled)
- Preliminary data validation test by Jenkins
- Two-phase drift analysis:
  - Main columns (model-related)
  - Secondary columns (complementary monitoring)
- Report generation in S3 and Evidently.ai
- Email notification of drift status

### 3. Retraining (if drift detected)

- Automatic triggering of Jenkins `retrain` pipeline
- Loading new data from S3
- RandomForest model retraining
- Tracking metrics (accuracy, F1 score) in MLflow
- Saving the new model in S3 and MLflow

### 4. Continuous Monitoring

- Streamlit dashboard for visualization:
  - Drift analysis history
  - Model performance over time
  - Environment status

## 📊 Model and Data

**Dataset**: Forest Cover Type (UCI Machine Learning Repository)
- 581,012 observations, 54 attributes, 7 forest cover types
- Split:
  - Training: 80% (`covtype_80.csv`)
  - Test: 20% (`covtype_20.csv`)
  - Generated data for tests: Random samples and simulated drift

**Model**: RandomForest
- Metrics: Accuracy (~94%), F1 score (~90%)
- Versioning via MLflow
- Artifact storage in S3

## 🔍 Monitoring Features

### Data Drift Detection
- Monitoring of main model columns (weekly)
- Monitoring of secondary columns (monthly)
- Configurable thresholds per column

### Automated Tests
- Column structure
- Missing values
- Aberrant values

### Streamlit Dashboard
- Recent drift analysis
- Model performance tracking
- Infrastructure verification

## 🧰 Tools and Technologies

- **Python**: Main language
- **Docker & Docker Compose**: Containerization
- **AWS S3**: Data storage
- **MLflow**: Model tracking and registry
- **Apache Airflow**: Orchestration
- **Jenkins**: CI/CD
- **Evidently.ai**: Drift detection
- **Streamlit**: Dashboard
- **FastAPI**: Data generation
- **NeonDB**: Managed PostgreSQL database

## 📝 Implementation Notes

- The architecture is fully dockerized for easy deployment
- Jenkins uses a Docker-out-of-Docker configuration to execute pipelines
- The MLflow server is hosted on HuggingFace for permanent availability
- Email notifications are configured via Gmail (requires an application password)
- Airflow DAGs are configured to run at regular intervals and can also be triggered manually via the Streamlit dashboard

## 🔒 Security

- Credentials are stored in `.env` and `.secrets` files (not versioned)
- Sensitive variables defined in Jenkins, Airflow, and HuggingFace
- Secure access to S3 via IAM

## 📹 Vidéos du Projet

### 📌 Présentation Synthétique (Recommandée)
[Présentation du Projet en 5 minutes](https://www.loom.com/share/9a7f59043092462489c48a6391eaf361?sid=a11c290e-585c-4c15-8293-7a52b6abf787)
*Une présentation claire et concise du projet en 5 minutes - idéale pour une première découverte*

### Documentation Technique Détaillée
- [Partie 1: Architecture et Modèle](https://www.loom.com/share/aad8b5869d46488a90035f431ab1c601?sid=36d1a972-7dfb400d-9861-99f401c28ee0) - Explication détaillée de l'architecture et du modèle
- [Partie 2: Démonstration en Action](https://www.loom.com/share/6e2ec0c0c7d04fa3958ae8285e77c633?sid=aff6c926-236f4303-896a-83aad0eb7b68) - Le système en production avec explications
