import uuid

from django.db import models
from django.db.models.signals import pre_save
from django.dispatch import receiver


def generate_short_id():
    return "TEMP"


class DiscordUser(models.Model):
    discord_id = models.BigIntegerField(unique=True, primary_key=True)
    username = models.CharField(max_length=100, blank=True, null=True)
    points = models.IntegerField(default=0)
    wins = models.IntegerField(default=0)
    losses = models.IntegerField(default=0)
    draws = models.IntegerField(default=0)
    cards_collected = models.IntegerField(default=0)
    is_premium = models.BooleanField(default=False)
    is_booster = models.BooleanField(default=False)
    is_admin = models.BooleanField(default=False)
    joined_at = models.DateTimeField(auto_now_add=True)

    last_pack_daily = models.DateTimeField(null=True, blank=True)
    last_pack_weekly = models.DateTimeField(null=True, blank=True)
    last_pack_event = models.DateTimeField(null=True, blank=True)
    last_pack_premium = models.DateTimeField(null=True, blank=True)
    last_pack_booster = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.username} ({self.discord_id})"


class CardTemplate(models.Model):
    CARD_TYPES = [
        ("BASE", "Base Card"),
        ("ICON", "Icon Card"),
        ("EVENT", "Event Card"),
        ("PREMIUM", "Premium Card"),
    ]

    POSITIONS = [
        ("LW", "Left Wing"),
        ("ST", "Striker"),
        ("RW", "Right Wing"),
        ("CAM", "Attacking Midfielder"),
        ("CM", "Central Midfielder"),
        ("CDM", "Central Defensive Midfielder"),
        ("LB", "Left Back"),
        ("CB", "Center Back"),
        ("RB", "Right Back"),
        ("GK", "Goalkeeper"),
    ]

    RARITIES = [
        ("Common", "Common"),
        ("Uncommon", "Uncommon"),
        ("Rare", "Rare"),
        ("Epic", "Epic"),
        ("Legendary", "Legendary"),
        ("Premium", "Premium"),
    ]

    name = models.CharField(max_length=100)
    position = models.CharField(max_length=5, choices=POSITIONS)
    attack_stat = models.IntegerField()
    defence_stat = models.IntegerField()
    ovr = models.IntegerField(blank=True, null=True)
    rarity = models.CharField(max_length=20, choices=RARITIES, blank=True)
    event_name = models.CharField(max_length=100, default="Base")
    club = models.CharField(max_length=100)
    card_type = models.CharField(max_length=10, choices=CARD_TYPES, default="BASE")
    image_base = models.ImageField(upload_to="card_templates/", blank=True, null=True)

    @property
    def display_name(self):
        if self.event_name and self.event_name.lower() != "base":
            return f"{self.name} ({self.event_name})"
        return self.name

    def __str__(self):
        return f"{self.name} - {self.ovr} {self.rarity} ({self.card_type})"


@receiver(pre_save, sender=CardTemplate)
def update_ovr_and_rarity(sender, instance, **kwargs):
    # Only auto-calculate OVR if not provided or zero
    if not instance.ovr:
        instance.ovr = max(instance.attack_stat, instance.defence_stat)


class UserCard(models.Model):
    card_id = models.CharField(
        max_length=8, unique=True, default=generate_short_id, editable=False
    )
    owner = models.ForeignKey(
        DiscordUser, on_delete=models.CASCADE, related_name="inventory"
    )
    template = models.ForeignKey(CardTemplate, on_delete=models.CASCADE)
    caught_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        if not self.card_id:
            self.card_id = "TEMP"
        super().save(*args, **kwargs)
        if is_new and (not self.card_id or self.card_id == "TEMP"):
            from core.utils import to_base36
            self.card_id = to_base36(self.id)
            super().save(update_fields=["card_id"])

    def __str__(self):
        return f"[{self.card_id}] {self.owner.username}'s {self.template.name}"


