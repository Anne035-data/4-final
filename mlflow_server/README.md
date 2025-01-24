---
title: Mlflow Server
emoji: 📈
colorFrom: pink
colorTo: green
sdk: docker
pinned: false
license: apache-2.0
---
# MLflow Tracking Server

MLflow tracking server for Forest Cover Type prediction project.

## Configuration
- Backend store: NeonDB
- Artifact store: AWS S3
- Port: 7860 (Hugging Face standard)

## Environment Variables Required
- DB_STORE_URI: NeonDB connection URL
- ARTIFACT_STORE_URI: S3 bucket path
- AWS_ACCESS_KEY_ID: AWS credentials
- AWS_SECRET_ACCESS_KEY: AWS credentials

## Usage
This server is used as part of a larger MLOps pipeline for tracking:
- Model parameters
- Training metrics
- Model artifacts
- Experiment tracking
Check out the configuration reference at https://huggingface.co/docs/hub/spaces-config-reference
