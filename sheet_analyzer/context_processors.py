from .models import Profile


def user_profile(request):
    if request.user.is_authenticated:
        try:
            profile, _ = Profile.objects.get_or_create(user=request.user)
            return {'user_profile': profile}
        except Exception:
            return {}
    return {}
