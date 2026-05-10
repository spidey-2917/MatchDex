from django.contrib import admin

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
    ServerSettings,
    SpawnRate,
    UserCard,
    UserLogo,
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
                    (80, 84, 30.0),
                    (85, 87, 50.0),
                    (88, 90, 15.0),
                    (91, 99, 5.0),
                ]
            else:
                ovr_defaults = [
                    (60, 69, 5.0),
                    (70, 79, 25.0),
                    (80, 84, 30.0),
                    (85, 89, 25.0),
                    (90, 94, 12.0),
                    (95, 99, 3.0),
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
        "is_premium",
        "is_admin",
    )
    search_fields = ("discord_id", "username")


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


@admin.register(PremiumRole)
class PremiumRoleAdmin(admin.ModelAdmin):
    list_display = ("role_id", "label", "added_at")
    search_fields = ("role_id", "label")
