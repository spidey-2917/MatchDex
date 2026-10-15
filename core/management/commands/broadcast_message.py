import os
import asyncio
import discord
from dotenv import load_dotenv
from django.core.management.base import BaseCommand
from asgiref.sync import sync_to_async
from core.models import DiscordUser

load_dotenv()

class Command(BaseCommand):
    help = "Send a direct message to all users registered in the DiscordUser database."

    def add_arguments(self, parser):
        parser.add_argument("message", type=str, nargs="?", default=None, help="The message to send to all users.")
        parser.add_argument(
            "--file",
            type=str,
            help="Path to a text file containing the message to send."
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show the list of target users and the message without sending any DMs."
        )
        parser.add_argument(
            "--delay",
            type=float,
            default=1.5,
            help="Delay (in seconds) between sending DMs to avoid spam flags and rate limits. Defaults to 1.5."
        )

    def handle(self, *args, **options):
        message_text = options["message"]
        file_path = options["file"]
        dry_run = options["dry_run"]
        delay = options["delay"]

        if file_path:
            if not os.path.exists(file_path):
                self.stderr.write(self.style.ERROR(f"File not found: {file_path}"))
                return
            with open(file_path, "r", encoding="utf-8") as f:
                message_text = f.read()

        if not message_text:
            self.stderr.write(self.style.ERROR("You must provide either a message argument or a --file path."))
            return
        
        self.stdout.write(self.style.WARNING("Starting broadcast script..."))
        asyncio.run(self.broadcast(message_text, dry_run, delay))

    async def broadcast(self, message_text, dry_run, delay):
        TOKEN = os.getenv("DISCORD_BOT_TOKEN")
        if not TOKEN:
            self.stderr.write(self.style.ERROR("DISCORD_BOT_TOKEN environment variable not set!"))
            return

        @sync_to_async
        def get_all_users():
            return list(DiscordUser.objects.all())

        users = await get_all_users()
        self.stdout.write(f"Found {len(users)} registered users in the database.")

        if dry_run:
            self.stdout.write(self.style.SUCCESS("\n[DRY RUN] Message to be sent:"))
            self.stdout.write(f"\"{message_text}\"")
            self.stdout.write(self.style.SUCCESS("\n[DRY RUN] Target Users list:"))
            for u in users:
                self.stdout.write(f"  - User ID: {u.discord_id} (Username: {getattr(u, 'username', 'N/A')})")
            self.stdout.write(self.style.WARNING("\nDry run completed. No messages were sent."))
            return

        intents = discord.Intents.default()
        client = discord.Client(intents=intents)

        @client.event
        async def on_ready():
            self.stdout.write(self.style.SUCCESS(f"Logged in as {client.user}"))
            success_count = 0
            fail_count = 0

            for db_user in users:
                user_id = db_user.discord_id
                username = getattr(db_user, "username", f"ID: {user_id}")
                try:
                    self.stdout.write(f"Attempting to DM {username}...")
                    user = await client.fetch_user(user_id)
                    await user.send(message_text)
                    self.stdout.write(self.style.SUCCESS(f"Successfully sent DM to {user.name}"))
                    success_count += 1
                except discord.Forbidden:
                    self.stdout.write(self.style.ERROR(f"Forbidden: DMs closed or blocked by {username}"))
                    fail_count += 1
                except discord.HTTPException as e:
                    self.stdout.write(self.style.ERROR(f"HTTP Error for {username}: {e}"))
                    fail_count += 1
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Unexpected error for {username}: {e}"))
                    fail_count += 1
                
                # Small rate-limit delay
                await asyncio.sleep(delay)

            self.stdout.write(self.style.SUCCESS(f"\nFinished broadcasting. Sent: {success_count}, Failed: {fail_count}"))
            await client.close()

        self.stdout.write("Starting Discord client...")
        try:
            await client.start(TOKEN)
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Error starting Discord client: {e}"))
