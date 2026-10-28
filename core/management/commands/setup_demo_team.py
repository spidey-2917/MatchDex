import random
from django.core.management.base import BaseCommand
from core.models import CardTemplate, DiscordUser, UserCard, Lineup

DEMO_PLAYERS = [
    # Team 1: Real Madrid Archetype (11 players)
    {"name": "Thibaut Courtois", "pos": "GK", "club": "Real Madrid", "atk": (15, 25), "df": (87, 92), "rarity": "Legendary I", "slot": "gk"},
    {"name": "Dani Carvajal", "pos": "RB", "club": "Real Madrid", "atk": (72, 80), "df": (82, 88), "rarity": "Elite I", "slot": "df1"},
    {"name": "Antonio Rüdiger", "pos": "CB", "club": "Real Madrid", "atk": (58, 68), "df": (86, 91), "rarity": "Epic I", "slot": "df2"},
    {"name": "Éder Militão", "pos": "CB", "club": "Real Madrid", "atk": (54, 65), "df": (84, 89), "rarity": "Elite II", "slot": "df3"},
    {"name": "Ferland Mendy", "pos": "LB", "club": "Real Madrid", "atk": (68, 77), "df": (82, 87), "rarity": "Elite II", "slot": "df4"},
    {"name": "Federico Valverde", "pos": "CM", "club": "Real Madrid", "atk": (83, 89), "df": (81, 87), "rarity": "Epic I", "slot": "md1"},
    {"name": "Aurélien Tchouaméni", "pos": "CDM", "club": "Real Madrid", "atk": (70, 78), "df": (84, 90), "rarity": "Elite I", "slot": "md2"},
    {"name": "Jude Bellingham", "pos": "CAM", "club": "Real Madrid", "atk": (88, 94), "df": (77, 84), "rarity": "Legendary I", "slot": "md3"},
    {"name": "Rodrygo", "pos": "RW", "club": "Real Madrid", "atk": (86, 92), "df": (46, 56), "rarity": "Elite I", "slot": "at1"},
    {"name": "Kylian Mbappé", "pos": "ST", "club": "Real Madrid", "atk": (93, 98), "df": (36, 46), "rarity": "Legendary III", "slot": "at2"},
    {"name": "Vinícius Jr", "pos": "LW", "club": "Real Madrid", "atk": (91, 96), "df": (40, 50), "rarity": "Legendary II", "slot": "at3"},

    # Team 2: Liverpool Archetype (11 players)
    {"name": "Alisson Becker", "pos": "GK", "club": "Liverpool", "atk": (15, 25), "df": (87, 92), "rarity": "Legendary I", "slot": "gk"},
    {"name": "Trent Alexander-Arnold", "pos": "RB", "club": "Liverpool", "atk": (79, 87), "df": (76, 83), "rarity": "Elite I", "slot": "df1"},
    {"name": "Virgil van Dijk", "pos": "CB", "club": "Liverpool", "atk": (58, 68), "df": (89, 94), "rarity": "Legendary II", "slot": "df2"},
    {"name": "Ibrahima Konaté", "pos": "CB", "club": "Liverpool", "atk": (52, 63), "df": (83, 89), "rarity": "Elite II", "slot": "df3"},
    {"name": "Andy Robertson", "pos": "LB", "club": "Liverpool", "atk": (73, 82), "df": (80, 86), "rarity": "Elite I", "slot": "df4"},
    {"name": "Alexis Mac Allister", "pos": "CM", "club": "Liverpool", "atk": (81, 87), "df": (78, 84), "rarity": "Elite I", "slot": "md1"},
    {"name": "Ryan Gravenberch", "pos": "CDM", "club": "Liverpool", "atk": (76, 83), "df": (80, 86), "rarity": "Elite II", "slot": "md2"},
    {"name": "Dominik Szoboszlai", "pos": "CAM", "club": "Liverpool", "atk": (84, 90), "df": (71, 79), "rarity": "Elite I", "slot": "md3"},
    {"name": "Mohamed Salah", "pos": "RW", "club": "Liverpool", "atk": (91, 96), "df": (44, 54), "rarity": "Legendary II", "slot": "at1"},
    {"name": "Darwin Núñez", "pos": "ST", "club": "Liverpool", "atk": (84, 90), "df": (42, 52), "rarity": "Elite II", "slot": "at2"},
    {"name": "Luis Díaz", "pos": "LW", "club": "Liverpool", "atk": (86, 92), "df": (46, 56), "rarity": "Elite I", "slot": "at3"},
]


class Command(BaseCommand):
    help = "Seeds 22 demo players with randomized stats and optionally sets up an active lineup for a Discord user ID"

    def add_arguments(self, parser):
        parser.add_argument("--user-id", type=int, help="Optional Discord User ID to assign cards and build a ready lineup for")
        parser.add_argument("--team", type=str, choices=["real", "liverpool", "all"], default="all", help="Which team's cards to give to user")

    def handle(self, *args, **options):
        # 1. Create/Update the 22 CardTemplate records
        templates_map = {}
        for p in DEMO_PLAYERS:
            atk = random.randint(*p["atk"])
            df = random.randint(*p["df"])
            ovr = max(atk, df)

            template, _ = CardTemplate.objects.update_or_create(
                name=p["name"],
                club=p["club"],
                defaults={
                    "position": p["pos"],
                    "attack_stat": atk,
                    "defence_stat": df,
                    "ovr": ovr,
                    "rarity": p["rarity"],
                    "event_name": "Base",
                    "card_type": "BASE",
                }
            )
            templates_map[p["name"]] = (template, p["slot"], p["club"])

        self.stdout.write(self.style.SUCCESS(f"Successfully seeded {len(templates_map)} CardTemplates!"))

        # 2. If a user ID is specified, grant cards and build active Lineup
        uid = options.get("user_id")
        if uid:
            user, _ = DiscordUser.objects.get_or_create(
                discord_id=uid,
                defaults={"username": f"User_{uid}"}
            )

            team_filter = options.get("team", "all")
            user_cards_by_slot = {}

            for p in DEMO_PLAYERS:
                t, slot, club = templates_map[p["name"]]
                
                # Check filter
                if team_filter == "real" and club != "Real Madrid":
                    continue
                if team_filter == "liverpool" and club != "Liverpool":
                    continue

                uc, _ = UserCard.objects.get_or_create(
                    owner=user,
                    template=t
                )
                if slot not in user_cards_by_slot:
                    user_cards_by_slot[slot] = uc

            # Create or update active lineup
            lineup, _ = Lineup.objects.get_or_create(
                owner=user,
                is_active=True,
                defaults={"name": "Demo Squad", "formation": "433", "tactic": "attacking"}
            )
            lineup.formation = "433"
            for slot, uc in user_cards_by_slot.items():
                setattr(lineup, slot, uc)
            lineup.save()

            self.stdout.write(self.style.SUCCESS(f"Configured full active 4-3-3 lineup for Discord User {uid}!"))
