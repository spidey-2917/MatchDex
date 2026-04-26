import io
import os
import random

from asgiref.sync import sync_to_async
from discord import app_commands
from django.conf import settings as django_settings
from PIL import Image, ImageDraw, ImageFont

from core.settings import settings

from .models import CardTemplate, DiscordUser, UserCard


async def player_autocomplete(interaction, current: str):
    @sync_to_async
    def get_matches():
        if current:
            qs = CardTemplate.objects.filter(name__icontains=current)[:25]
        else:
            qs = CardTemplate.objects.all()[:25]
        return [
            app_commands.Choice(name=f"{c.name} ({c.ovr} OVR)", value=c.name)
            for c in qs
        ]

    return await get_matches()


SOFIFA_POS_MAP = {
    "ST": "ST",
    "LS": "ST",
    "RS": "ST",
    "CF": "ST",
    "RW": "RW",
    "RF": "RW",
    "LW": "LW",
    "LF": "LW",
    "CAM": "CAM",
    "LAM": "CAM",
    "RAM": "CAM",
    "CM": "CM",
    "LCM": "CM",
    "RCM": "CM",
    "CDM": "CDM",
    "LDM": "CDM",
    "RDM": "CDM",
    "CB": "CB",
    "LCB": "CB",
    "RCB": "CB",
    "LB": "LB",
    "LWB": "LB",
    "RB": "RB",
    "RWB": "RB",
    "GK": "GK",
}


def map_sofifa_pos(pos_str):
    """Maps a SoFIFA position string (e.g. 'CAM') to our model's choices."""
    return SOFIFA_POS_MAP.get(pos_str.upper(), "ST")


def get_random_rarity():
    """Pick a rarity using the weights from config.yml."""
    rarities = list(settings.rarity_weights.keys())
    weights = list(settings.rarity_weights.values())
    return random.choices(rarities, weights=weights, k=1)[0]


def get_random_card_by_rarity(rarity):
    chosen_type = random.choices(["BASE", "ICON"], weights=[95, 5], k=1)[0]
    cards = CardTemplate.objects.filter(card_type=chosen_type, rarity=rarity)
    if not cards.exists():
        # Fallback to any card of that type if the requested rarity has no entries
        return CardTemplate.objects.filter(card_type=chosen_type).order_by("?").first()
    return cards.order_by("?").first()


def generate_card_image(card_template):
    # If a custom image was uploaded in the admin panel, use it directly
    if card_template.image_base and card_template.image_base.name:
        try:
            image_path = os.path.join(
                django_settings.MEDIA_ROOT, card_template.image_base.name
            )
            img = Image.open(image_path)
            img = img.convert("RGB")

            # Resize large images to prevent 413 Payload Too Large errors
            img.thumbnail((800, 1200), Image.Resampling.LANCZOS)

            buffer = io.BytesIO()
            img.save(buffer, format="PNG", optimize=True)
            buffer.seek(0)
            return buffer
        except (OSError, IOError, FileNotFoundError):
            pass  # Fall through to generated card

    # Fallback: generated text-based card
    width, height = 400, 600
    rarity_colors = {
        "Common": (169, 169, 169),
        "Uncommon": (34, 139, 34),
        "Rare": (30, 144, 255),
        "Epic": (138, 43, 226),
        "Legendary": (255, 215, 0),
        "Premium": (220, 20, 60),
    }

    color = rarity_colors.get(card_template.rarity, (255, 255, 255))
    img = Image.new("RGB", (width, height), color=color)
    draw = ImageDraw.Draw(img)

    try:
        font_large = ImageFont.truetype("arial.ttf", 40)
        font_small = ImageFont.truetype("arial.ttf", 20)
    except (OSError, IOError):
        font_large = ImageFont.load_default()
        font_small = ImageFont.load_default()

    draw.text((20, 20), card_template.name, fill=(0, 0, 0), font=font_large)
    draw.text((20, 80), f"OVR: {card_template.ovr}", fill=(0, 0, 0), font=font_large)
    draw.text(
        (20, 140), f"POS: {card_template.position}", fill=(0, 0, 0), font=font_small
    )
    draw.text(
        (20, 170),
        f"ATT: {card_template.attack_stat}  DEF: {card_template.defence_stat}",
        fill=(0, 0, 0),
        font=font_small,
    )
    draw.text((20, 200), f"CLUB: {card_template.club}", fill=(0, 0, 0), font=font_small)
    draw.text((20, 550), card_template.rarity.upper(), fill=(0, 0, 0), font=font_large)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


async def catch_card(user_id, card_template):
    user, created = await DiscordUser.objects.aget_or_create(discord_id=user_id)
    user_card = await UserCard.objects.acreate(owner=user, template=card_template)
    user.cards_collected += 1
    await user.asave()
    return user_card


async def clear_card_from_lineups(user_card_id: int):
    """
    Find all Lineup records that reference this specific UserCard ID
    and set those slots to NULL.
    """
    from django.db.models import Q

    from .models import Lineup

    # Search across all 14 slots + 3 subs
    query = Q(gk_id=user_card_id) | Q(df1_id=user_card_id) | Q(df2_id=user_card_id) | \
            Q(df3_id=user_card_id) | Q(df4_id=user_card_id) | Q(df5_id=user_card_id) | \
            Q(md1_id=user_card_id) | Q(md2_id=user_card_id) | Q(md3_id=user_card_id) | \
            Q(md4_id=user_card_id) | Q(md5_id=user_card_id) | Q(at1_id=user_card_id) | \
            Q(at2_id=user_card_id) | Q(at3_id=user_card_id) | Q(sub1_id=user_card_id) | \
            Q(sub2_id=user_card_id) | Q(sub3_id=user_card_id)

    @sync_to_async
    def clear():
        lineups = Lineup.objects.filter(query)
        for lineup in lineups:
            if lineup.gk_id == user_card_id: lineup.gk = None
            if lineup.df1_id == user_card_id: lineup.df1 = None
            if lineup.df2_id == user_card_id: lineup.df2 = None
            if lineup.df3_id == user_card_id: lineup.df3 = None
            if lineup.df4_id == user_card_id: lineup.df4 = None
            if lineup.df5_id == user_card_id: lineup.df5 = None
            if lineup.md1_id == user_card_id: lineup.md1 = None
            if lineup.md2_id == user_card_id: lineup.md2 = None
            if lineup.md3_id == user_card_id: lineup.md3 = None
            if lineup.md4_id == user_card_id: lineup.md4 = None
            if lineup.md5_id == user_card_id: lineup.md5 = None
            if lineup.at1_id == user_card_id: lineup.at1 = None
            if lineup.at2_id == user_card_id: lineup.at2 = None
            if lineup.at3_id == user_card_id: lineup.at3 = None
            if lineup.sub1_id == user_card_id: lineup.sub1 = None
            if lineup.sub2_id == user_card_id: lineup.sub2 = None
            if lineup.sub3_id == user_card_id: lineup.sub3 = None
            lineup.save()

    await clear()