class FavouriteCard(models.Model):
    owner = models.ForeignKey(
        DiscordUser, on_delete=models.CASCADE, related_name="favourites"
    )
    card = models.ForeignKey(
        UserCard, on_delete=models.CASCADE, related_name="favourited_by"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("owner", "card")

    def __str__(self):
        return f"{self.owner.username} ❤️ {self.card.template.name}"


class Logo(models.Model):
    RARITIES = [("RARE", "Rare"), ("EPIC", "Epic"), ("LEGENDARY", "Legendary")]
    name = models.CharField(max_length=100)
    rarity = models.CharField(max_length=10, choices=RARITIES)
    bonus = models.IntegerField(default=1)  # +1, +2, +3

    def __str__(self):
        return f"{self.name} ({self.rarity} +{self.bonus})"


class UserLogo(models.Model):
    owner = models.OneToOneField(
        DiscordUser, on_delete=models.CASCADE, related_name="equipped_logo"
    )
    logo = models.ForeignKey(Logo, on_delete=models.CASCADE)


class Lineup(models.Model):
    owner = models.ForeignKey(DiscordUser, on_delete=models.CASCADE)
    name = models.CharField(max_length=50, default="Lineup 1")
    is_active = models.BooleanField(default=False)
    formation = models.CharField(
        max_length=20, default="433"
    )  # e.g. 433_attack, 442_diamond

    # Slots for 11 players
    gk = models.ForeignKey(
        UserCard,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="slot_gk",
    )
    df1 = models.ForeignKey(
        UserCard,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="slot_df1",
    )
    df2 = models.ForeignKey(
        UserCard,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="slot_df2",
    )
    df3 = models.ForeignKey(
        UserCard,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="slot_df3",
    )
    df4 = models.ForeignKey(
        UserCard,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="slot_df4",
    )
    df5 = models.ForeignKey(
        UserCard,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="slot_df5",
    )
    md1 = models.ForeignKey(
        UserCard,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="slot_md1",
    )
    md2 = models.ForeignKey(
        UserCard,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="slot_md2",
    )
    md3 = models.ForeignKey(
        UserCard,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="slot_md3",
    )
    md4 = models.ForeignKey(
        UserCard,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="slot_md4",
    )
    md5 = models.ForeignKey(
        UserCard,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="slot_md5",
    )
    at1 = models.ForeignKey(
        UserCard,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="slot_at1",
    )
    at2 = models.ForeignKey(
        UserCard,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="slot_at2",
    )
    at3 = models.ForeignKey(
        UserCard,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="slot_at3",
    )

    # 3 substitute slots
    sub1 = models.ForeignKey(
        UserCard,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="slot_sub1",
    )
    sub2 = models.ForeignKey(
        UserCard,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="slot_sub2",
    )
    sub3 = models.ForeignKey(
        UserCard,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="slot_sub3",
    )


class PromoCode(models.Model):
    REWARD_TYPES = [
        ("POINTS", "Points"),
        ("PACK_DAILY", "Daily Pack (Base)"),
        ("PACK_WEEKLY", "Weekly Pack (Icon)"),
        ("PACK_EVENT", "Event Pack (Event)"),
        ("PACK_PREMIUM", "Premium Pack (Icon/Event)"),
        ("CARD", "Specific Card"),
    ]

    code = models.CharField(max_length=50, unique=True)
    reward_type = models.CharField(max_length=20, choices=REWARD_TYPES)
    reward_points = models.IntegerField(default=0, help_text="Amount of points if reward type is POINTS")
    reward_card = models.ForeignKey(
        CardTemplate, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        help_text="Select a specific card if reward type is Specific Card"
    )
    uses = models.IntegerField(default=0)
    max_uses = models.IntegerField(default=100)
    expires_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.code


class PromoCodeRedemption(models.Model):
    """Tracks which users have redeemed which promo codes — one redemption per user per code."""
    user = models.ForeignKey(DiscordUser, on_delete=models.CASCADE, related_name="promo_redemptions")
    promo_code = models.ForeignKey(PromoCode, on_delete=models.CASCADE, related_name="redemptions")
    redeemed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "promo_code")

    def __str__(self):
        return f"{self.user.username} redeemed {self.promo_code.code}"


