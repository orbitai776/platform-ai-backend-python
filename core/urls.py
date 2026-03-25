from django.urls import path
from . import views

urlpatterns = [
    path('health', views.health_check),
    path('env-check', views.check_env),
]