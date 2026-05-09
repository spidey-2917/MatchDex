from django.contrib import admin

from .models import (
    Blacklist,
    CardTemplate,
    CommandLog,
    DiscordUser,
    FavouriteCard,
    Lineup,
    Logo,
    PromoCode,
    PromoCodeRedemption,
    RateConfig,
    ServerSettings,
    UserCard,
    UserLogo,
    DropRate,
)


@admin.register(RateConfig)
class RateConfigAdmin(admin.ModelAdmin):
    list_display = ("category", "mode")


@admin.register(DropRate)
class DropRateAdmin(admin.ModelAdmin):
    list_display = ("category", "mode", "get_value", "weight", "get_percentage")
    list_filter = ("category", "mode")

    def get_value(self, obj):
        if obj.mode == "RARITY":
            return obj.rarity
        return f"OVR {obj.min_ovr}-{obj.max_ovr}"

    get_value.short_description = "Value"

    def get_percentage(self, obj):
        from django.db.models import Sum
        total = DropRate.objects.filter(
            category=obj.category, mode=obj.mode
        ).aggregate(Sum("weight"))["weight__sum"]
        if total:
            return f"{(obj.weight / total) * 100:.1f}%"
        return "0%"

    get_percentage.short_description = "Percentage"


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