class ServerSettings(models.Model):
    guild_id = models.BigIntegerField(unique=True)
    spawn_channel_id = models.BigIntegerField(null=True, blank=True)
    catch_log_channel_id = models.BigIntegerField(null=True, blank=True)
    command_log_channel_id = models.BigIntegerField(null=True, blank=True)

    def __str__(self):
        return f"Server {self.guild_id}"


class Blacklist(models.Model):
    TYPES = [("USER", "User"), ("GUILD", "Guild")]
    target_id = models.BigIntegerField()
    type = models.CharField(max_length=5, choices=TYPES)
    reason = models.TextField(default="No reason provided")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("target_id", "type")

    def __str__(self):
        return f"{self.type} {self.target_id} — {self.reason}"


class CommandLog(models.Model):
    guild_id = models.BigIntegerField()
    user_id = models.BigIntegerField()
    command_name = models.CharField(max_length=100)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user_id} used /{self.command_name} in {self.guild_id}"


class Trade(models.Model):
    initiator = models.ForeignKey(
        DiscordUser, on_delete=models.CASCADE, related_name="trades_initiated"
    )
    receiver = models.ForeignKey(
        DiscordUser, on_delete=models.CASCADE, related_name="trades_received"
    )
    channel_id = models.BigIntegerField(null=True, blank=True)
    message_id = models.BigIntegerField(null=True, blank=True)
    state_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        default="PENDING",
        choices=[
            ("PENDING", "Pending"),
            ("COMPLETED", "Completed"),
            ("CANCELLED", "Cancelled"),
        ],
    )

    def __str__(self):
        return f"Trade between {self.initiator.username} and {self.receiver.username} - {self.status}"


class TradeItem(models.Model):
    trade = models.ForeignKey(Trade, on_delete=models.CASCADE, related_name="items")
    card = models.ForeignKey(UserCard, on_delete=models.CASCADE)
    sender = models.ForeignKey(
        DiscordUser, on_delete=models.CASCADE, related_name="sent_trade_items"
    )
    receiver = models.ForeignKey(
        DiscordUser, on_delete=models.CASCADE, related_name="received_trade_items"
    )

    def __str__(self):
        return f"{self.card.template.name} sent by {self.sender.username}"


class RateConfig(models.Model):
    CATEGORIES = [("SPAWN", "Spawning"), ("PACK", "Packs")]
    MODES = [("RARITY", "By Rarity"), ("OVR", "By OVR")]

    category = models.CharField(max_length=10, choices=CATEGORIES, unique=True)
    mode = models.CharField(max_length=10, choices=MODES, default="RARITY")

    def __str__(self):
        return f"{dict(self.CATEGORIES).get(self.category)} - {dict(self.MODES).get(self.mode)}"


class DropRate(models.Model):
    category = models.CharField(max_length=10, choices=RateConfig.CATEGORIES)
    mode = models.CharField(max_length=10, choices=RateConfig.MODES)

    # For Rarity mode
    rarity = models.CharField(
        max_length=20, choices=CardTemplate.RARITIES, blank=True, null=True
    )

    # For OVR mode
    min_ovr = models.IntegerField(blank=True, null=True)
    max_ovr = models.IntegerField(blank=True, null=True)

    weight = models.FloatField(default=1.0)

    class Meta:
        verbose_name = "Drop Rate"
        verbose_name_plural = "Drop Rates"

    def __str__(self):
        if self.mode == "RARITY":
            return f"[{self.category}] {self.rarity}: {self.weight}"
        else:
            return f"[{self.category}] OVR {self.min_ovr}-{self.max_ovr}: {self.weight}"
