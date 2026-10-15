# Matchdex Admin Guide: Adding New Cards

Welcome to the Matchdex administration guide! Since Matchdex is built using the **Django Web Framework** for its database backend, adding new cards is incredibly simple. You don't need to write any new Python code or edit configuration files to add new players to the game. 

Instead, you will use the built-in **Django Admin Panel**, a secure, graphical web interface that comes pre-configured with the bot.

## How to Access the Admin Panel

1. While the system (bot + backend) is running locally or on your server, open your web browser.
2. Navigate to your local server address (usually `http://127.0.0.1:8000/admin` or your secure domain).
3. Log in using your Superuser credentials (created during the initial project setup).

## How to Add a Single New Card

1. On the Admin Dashboard, look for the **Matchdex App** section.
2. Click on **Card Templates**. This will show you a list of all existing cards in the game.
3. Click the **"Add Card Template"** button in the top right corner.
4. Fill out the following fields in the form:
   - **Name**: The player's name (e.g., "Mohamed Salah").
   - **Position**: Their real-life position (e.g., "RW").
   - **Attack Stat**: A number between 1-99 for their attack capabilities.
   - **Defence Stat**: A number between 1-99 for their defensive capabilities.
   - **Event**: The name of the event they belong to (e.g., "TOTY", or leave blank for "Base").
   - **Club**: The player's real-world club (e.g., "Liverpool").
   - **Card Type**: Select from a dropdown (Base, Icon, Event, Premium).
5. **Click Save.**

### What Happens Automatically (The Magic)

You **do not** need to manually calculate the player's Overall Rating (OVR) or their Game Rarity (Common, Epic, etc.). 

When you click "Save", the Django backend automatically triggers a hidden script we've built into the system:
- **OVR Calculation**: It looks at the Attack and Defence stats, picks the highest one, and sets that as the exact OVR.
- **Rarity Assignment**: It checks the OVR against the specific rarity ranges we set up for that `Card Type` (e.g., if it's an Icon card with a 91 OVR, the script instantly locks its rarity to "Epic").

Once saved, the card is *immediately* available in the bot. It will start spawning in Discord chat (based on its new rarity drop rate) and can be pulled from `/pack` commands instantly without restarting the bot.

## How to Add Cards in Bulk (Optional)

If you are beginning a massive new event and want to add 50 new cards at once without clicking through a web form 50 times, we can use a basic CSV or JSON upload script (to be provided with the final source code). 

You would simply ensure your spreadsheet has the columns `Name, Position, Attack, Defence, Event, Club, Type` and run a single command in your server terminal:

```bash
python manage.py import_cards new_event_cards.csv
```

All 50 cards will be processed, OVRs automatically calculated, and injected into the Discord economy in seconds.

## Frequently Asked Questions

**Q: If I change a card's stats later, does it affect people who already own the card?**
No. Because players own a `UserCard` which links back to the central `CardTemplate`, if you buff a card's stats from 88 to 90 via the Admin Panel, every user who owns that card will instantly see the new 90 OVR the next time they play a match. The update applies globally and immediately.

**Q: Do I need to upload an image for every card?**
No! Because we are using the dynamic `Pillow` image generator, you only need to provide the base empty PNG templates for each Rarity type *once* during setup. Every time a user types `/show Mohamed Salah`, the bot grabs the data from your database and paints his Name, OVR, and Stats onto the correct template on the fly.
