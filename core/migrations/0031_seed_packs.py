# Generated manually
from django.db import migrations

def seed_packs(apps, schema_editor):
    Pack = apps.get_model('core', 'Pack')
    
    Pack.objects.get_or_create(
        code="weekly",
        defaults={
            "name": "Weekly Pack",
            "cooldown_days": 7.0,
            "card_type_filter": "ICON",
            "is_premium_only": False,
        }
    )
    Pack.objects.get_or_create(
        code="premium",
        defaults={
            "name": "Premium Pack",
            "cooldown_days": 2.0,
            "card_type_filter": "ANY",
            "is_premium_only": True,
            "rate_config_category": "PACK_PREMIUM",
        }
    )
    Pack.objects.get_or_create(
        code="event",
        defaults={
            "name": "Event Pack",
            "cooldown_days": 7.0,
            "card_type_filter": "EVENT",
            "is_premium_only": False,
        }
    )

def revert_packs(apps, schema_editor):
    Pack = apps.get_model('core', 'Pack')
    Pack.objects.filter(code__in=["weekly", "premium", "event"]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0030_simseason_simseasonplayer_tradeobjectivelog'),
    ]
    operations = [
        migrations.RunPython(seed_packs, revert_packs)
    ]
