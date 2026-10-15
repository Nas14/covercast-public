"""Codes owned by the workforce app (coverage may import these; never the reverse)."""

from django.db import models


class DataOrigin(models.TextChoices):
    OFFICIAL = "official", "رسمي"
    SIMULATED = "simulated", "محاكاة"
