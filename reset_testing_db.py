import os
import django

def reset_db():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'matchdex_web.settings')
    django.setup()

    from core.models import DiscordUser, CommandLog, ServerSettings, Blacklist, Trade

    print("Deleting all users and their associated data (cards, lineups, etc.)...")
    # Because of CASCADE, deleting DiscordUser deletes UserCard, Lineup, FavouriteCard, etc.
    DiscordUser.objects.all().delete()

    print("Deleting command logs...")
    CommandLog.objects.all().delete()

    print("Deleting server settings...")
    ServerSettings.objects.all().delete()

    print("Deleting blacklists...")
    Blacklist.objects.all().delete()
    
    print("Deleting any dangling trades...")
    Trade.objects.all().delete()

    print("=========================================")
    print("Database reset complete!")
    print("Card Templates, Promos, SBCs, and Drop Rates have been preserved.")
    print("=========================================")

if __name__ == "__main__":
    reset_db()
