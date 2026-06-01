import os
import sys
import django

sys.path.insert(0, os.getcwd())
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "matchdex_web.settings")
django.setup()

from core.utils import get_drop_config

mode, weights = get_drop_config("PACK_PREMIUM")
print(f"MODE: {mode}")
print("WEIGHTS:")
print(weights)
