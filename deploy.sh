#!/bin/bash

# Exit on any error
set -e

echo "Starting deployment process..."

# Pull the latest code (assuming it's a git repository on the VPS)
echo "Pulling latest code from git..."
git pull origin main

# Build the docker images
echo "Building new docker images..."
docker compose build

# Restart the containers
echo "Restarting containers with new images..."
docker compose up -d

echo "Running database migrations..."
docker compose exec web python manage.py migrate

echo "Deployment complete! Checking status:"
docker compose ps
