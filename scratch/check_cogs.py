import os
import sys
import django

# Add current working directory to sys.path
sys.path.insert(0, os.getcwd())

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "matchdex_web.settings")
django.setup()

from core.settings import read_settings
read_settings()

from core.management.commands.run_bot import MatchdexBot
import asyncio

async def test_load():
    bot = MatchdexBot()
    # Mock some methods to avoid actual connection
    for package in bot.admin_ids:
        pass
    print("Trying to load packages...")
    for package in [
        "core.bot_logic.AdminCog",
        "core.bot_logic.GeneralCog",
        "core.bot_logic.GuildConfigCog",
        "core.bot_logic.InfoCog",
        "core.bot_logic.MatchCog",
        "core.bot_logic.MdSettingsCog",
        "core.bot_logic.PackCog",
        "core.bot_logic.SpawningCog",
        "core.bot_logic.TeamCog",
        "core.bot_logic.TradeCog",
        "core.bot_logic.WagerCog",
        "core.bot_logic.SBCCog",
    ]:
        try:
            await bot.load_extension(package)
            print(f"Loaded {package}")
        except Exception as e:
            print(f"Failed to load {package}: {e}")
            import traceback
            traceback.print_exc()

asyncio.run(test_load())
