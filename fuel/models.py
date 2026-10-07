from django.db import models
from django.db.models import Q


class FuelStation(models.Model):
    class GeocodeStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        OK = "ok", "Geocoded"
        FAILED = "failed", "Not found"

    opis_id = models.PositiveIntegerField(unique=True, help_text="OPIS Truckstop ID from the source CSV.")
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    country = models.CharField(max_length=2, default="US")
    rack_id = models.PositiveIntegerField()
    retail_price = models.DecimalField(max_digits=10, decimal_places=8)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    geocode_status = models.CharField(max_length=10, choices=GeocodeStatus.choices, default=GeocodeStatus.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["opis_id"]
        indexes = [
            models.Index(fields=["state", "city"]),
            models.Index(fields=["retail_price"]),
            models.Index(fields=["latitude", "longitude"]),
            models.Index(fields=["geocode_status"]),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(retail_price__gt=0), name="fuelstation_price_positive"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.city}, {self.state}) ${self.retail_price}"

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None
