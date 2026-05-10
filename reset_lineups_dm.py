import os
import django
import asyncio
from dotenv import load_dotenv
import discord

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "matchdex_web.settings")
django.setup()

from core.models import Lineup, DiscordUser
from asgiref.sync import sync_to_async

async def reset_lineups():
    print("Resetting lineups in database...")
    # Update is sync, run it safely inside async wrapper
    def do_update():
        Lineup.objects.all().update(
            gk=None, df1=None, df2=None, df3=None, df4=None, df5=None,
            md1=None, md2=None, md3=None, md4=None, md5=None,
            at1=None, at2=None, at3=None, sub1=None, sub2=None, sub3=None
        )
    await sync_to_async(do_update)()
    print("Lineups cleared.")

async def main():
    await reset_lineups()

    load_dotenv()
    TOKEN = os.getenv("DISCORD_BOT_TOKEN")

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        print(f"Logged in as {client.user}")
        count = 0
        
        @sync_to_async
        def get_users():
            return list(DiscordUser.objects.all())
            
        users = await get_users()
        
        for db_user in users:
            try:
                user = await client.fetch_user(db_user.discord_id)
                await user.send(
                    "Hey Manager! ⚽ MatchDex just received an update to its squad building rules.\n\n"
                    "We've cleared your current lineup because two players with the exact same name can no longer be in the same squad (even if they're different event cards). "
                    "Also, the `/team_auto` command has been upgraded—it now automatically replaces your entire 11 with the best possible cards instead of just filling empty slots!\n\n"
                    "Please head back to your server and run `/team_auto` to instantly rebuild your strongest squad."
                )
                print(f"DM sent to {user.name}")
                count += 1
            except Exception as e:
                print(f"Could not DM {db_user.username}: {e}")
        
        print(f"Finished sending {count} DMs.")
        await client.close()

    print("Starting client...")
    await client.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
