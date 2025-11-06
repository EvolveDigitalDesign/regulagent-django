from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Create a superuser with a specified email and password'

    def handle(self, *args, **options):
        User = get_user_model()

        username = 'ruben'
        email = 'ruben@rurotech.com'
        password = 'password'

        if not User.objects.filter(username=username).exists():
            user = User.objects.create_superuser(username=username, email=email, password=password)
            print("Superuser created with specified password.")
        else:
            user = User.objects.get(username=username)
            user.set_password(password)
            user.save()
            print("Password updated for existing superuser.")
