import os
import sys
import django

sys.path.insert(0, os.getcwd())
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "matchdex_web.settings")
django.setup()

from core.models import RateConfig, SpawnRate

config = RateConfig.objects.filter(category="PACK_PREMIUM").first()
if config:
    # Clear existing rates
    config.rates.all().delete()
    print("Deleted old rates.")
else:
    config = RateConfig.objects.create(category="PACK_PREMIUM", mode="OVR")
    print("Created PACK_PREMIUM config.")

ovr_defaults = [
    (86, 87, 50.0),
    (87, 90, 30.0),
    (91, 92, 15.0),
    (93, 94, 5.0),
]

for min_ovr, max_ovr, weight in ovr_defaults:
    SpawnRate.objects.create(
        config=config,
        min_ovr=min_ovr,
        max_ovr=max_ovr,
        weight=weight,
    )
print("Populated new rates.")
