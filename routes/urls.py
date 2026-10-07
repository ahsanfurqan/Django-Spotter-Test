from django.urls import path

from .views import OptimizeRouteView

urlpatterns = [
    path("optimize/", OptimizeRouteView.as_view(), name="optimize-route"),
]
