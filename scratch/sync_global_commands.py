import asyncio
import os
import sys
import django
from dotenv import load_dotenv

# Setup django
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "matchdex_web.settings")
django.setup()

from core.management.commands.run_bot import MatchdexBot
from core.settings import read_settings

load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")

class SyncBot(MatchdexBot):
    async def on_ready(self):
        # Run original on_ready to load all cogs/extensions
        await super().on_ready()
        print(f"SyncBot: Cogs loaded. Syncing global commands...")
        try:
            synced = await self.tree.sync()
            print(f"Successfully synced {len(synced)} global commands.")
        except Exception as e:
            print(f"Error syncing: {e}")
        finally:
            await self.close()

async def main():
    read_settings()
    bot = SyncBot()
    await bot.start(TOKEN)

if __name__ == "__main__":
    if not TOKEN:
        print("DISCORD_BOT_TOKEN not found.")
        sys.exit(1)
    asyncio.run(main())
