# Matchdex Bot - Production VPS Deployment Guide

This guide explains how to deploy the bot on a high-performance VPS (like Hetzner or DigitalOcean) to handle a large public userbase using PostgreSQL and Docker. 

## Prerequisites
1. **Purchase a VPS** (Ubuntu 22.04 or 24.04).
   - *Recommendation:* DigitalOcean Basic Droplet ($12/mo, 2GB RAM, 1 vCPU).
2. **Setup SSH Keys**: Ensure you have SSH access to your droplet.

## Step 1: Server Setup (Install Docker)
SSH into your VPS as your main user, and run the following command to securely install Docker and Docker Compose:
```bash
sudo curl -sSL https://get.docker.com/ | sh
sudo usermod -aG docker $USER
```
*Note: Log out and back in if running Docker commands without `sudo` gives permission errors.*

## Step 2: Download the Code
Clone your bot from GitHub:
```bash
git clone https://github.com/YOUR_USERNAME/matchdex-bot.git
cd matchdex-bot
```

## Step 3: Configure Environment Variables
Copy the example file to create your production credentials file:
```bash
cp .env.example .env
nano .env
```
Fill out the variables in the file:
- `DISCORD_BOT_TOKEN`: The token from the Discord Developer Portal.
- `POSTGRES_PASSWORD`: Make up a strong, unique password for your database.
- `DJANGO_SECRET_KEY`: Any long, random string (e.g. 50 characters).

Save and exit (`Ctrl+O`, `Enter`, `Ctrl+X`).

## Step 4: First Time Launch
Start everything (Database, Web Server, Bot) with one command. The new `docker-compose.yml` includes healthchecks to safely boot the database before the bot runs.
```bash
docker compose up -d --build
```

## Step 5: Initialize the Database (One-time only)
Because you're using a fresh PostgreSQL instance, you need to set up the database structure and create an admin account:

1. **Run Migrations**:
```bash
docker compose exec web python manage.py migrate
```

2. **Create Admin Superuser** (Follow the prompts):
```bash
docker compose exec web python manage.py createsuperuser
```

## Managing Updates
When you make changes to your bot's code and push them to GitHub, updating your VPS is extremely easy. Simply run:
```bash
chmod +x deploy.sh
./deploy.sh
```
This script will pull new code, rebuild the Docker containers, run any new database migrations, and safely restart the bot.

## Maintenance Commands
- **View Bot Logs**: `docker compose logs -f bot`
- **View Database Logs**: `docker compose logs -f db`
- **Restart the Bot Quickly**: `docker compose restart bot`
- **Stop Everything**: `docker compose down`
