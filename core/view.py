import os
from django.http import HttpResponse, JsonResponse

def health_check(request):
    if request.method in ['GET', 'HEAD']:
        return HttpResponse(status=200)
    return HttpResponse(status=405)

def check_env(request):
    env_data = {
        "SERVICE_PORT": os.getenv("SERVICE_PORT", "Not Set"),
        "HAS_JWT_SECRET": bool(os.getenv("INTERNAL_JWT_SECRET")),
        "MESSAGE": "Environment variables loaded successfully!"
    }
    return JsonResponse(env_data)