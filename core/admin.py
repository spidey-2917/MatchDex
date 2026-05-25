from django.contrib import admin
from django.utils.html import format_html

from .models import (
    Blacklist,
    CardTemplate,
    CommandLog,
    DiscordUser,
    FavouriteCard,
    Lineup,
    Logo,
    PremiumRole,
    PromoCode,
    PromoCodeRedemption,
    RateConfig,
    SBC,
    SBCRequirement,
    ServerSettings,
    SpawnRate,
    Trade,
    TradeItem,
    UserCard,
    UserLogo,
    Pack,
    UserPack,
)


# ── Rate Configuration with Inline Rates ────────────────────


class SpawnRateInline(admin.TabularInline):
    model = SpawnRate
    extra = 0
    fields = ("rarity", "min_ovr", "max_ovr", "weight", "get_percentage")
    readonly_fields = ("get_percentage",)

    def get_percentage(self, obj):
        if obj.pk is None:
            return "—"
        return obj.percentage

    get_percentage.short_description = "Effective %"


@admin.register(RateConfig)
class RateConfigAdmin(admin.ModelAdmin):
    list_display = ("category", "mode", "rates_summary")
    inlines = [SpawnRateInline]

    def rates_summary(self, obj):
        return obj.get_rates_summary()

    rates_summary.short_description = "Current Rates"

    def save_model(self, request, obj, form, change):
        """Auto-populate default rates when creating a new config or switching modes."""
        super().save_model(request, obj, form, change)

        # Check if mode changed or if there are no rates yet
        existing_rates = obj.rates.count()
        mode_changed = change and "mode" in form.changed_data

        if mode_changed:
            # Wipe old rates when switching modes
            obj.rates.all().delete()
            existing_rates = 0

        if existing_rates == 0:
            self._populate_defaults(obj)

    def _populate_defaults(self, config):
        """Create default rate entries based on the mode."""
        from core.settings import settings as bot_settings

        if config.mode == "RARITY":
            for rarity, weight in bot_settings.rarity_weights.items():
                SpawnRate.objects.create(
                    config=config, rarity=rarity, weight=weight
                )
        else:
            # OVR mode defaults
            if config.category == "PACK_PREMIUM":
                ovr_defaults = [
                    (86, 88, 40.0),
                    (89, 91, 30.0),
                    (91, 93, 20.0),
                    (94, 99, 10.0),
                ]
            else:
                ovr_defaults = [
                    (80, 84, 30.0),
                    (85, 87, 50.0),
                    (88, 90, 15.0),
                    (91, 99, 5.0),
                ]
            for min_ovr, max_ovr, weight in ovr_defaults:
                SpawnRate.objects.create(
                    config=config,
                    min_ovr=min_ovr,
                    max_ovr=max_ovr,
                    weight=weight,
                )


# ── Standard Model Admins ───────────────────────────────────


@admin.register(DiscordUser)
class DiscordUserAdmin(admin.ModelAdmin):
    list_display = (
        "discord_id",
        "username",
        "points",
        "wins",
        "losses",
        "cards_collected",
        "is_premium",
        "is_admin",
        "is_inventory_private",
    )
    search_fields = ("discord_id", "username")
    readonly_fields = (
        "get_trade_count",
        "get_catch_count",
        "get_pack_opens",
        "get_bet_count",
        "get_activity_summary",
    )
    fieldsets = (
        (None, {
            "fields": (
                "discord_id", "username", "points",
                "wins", "losses", "draws", "cards_collected",
                "is_premium", "is_booster", "is_admin", "is_inventory_private",
            )
        }),
        ("Cooldowns", {
            "fields": (
                "last_pack_daily", "last_pack_weekly",
                "last_pack_event", "last_pack_premium", "last_pack_booster",
            ),
            "classes": ("collapse",),
        }),
        ("Activity Stats (read-only)", {
            "fields": (
                "get_activity_summary",
                "get_trade_count", "get_catch_count",
                "get_pack_opens", "get_bet_count",
            ),
        }),
    )

    def get_trade_count(self, obj):
        from django.db.models import Q
        count = Trade.objects.filter(
            Q(initiator=obj) | Q(receiver=obj),
            status="COMPLETED"
        ).count()
        return count
    get_trade_count.short_description = "Completed Trades"

    def get_catch_count(self, obj):
        return UserCard.objects.filter(owner=obj, traded_by__isnull=True).count()
    get_catch_count.short_description = "Cards Caught (not traded)"

    def get_pack_opens(self, obj):
        pack_commands = ["pack_daily", "pack_weekly", "pack_event", "pack_premium", "pack_booster"]
        count = CommandLog.objects.filter(
            user_id=obj.discord_id,
            command_name__in=pack_commands,
        ).count()
        return count
    get_pack_opens.short_description = "Packs Opened"

    def get_bet_count(self, obj):
        count = CommandLog.objects.filter(
            user_id=obj.discord_id,
            command_name__in=["wager challenge", "wager"],
        ).count()
        return count
    get_bet_count.short_description = "Wagers/Bets"

    def get_activity_summary(self, obj):
        trades = self.get_trade_count(obj)
        catches = self.get_catch_count(obj)
        packs = self.get_pack_opens(obj)
        bets = self.get_bet_count(obj)
        return format_html(
            "<strong>Trades:</strong> {} | <strong>Catches:</strong> {} | "
            "<strong>Packs:</strong> {} | <strong>Bets:</strong> {}",
            trades, catches, packs, bets
        )
    get_activity_summary.short_description = "Quick Summary"


