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
    ServerSettings,
    UserCard,
    UserLogo,
)


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
    readonly_fields = ("ovr", "rarity")


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
