# 🏟️ Matchdex Bot

A Discord bot that lets users collect, trade, and battle with football player cards. Built with **Django** (database backend + admin panel) and **discord.py** (bot logic).

---

## 📋 Table of Contents

- [Features](#-features)
- [Tech Stack](#-tech-stack)
- [Prerequisites](#-prerequisites)
- [Installation & Setup](#-installation--setup)
- [Running the Bot](#-running-the-bot)
- [Running the Admin Panel](#-running-the-admin-panel)
- [Discord Bot Setup](#-discord-bot-setup)
- [Bot Commands Reference](#-bot-commands-reference)
- [Project Structure](#-project-structure)
- [Adding Cards](#-adding-cards)
- [Troubleshooting](#-troubleshooting)

---

## ✨ Features

- **Card Spawning** – Cards spawn automatically in a configured channel every 15 minutes
- **Pack System** – Daily, Weekly, Event, and Premium packs with cooldowns
- **Collection** – Browse and view your card collection with pagination
- **Team Building** – Build an 11-player lineup with formations (4-3-3, 4-4-2, etc.)
- **Match System** – Challenge other players to card-based matches
- **Leaderboard** – Server-wide and global leaderboards
- **Promo Codes** – Redeemable codes for points and packs
- **Django Admin Panel** – Full web-based admin for managing cards, users, and settings
- **Auto OVR & Rarity** – Card ratings and rarities calculated automatically on save

---

## 🛠️ Tech Stack

| Technology     | Purpose                    |
| -------------- | -------------------------- |
| Python 3.10+   | Core language              |
| Django 5.2     | Database backend & admin   |
| discord.py 2.x | Discord bot framework      |
| Pillow         | Dynamic card image generation |
| SQLite         | Database (default)         |
| python-dotenv  | Environment variable management |

---

## 📦 Prerequisites

Before you begin, make sure you have:

1. **Python 3.10 or higher** installed ([Download Python](https://www.python.org/downloads/))
2. **A Discord Bot Token** ([Discord Developer Portal](https://discord.com/developers/applications))
3. **Git** (optional, for cloning)

### Getting a Discord Bot Token

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click **"New Application"** → give it a name → click **Create**
3. Go to the **Bot** tab on the left sidebar
4. Click **"Reset Token"** → copy the token (you'll need it later)
5. Under **Privileged Gateway Intents**, enable:
   - ✅ **Message Content Intent**
   - ✅ **Server Members Intent**
6. Go to **OAuth2** → **URL Generator**:
   - Check **bot** and **applications.commands** under Scopes
   - Under Bot Permissions, check: **Send Messages**, **Embed Links**, **Attach Files**, **Read Message History**, **Use Slash Commands**
7. Copy the generated URL and open it in your browser to invite the bot to your server

---

## 🚀 Installation & Setup

### Step 1: Clone or Download the Project

```bash
git clone <your-repo-url> matchdex-bot
cd matchdex-bot
```

Or download and extract the ZIP file, then `cd` into the `matchdex-bot` folder.

### Step 2: Create a Virtual Environment

```bash
# Windows (use the py launcher)
py -3.11 -m venv venv
venv\Scripts\activate

# macOS / Linux
python3 -m venv venv
source venv/bin/activate
```

> ⚠️ You should see `(venv)` in your terminal prompt after activation.

### Step 3: Install Dependencies

```bash
py -3.11 -m pip install -r requirements.txt
```

### Step 4: Configure Environment Variables

Edit the `.env` file in the project root:

```env
DISCORD_BOT_TOKEN=your_actual_bot_token_here
DATABASE_URL=sqlite:///db.sqlite3
REDIS_URL=redis://localhost:6379/0
```

> 🔑 Replace `your_actual_bot_token_here` with the token you copied from the Discord Developer Portal.

### Step 5: Set Up the Database

```bash
py -3.11 manage.py migrate
```

### Step 6: Create a Superuser (for Admin Panel)

```bash
py -3.11 manage.py createsuperuser
```

Follow the prompts to set a username, email, and password.

---

## ▶️ Running the Bot

With your virtual environment activated and `.env` configured:

```bash
py -3.11 manage.py run_bot
```

You should see output like:

```
Matchdex Bot logged in as YourBot#1234 (ID: 123456789)
------
Synced slash commands for YourBot#1234
```

> 💡 The bot must be running for Discord commands to work. Keep this terminal open.

### Stopping the Bot

Press `Ctrl+C` in the terminal to stop the bot.

---

## 🖥️ Running the Admin Panel

The Django Admin Panel lets you manage cards, users, promo codes, and server settings through a web browser.

In a **separate terminal** (with the virtual environment activated):

```bash
py -3.11 manage.py runserver
```

Then open your browser and go to: **http://127.0.0.1:8000/admin**

Log in with the superuser credentials you created in Step 6.

> 📝 The admin panel and the bot can run simultaneously — use two separate terminal windows.

---

## 🤖 Discord Bot Setup

After the bot is running and invited to your server, you need to configure a spawn channel:

1. In your Discord server, use the command:
   ```
   /configure channel:#your-channel
   ```
   *(Requires Manage Server permission)*

2. Cards will start spawning in that channel every 15 minutes automatically.

---

## 📖 Bot Commands Reference

### 🎒 Packs
| Command           | Description                               | Cooldown  |
| ----------------- | ----------------------------------------- | --------- |
| `/pack_daily`     | Open a Base card pack                     | 1 day     |
| `/pack_weekly`    | Open an Icon card pack                    | 7 days    |
| `/pack_event`     | Open an Event card pack                   | 7 days    |
| `/pack_premium`   | Open an Icon/Event pack (Premium only)    | 2 days    |

### ⚽ Team Management
| Command                              | Description                           |
| ------------------------------------ | ------------------------------------- |
| `/start`                             | Initialize your team and first lineup |
| `/select_lineup formation:<choice>`  | Pick a formation for your team        |
| `/player_add position_slot:<slot> player_search:<name>` | Add a card to a lineup slot |

### 📊 Social
| Command                        | Description                      |
| ------------------------------ | -------------------------------- |
| `/help`                        | Show all available commands      |
| `/stats`                       | View your stats (W/L/D, points)  |
| `/collection`                  | Browse your card collection      |
| `/leaderboard scope:<choice>`  | View server or global leaderboard|

### ⚔️ Match
| Command                              | Description                        |
| ------------------------------------ | ---------------------------------- |
| `/match_start opponent:<@user>`      | Challenge another player to a match|

### 🔧 Admin (Bot Owner Only)
| Command                                             | Description                              |
| --------------------------------------------------- | ---------------------------------------- |
| `/configure channel:<#channel>`                     | Set the card spawn channel               |
| `/admin_spawn`                                      | Spawn 15 catchable cards in the channel  |
| `/admin_spawn_card player_name:<name>`              | Spawn a specific player card by name     |
| `/give user:<@user> player_search:<name>`           | Give a specific card to a user           |
| `/give_full user:<@user>`                           | Give all non-premium cards to a user     |

### 🎫 Promo
| Command               | Description                |
| --------------------- | -------------------------- |
| `/promo code:<code>`  | Redeem a promotional code  |

---

## 📁 Project Structure

```
matchdex-bot/
├── .env                          # Environment variables (bot token, etc.)
├── manage.py                     # Django management CLI
├── requirements.txt              # Python dependencies
├── db.sqlite3                    # SQLite database
│
├── matchdex_web/                 # Django project settings
│   ├── settings.py               # Main configuration
│   ├── urls.py                   # URL routing (admin + media)
│   ├── asgi.py
│   └── wsgi.py
│
├── core/                         # Main Django app
│   ├── models.py                 # Database models (DiscordUser, CardTemplate, etc.)
│   ├── admin.py                  # Django admin registration
│   ├── utils.py                  # Card generation, rarity logic
│   ├── apps.py
│   ├── views.py
│   │
│   ├── bot_logic/                # Discord bot cogs
│   │   ├── GeneralCog.py         # /help, /stats, /collection, /leaderboard
│   │   ├── SpawningCog.py        # Auto card spawning + catch button
│   │   ├── PackCog.py            # /pack_daily, /pack_weekly, /pack_event, /promo
│   │   ├── TeamCog.py            # /start, /select_lineup, /player_add
│   │   ├── MatchCog.py           # /match_start (full match system)
│   │   └── AdminCog.py           # /configure, /admin_spawn, /give, /give_full
│   │
│   ├── management/
│   │   └── commands/
│   │       └── run_bot.py        # Django management command to start the bot
│   │
│   └── migrations/               # Database migrations
│
└── media/                        # Media files
    ├── card_templates/           # Template images for cards
    └── generated_cards/          # Dynamically generated card images
```

---

## 🃏 Adding Cards

### Via Django Admin Panel

1. Go to **http://127.0.0.1:8000/admin** and log in
2. Click **Card Templates** → **Add Card Template**
3. Fill in: Name, Position, Attack Stat, Defence Stat, Club, Card Type
4. Click **Save** — OVR and Rarity are calculated automatically!

### Via CSV Import (Bulk)

```bash
py -3.11 manage.py import_cards your_cards.csv
```

CSV format: `Name, Position, Attack, Defence, Event, Club, Type`

---

## 🔧 Troubleshooting

### "DISCORD_BOT_TOKEN not found"
- Make sure your `.env` file exists in the project root
- Ensure the token is set: `DISCORD_BOT_TOKEN=your_token_here`
- Don't wrap the token in quotes

### Bot is online but slash commands don't appear
- Wait 1-2 minutes for Discord to sync commands globally
- Try restarting the bot
- Make sure the bot was invited with the `applications.commands` scope

### "No cards available in this pack category yet!"
- You need to add cards via the Admin Panel first
- Go to `/admin` → **Card Templates** → **Add Card Template**

### Migration errors
```bash
py -3.11 manage.py makemigrations
py -3.11 manage.py migrate
```

### Virtual environment issues
```bash
# Recreate the virtual environment
py -3.11 -m venv venv --clear
venv\Scripts\activate
py -3.11 -m pip install -r requirements.txt
```

---

## 📄 License

This project is for personal/educational use.