@admin.register(CardTemplate)
class CardTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "ovr", "rarity", "position", "card_type", "event_name")
    list_filter = ("rarity", "card_type", "position", "event_name")
    search_fields = ("name", "club", "event_name")
    readonly_fields = ()


@admin.register(UserCard)
class UserCardAdmin(admin.ModelAdmin):
    list_display = ("owner", "template", "caught_at")
    list_filter = ("caught_at",)
    search_fields = ("owner__username", "template__name")


@admin.register(FavouriteCard)
class FavouriteCardAdmin(admin.ModelAdmin):
    list_display = ("owner", "card", "created_at")
    search_fields = ("owner__username",)


@admin.register(Logo)
class LogoAdmin(admin.ModelAdmin):
    list_display = ("name", "rarity", "bonus")


@admin.register(UserLogo)
class UserLogoAdmin(admin.ModelAdmin):
    list_display = ("owner", "logo")


@admin.register(Lineup)
class LineupAdmin(admin.ModelAdmin):
    list_display = ("owner", "name", "formation", "is_active")


@admin.register(PromoCode)
class PromoCodeAdmin(admin.ModelAdmin):
    list_display = ("code", "reward_type", "uses", "max_uses", "expires_at")
    autocomplete_fields = ["reward_card"]


@admin.register(PromoCodeRedemption)
class PromoCodeRedemptionAdmin(admin.ModelAdmin):
    list_display = ("user", "promo_code", "redeemed_at")
    list_filter = ("redeemed_at",)
    search_fields = ("user__username", "promo_code__code")


@admin.register(ServerSettings)
class ServerSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "guild_id",
        "spawn_channel_id",
        "catch_log_channel_id",
        "command_log_channel_id",
    )


@admin.register(Blacklist)
class BlacklistAdmin(admin.ModelAdmin):
    list_display = ("type", "target_id", "reason", "created_at")
    list_filter = ("type", "created_at")
    search_fields = ("target_id", "reason")


@admin.register(CommandLog)
class CommandLogAdmin(admin.ModelAdmin):
    list_display = ("user_id", "command_name", "guild_id", "timestamp")
    list_filter = ("command_name", "timestamp")
    search_fields = ("user_id", "guild_id")


class SBCRequirementInline(admin.TabularInline):
    model = SBCRequirement
    extra = 1
    autocomplete_fields = ["specific_template"]


@admin.register(SBC)
class SBCAdmin(admin.ModelAdmin):
    list_display = ("name", "reward_card", "is_active", "end_date")
    list_filter = ("is_active", "end_date")
    search_fields = ("name", "reward_card__name")
    autocomplete_fields = ["reward_card"]
    inlines = [SBCRequirementInline]


@admin.register(PremiumRole)
class PremiumRoleAdmin(admin.ModelAdmin):
    list_display = ("role_id", "label", "added_at")
    search_fields = ("role_id", "label")


@admin.register(Pack)
class PackAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "cooldown_days", "is_premium_only")
    search_fields = ("name", "code")


@admin.register(UserPack)
class UserPackAdmin(admin.ModelAdmin):
    list_display = ("user", "pack", "stash_count", "last_opened_at")
    search_fields = ("user__username", "user__discord_id", "pack__name")
    list_filter = ("pack",)
