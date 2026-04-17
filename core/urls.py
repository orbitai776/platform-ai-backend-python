from django.urls import include, path
from . import views

urlpatterns = [
    path('health', views.health_check),
    path('env-check', views.check_env),

    # Billing & Payment APIs
    path('', include('billing.urls')),
]