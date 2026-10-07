from django.contrib import admin

from .models import FuelStation


@admin.register(FuelStation)
class FuelStationAdmin(admin.ModelAdmin):
    list_display = ("opis_id", "name", "city", "state", "retail_price", "geocode_status")
    list_filter = ("geocode_status", "country", "state")
    search_fields = ("name", "city", "address", "opis_id")
